"""The library service ties the on-disk vault (source of truth) to the SQLite
index (rebuildable cache). Reads go through the index; writes go to the vault
first, then the index — so a re-scan of disk reproduces the exact same state.
The service also composes the flat wire DTO from the layered documents.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import threading
import time
import uuid
import zipfile
from collections import Counter
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from . import comicinfo, fields, media
from .index import LibraryIndex
from .models import (
    AuthorWork, BrowseGroup, ChapterRow, DraftIn, Source, TitleDoc, TitleOut, UserPatch,
)
from .vault import FRAME_KINDS, Vault, safe_id
from .versions import cache_key, chapter_version, cover_version


log = logging.getLogger("longbox.library")


def _chapter_num_key(num: str) -> tuple:
    """Smart chapter order: numeric ascending ("2" < "10", 5.5 between 5 and 6),
    non-numeric ("Extra") after, alphabetically."""
    m = re.match(r"\s*(\d+(?:[.,]\d+)?)", num)
    if m:
        return (0, float(m.group(1).replace(",", ".")))
    return (1, num.strip().casefold())


def _missing_numbers(nums: list[str], min_rows: int, numeric: float,
                     density: float) -> list[int]:
    """The integers absent from a run of labels — or nothing at all, if this is
    not provably a run. `_chapter_num_key` already owns what "a number in a
    label" means; this reads the same leading value and refuses everything else.

    Fractions never count as a gap (10.5 is an extra, not a missing 11) but they
    do count as entries held: a scope that is half extras is still a sequence."""
    if len(nums) < min_rows:
        return []
    values = [key[1] for key in map(_chapter_num_key, nums) if key[0] == 0]
    if len(values) < numeric * len(nums):
        return []
    whole = {int(v) for v in values if float(v).is_integer()}
    if not whole:
        return []
    low, high = min(whole), max(whole)
    span = high - low + 1
    if span > min_rows * 20 or len(set(values)) < density * span:
        return []  # arbitrary labels that happen to be numbers
    return sorted(set(range(low, high + 1)) - whole)


# How a long pass reports itself: (titles done, titles in the library).
Progress = Callable[[int, int], None] | None


def _stopped(stop: threading.Event | None, seen: int, total: int) -> bool:
    """Whether a pass was cut short. A report that covered part of the library
    must say so — "nothing wrong" is a very different claim from "nothing wrong
    in the first two hundred"."""
    return bool(stop is not None and stop.is_set() and seen < total)


# ---- from what the pass found to what a person is told --------------------
#
# The pass speaks in mechanisms (a stale digest, an orphaned sidecar, a `.tmp`);
# a person needs three answers and something to do about each. Keeping the
# translation HERE, as a pure function over the pass's output, is what lets the
# mechanism keep its own vocabulary and its own tests.

# What each mechanism means for the file a person owns.
BROKEN_WHAT = {
    "missing": "the file is gone",
    "unreadable": "the file will not open",
    "corrupt": "the file changed since it was stored",
    "size": "the file is a different size than recorded",
}
# Deliberately NOT reported as problems: a chapter with no record still reads,
# and content that never had a checksum is a fact about this vault's age, not
# damage. Both are counted, neither is a row in a list of things gone wrong.
LEFTOVER_KINDS = ("stray", "orphan")

# A list of thousands is not a list. Rows are capped; the totals never are, and
# every list says how much of itself is showing.
ROW_CAP = 200
# When this share of the media the library EXPECTS to hold cannot be read, the
# count is not the answer — a whole library failing at once is a folder that
# moved, not damage. The floor keeps a two-chapter vault from being told its
# storage is unreachable because both of its files happen to be broken.
SYSTEMIC = 0.9
SYSTEMIC_FLOOR = 3


def _rows_for_broken(findings: list[dict]) -> tuple[list[dict], int, int]:
    """Broken chapters as (title x problem), biggest first.

    The row unit is not a chapter: forty broken chapters in one title are one
    problem with that title, and forty rows would hide the other sixteen titles
    under it. A title with exactly one names the entry instead of counting it."""
    by: dict[tuple[str, str], dict] = {}
    for f in findings:
        what = BROKEN_WHAT.get(f["kind"])
        if what is None:
            continue
        key = (f["titleId"], what)
        row = by.get(key)
        if row is None:
            by[key] = {"titleId": f["titleId"], "title": f["title"], "what": what, "count": 1,
                       "num": f.get("num", ""), "lang": f.get("lang", ""),
                       "group": f.get("group", "")}
        else:
            row["count"] += 1
            row["num"] = row["lang"] = row["group"] = ""  # no longer one entry
    rows = sorted(by.values(), key=lambda r: -r["count"])
    total = sum(r["count"] for r in rows)
    return rows[:ROW_CAP], total, len({r["titleId"] for r in rows})


def _leftovers(findings: list[dict]) -> dict:
    rows = [f for f in findings if f["kind"] in LEFTOVER_KINDS]
    rows.sort(key=lambda r: -int(r.get("bytes") or 0))
    titles = sorted({r["titleId"] for r in rows})
    return {
        "files": len(rows),
        "bytes": sum(int(r.get("bytes") or 0) for r in rows),
        "titles": len(titles),
        # Every affected title, uncapped — ids are small, and this is the SCOPE
        # the sweep works in. Without it deleting two files means walking the
        # whole library to rediscover where they were.
        "titleIds": titles,
        "rows": [{"titleId": r["titleId"], "title": r["title"],
                  "name": r.get("name", ""), "bytes": int(r.get("bytes") or 0)}
                 for r in rows[:ROW_CAP]],
    }


def _span_of(missing: list[int]) -> str:
    return f"missing {', '.join(str(n) for n in missing)}" if len(missing) <= 4         else f"missing {len(missing)}"


def compose(report: dict, dupes: dict, gaps: list[dict], total: int) -> dict:
    """The three answers, from one pass over the library."""
    findings = report["findings"]
    broken_rows, broken_total, broken_titles = _rows_for_broken(findings)
    left = _leftovers(findings)
    gap_rows = sorted(gaps, key=lambda g: -len(g["missing"]))
    with_digest = report.get("withDigest", 0)
    # `checked` counts chapters whose file was THERE to check, so a vault whose
    # media has all vanished checks nothing at all: the denominator has to be
    # what the library expected to find, not what it managed to open.
    expected = report["checked"] + sum(1 for f in findings if f["kind"] == "missing")
    return {
        "checked": report["checked"], "hashed": report["hashed"], "deep": report["deep"],
        "stopped": report["stopped"], "total": total, "withDigest": with_digest,
        # a whole library failing at once is a folder that moved, and three
        # thousand identical rows would bury that
        "systemic": bool(expected >= SYSTEMIC_FLOOR and broken_total >= expected * SYSTEMIC),
        "expected": expected,
        "broken": {"total": broken_total, "titles": broken_titles, "rows": broken_rows},
        "leftovers": left,
        "duplicates": {"sets": len(dupes["groups"]), "bytes": dupes["wasted"],
                       "groups": dupes["groups"][:ROW_CAP]},
        "gaps": {"titles": len(gap_rows),
                 "rows": [{**g, "what": _span_of(g["missing"])} for g in gap_rows[:ROW_CAP]]},
    }


def _mb(n: int) -> str:
    if n >= 1 << 30:
        return f"{n / (1 << 30):.1f} GB"
    if n >= 1 << 20:
        return f"{n / (1 << 20):.1f} MB"
    return f"{max(1, n // 1024)} KB"


def _check_outcome(out: dict) -> str:
    """One sentence for the log: what the check came to, in the same words the
    report uses on screen."""
    bits: list[str] = []
    if out["broken"]["total"]:
        bits.append(f"{out['broken']['total']} chapter(s) cannot be read")
    wasted = out["leftovers"]["bytes"] + out["duplicates"]["bytes"]
    if wasted:
        bits.append(f"{_mb(wasted)} wasted")
    if out["gaps"]["titles"]:
        bits.append(f"{out['gaps']['titles']} title(s) with gaps")
    if not bits:
        return f"nothing wrong with {out['checked']} chapter(s)"
    return " · ".join(bits)


def _size_of(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _retyped(value: object, to_type: str, sep: str) -> str | list[str] | None:
    """One stored value in the shape the field now says it has, or None when it
    already is. A list folds into text on the separator; text splits back on it
    — and a separator that is only punctuation still reads as one, so the parts
    are stripped rather than left with the spacing glued on."""
    # A title that does not hold this field has nothing to convert. Without
    # this, `str(None).split(...)` makes the literal string "None" a value and
    # a retype writes it into every title in the library.
    if value is None or value == "" or value == []:
        return None
    if to_type == "list":
        if isinstance(value, list):
            return None
        parts = [p.strip() for p in str(value).split(sep.strip() or sep)]
        return [p for p in parts if p]
    # every other type holds a single value; a list under one of them is a
    # leftover from what the field used to be
    if isinstance(value, list):
        return sep.join(str(v) for v in value if v)
    return None


def _finding(title_id: str, doc: TitleDoc, ch: ChapterRow, kind: str, detail: str) -> dict:
    """One problem, named by the thing a human would go looking for: the work
    and the entry, never the path on disk."""
    return {"titleId": title_id, "title": doc.meta.title, "chapterId": ch.id,
            "num": ch.num, "lang": ch.lang, "group": ch.group,
            "kind": kind, "detail": detail}


class Library:
    def __init__(self, root: Path, *, defer_sync: bool = False) -> None:
        self.vault = Vault(root)
        # the registry is per-library: point it at THIS vault's definitions
        fields.set_custom(self.vault.custom_fields())
        self.index = LibraryIndex(root / "index.db")
        # two concurrent creates may derive the same fresh id — serialize the
        # pick-unique-id → first-commit step
        self._create_lock = threading.Lock()
        # ONE sweep at a time, whoever asked for it (startup or Settings), and a
        # flag the sweep polls so close() can stop it instead of letting it write
        # into an index that is about to be closed
        self._normalize_lock = threading.Lock()
        self._closing = threading.Event()
        self._sidecar_cache: dict[str, tuple[int, dict[str, dict]]] = {}
        # What the index already holds needs no disk at all, so the library is
        # SERVED FIRST and verified after: the scan below only answers "did
        # anything change on disk", and nothing has to wait for that answer.
        # (It cannot be skipped: the vault is the source of truth, and files can
        # be dropped in, edited or deleted while the app is closed — or by
        # another machine, on a shared drive.)
        self.sync_state = {"running": False, "done": 0, "total": 0, "changed": 0}
        self._sync_thread: threading.Thread | None = None
        if not defer_sync:
            self.sync()
        # The zip invariant is enforced at INGEST, so the archive sweep is a
        # one-time migration for pre-invariant (or hand-dropped) content: it
        # runs only for a vault that never had it, off the startup path (large
        # repacks are slow), and marks the vault when done. Settings re-runs it.
        self._normalize_thread: threading.Thread | None = None
        if self.vault.needs_normalize() or self.vault.needs_faststart():
            self._normalize_thread = threading.Thread(
                target=self._migrations_bg, name="lb-migrate", daemon=True)
            self._normalize_thread.start()

    def _migrations_bg(self) -> None:
        """One-time vault passes, in sequence — never in parallel: both walk the
        whole vault, and on a network share two sweeps only get in each other's
        way."""
        try:
            if self.vault.needs_normalize():
                self.normalize_archives()
            if self.vault.needs_faststart():
                self.refresh_episodes()
        except Exception:  # noqa: BLE001 — a failed pass must never take the app down
            pass

    def refresh_episodes(self) -> int:
        """Bring stored episodes up to what the app records about them, once per
        vault. Anything arriving now gets it at ingest."""
        changed = self.vault.refresh_stored_episodes(stop=self._closing)
        if not self._closing.is_set():
            self.vault.mark_faststart()
        if changed and not self._closing.is_set():
            self.sync()  # the media version moved; the index carries it
        return changed

    def normalize_archives(self, *, force: bool = False, progress: Progress = None,
                           stop: threading.Event | None = None) -> int:
        """Run the archive sweep and mark the vault as normalized. `force`
        retries archives whose conversion failed before. Returns -1 when another
        pass already holds the sweep."""
        if not self._normalize_lock.acquire(blocking=False):
            return -1
        started = time.monotonic()
        # Say how big this is BEFORE the disk work that finds out exactly: the
        # sweep has to list every shelf to know its own scope, and a pass that
        # reports nothing for the first stretch reads as a hung one — with a
        # Stop that does nothing, because there is no title boundary yet.
        # The index already knows every title, so the sweep does not go back to
        # the disk to find that out. Empty means it has not been filled yet
        # (the startup migration), and the vault lists the shelves itself.
        ids = [tid for tid, _doc, _cover in self.index.all_docs()] or None
        if progress is not None:
            progress(0, len(ids or []))
        try:
            changed = self.vault.normalize_chapter_archives(
                force=force, progress=progress, ids=ids, stop=stop or self._closing)
            if not self._closing.is_set():
                self.vault.mark_normalized()
            if changed and not self._closing.is_set():
                # sync, NOT rescan: a rebuild is DELETE + INSERT over the whole
                # table, and this runs on a background thread while the app is
                # being used — it would drop a title committed a moment ago
                self.sync()
            self._record("convert archives", started,
                         f"{changed} converted" if changed else "everything already zip")
            return changed
        finally:
            self._normalize_lock.release()

    # ---- the record of what has been done ------------------------------------
    #
    # Every operation that touches the vault leaves a line: when it ran, how
    # long it took, and what came of it in one sentence. Without it a report is
    # a thing you have to re-earn every time you open the panel, and "has this
    # library ever been checked" has no answer at all.

    def _record(self, op: str, started: float, outcome: str, *, stopped: bool = False,
                last_check: dict | None = None) -> None:
        self.vault.write_health(
            entry={"op": op, "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   "seconds": round(time.monotonic() - started, 1),
                   "outcome": outcome, "stopped": stopped},
            last_check=last_check)

    def health(self) -> dict:
        """What has been done to this library, newest first, and the last report."""
        return self.vault.health()

    # ---- the vault passes ---------------------------------------------------
    #
    # Four questions that all walk the whole library, so all four report the
    # same way and stop the same way. A pass over a network vault takes as long
    # as it takes; what it must never do is take that long in silence.

    def _walk(self, progress: Progress = None, stop: threading.Event | None = None):
        """Every title, counted out loud and interruptible at each one.

        Titles, not chapters, are the unit: the total is known before the walk
        starts (the index knows how many there are) and a caller that needed
        finer progress would be paying a chapter count it does not have yet."""
        docs = self.index.all_docs()
        total = len(docs)
        if progress is not None:
            progress(0, total)
        for done, row in enumerate(docs, 1):
            if stop is not None and stop.is_set():
                return
            yield row
            if progress is not None:
                progress(done, total)

    def refresh_comicinfo(self, progress: Progress = None,
                          stop: threading.Event | None = None) -> dict:
        """Bring every archive's ComicInfo.xml in line with the library.

        Ingest and page edits keep it current for free — they rewrite the file
        anyway. A metadata edit does NOT: rewriting gigabytes of archives
        because a tag changed is not a trade the app gets to make quietly. So
        this is the explicit half, and it rewrites only what actually differs."""
        started = time.monotonic()
        changed = 0
        # Counted in CHAPTERS, not titles: this rewrites archives, so one title
        # can be gigabytes and a per-title tick leaves the bar still while the
        # disk works — and leaves Stop with nowhere to take effect until the
        # whole title is done.
        # Which titles have anything to write is decided from the INDEX: it
        # carries both the document and the chapter sidecars, and a sidecar
        # records the mirror it last got. Visiting every title to find out means
        # a lock, a title.json read and a directory listing each — on a network
        # vault, minutes to discover there is nothing to do.
        rows = [(tid, doc, side) for tid, doc, side, _c in self.index.query()
                if any(not (side.get(safe_id(ch.id)) or {}).get("comicinfo")
                       or (side.get(safe_id(ch.id)) or {}).get("comicinfo")
                       != hashlib.sha256(comicinfo.build(doc, ch)).hexdigest()[:16]
                       for ch in doc.chapters)]
        total = sum(len(doc.chapters) for _t, doc, _s in rows)
        seen = 0
        if progress is not None:
            progress(0, total)
        for title_id, _doc, _side in rows:
            if stop is not None and stop.is_set():
                break
            with self.vault.title_lock(title_id):
                doc = self.vault.load(title_id)
                if doc is None:
                    continue
                stored = self.vault.chapter_files(title_id)
                known = self.vault.chapter_sidecars(title_id)
                for ch in doc.chapters:
                    if stop is not None and stop.is_set():
                        break
                    seen += 1
                    if progress is not None:
                        progress(seen, total)
                    path = stored.get(safe_id(ch.id))
                    if path is None:
                        continue
                    if self._mirror_comicinfo(title_id, doc, ch, path, known):
                        changed += 1
        stopped = _stopped(stop, seen, total)
        self._record("update metadata", started,
                     f"{changed} archive(s) updated" if changed else "already up to date",
                     stopped=stopped)
        return {"written": changed, "stopped": stopped}

    # ---- the revision pass --------------------------------------------------
    #
    # What the vault CANNOT answer on its own: is every chapter's file still
    # there, still readable, and still the bytes that were put in. A listing
    # trusts the index, a reader finds out by failing — this is the one place
    # that goes looking on purpose.
    #
    # Deliberately NOT run on startup, and never holding a title lock across the
    # walk: it reads gigabytes on a large vault. `deep` gates the re-hash;
    # without it the pass is structural only and costs one stat per chapter.

    def verify(self, *, deep: bool = False, backfill: bool = False,
               progress: Progress = None, stop: threading.Event | None = None) -> dict:
        """Every chapter checked against what its sidecar claims.

        `deep` re-reads each file to compare digests; `backfill` stamps one on
        content stored before digests existed. Backfilling is NOT verification
        and says so in the report — a digest first taken today proves the file
        has been stable since today, not that it is what arrived."""
        findings: list[dict] = []
        checked = hashed = seen = with_digest = 0
        for title_id, doc, _cover in self._walk(progress, stop):
            seen += 1
            sidecars = self.vault.chapter_sidecars(title_id)
            # ONE directory listing for the whole title: asking per chapter costs
            # up to ten filesystem round trips each, which is what made a pass
            # over a network vault take minutes
            stored = self.vault.chapter_files(title_id)
            stems: set[str] = set()
            for ch in doc.chapters:
                stem = safe_id(ch.id)
                stems.add(stem)
                side = sidecars.get(stem)
                path = stored.get(stem)
                if path is None:
                    # a row with no file is a NORMAL state (a chapter listed but
                    # not downloaded); only a sidecar makes it a loss
                    if side:
                        findings.append(_finding(title_id, doc, ch, "missing",
                                                 f"sidecar claims {side.get('filename') or 'a file'}"))
                    continue
                checked += 1
                if side is None:
                    findings.append(_finding(title_id, doc, ch, "noSidecar", path.name))
                    continue
                if side.get("sha256"):
                    with_digest += 1
                size = path.stat().st_size
                if int(side.get("size") or 0) != size:
                    findings.append(_finding(title_id, doc, ch, "size",
                                             f"{side.get('size')} recorded, {size} on disk"))
                if side.get("kind") != "video" and not zipfile.is_zipfile(path):
                    findings.append(_finding(title_id, doc, ch, "unreadable", path.name))
                    continue
                if not deep:
                    continue
                want = str(side.get("sha256") or "")
                if not want:
                    if backfill:
                        self._stamp_digest(title_id, ch.id, media.digest_of(path))
                        hashed += 1
                    findings.append(_finding(title_id, doc, ch,
                                             "stamped" if backfill else "noDigest", path.name))
                    continue
                if media.digest_of(path) != want:
                    findings.append(_finding(title_id, doc, ch, "corrupt",
                                             f"{size} bytes, digest does not match"))
                hashed += 1
            for stem, side in sidecars.items():
                if stem not in stems:
                    record = self.vault._chapters_dir(title_id) / f"{stem}.json"
                    findings.append({"titleId": title_id, "title": doc.meta.title,
                                     "chapterId": stem, "num": str(side.get("filename") or ""),
                                     "kind": "orphan", "name": record.name,
                                     "bytes": _size_of(record),
                                     "detail": "a record for a chapter this title no longer lists"})
            findings.extend(self._strays(title_id, doc, stems))
        return {"checked": checked, "hashed": hashed, "deep": deep,
                "withDigest": with_digest,
                "stopped": _stopped(stop, seen, self.index.count()),
                "findings": findings}

    def _strays(self, title_id: str, doc: TitleDoc, seen: set[str]) -> list[dict]:
        """Files in a title's chapters directory that belong to no entry.

        A `.tmp` is an interrupted write — ingest also renames a media file it
        cannot unlink to one, so these accumulate exactly where something went
        wrong. Everything else here is media whose row is gone: invisible in the
        UI, still occupying the vault."""
        d = self.vault._chapters_dir(title_id)
        out: list[dict] = []
        if not d.is_dir():
            return out
        for p in sorted(d.iterdir()):
            if not p.is_file():
                continue
            name = p.name
            if name.endswith(".tmp"):
                out.append({"titleId": title_id, "title": doc.meta.title, "chapterId": "",
                            "num": "", "lang": "", "group": "", "kind": "stray",
                            "name": name, "bytes": _size_of(p),
                            "detail": f"{name} — an interrupted write"})
                continue
            if p.suffix == ".json":
                continue  # a sidecar with no row is reported as an orphan above
            stem = name
            for kind in FRAME_KINDS:
                if stem.endswith(f".{kind}.jpg"):
                    stem = stem[: -len(f".{kind}.jpg")]
                    break
            else:
                stem = p.stem
            if stem not in seen:
                out.append({"titleId": title_id, "title": doc.meta.title, "chapterId": stem,
                            "num": "", "lang": "", "group": "", "kind": "stray",
                            "name": name, "bytes": _size_of(p),
                            "detail": f"{name} — media for an entry this title no longer lists"})
        return out

    def duplicates(self, progress: Progress = None,
                   stop: threading.Event | None = None) -> dict:
        """Chapters whose stored file is byte-for-byte the same one.

        Reads the index, not the disk: a sidecar rides its title's row, so this
        is SQL plus grouping even on a vault that lives on a network share. The
        same release filed under two languages, a re-download that landed beside
        the original, one volume added twice under different labels — none of
        them are visible any other way."""
        by_digest: dict[str, list[dict]] = {}
        rows = self.index.query()
        total = len(rows)
        seen = 0
        for title_id, doc, sidecars, _cover in rows:
            if stop is not None and stop.is_set():
                break
            seen += 1
            if progress is not None:
                progress(seen, total)
            for ch in doc.chapters:
                side = sidecars.get(safe_id(ch.id)) or {}
                digest = str(side.get("content") or "")
                if not digest:
                    continue
                by_digest.setdefault(digest, []).append(
                    {**_finding(title_id, doc, ch, "duplicate", side.get("filename") or ""),
                     "size": int(side.get("size") or 0)})
        groups = [{"sha256": d, "size": rows[0]["size"], "copies": rows}
                  for d, rows in by_digest.items() if len(rows) > 1]
        # what reclaiming would actually save, biggest first — every copy but
        # one is the waste, and a 2 GB episode twice beats twenty small chapters
        for g in groups:
            g["wasted"] = g["size"] * (len(g["copies"]) - 1)
        groups.sort(key=lambda g: g["wasted"], reverse=True)
        return {"groups": groups, "wasted": sum(g["wasted"] for g in groups),
                "stopped": _stopped(stop, seen, total)}

    # ---- numbering gaps -----------------------------------------------------
    #
    # A vault holds image sets, one-shots and things with no sequence at all, so
    # this must never GUESS that entries are chapters. Five gates, and a scope
    # that fails any of them is silent — a wrong "you are missing 8" costs more
    # trust than a right one buys.

    _GAP_MIN_ROWS = 5      # three labels are not a sequence
    _GAP_NUMERIC = 0.8     # "Prologue" among numbers is fine; the reverse is not
    _GAP_DENSITY = 0.7     # holding 1, 5, 40, 900 is arbitrary labels, not gaps

    def gaps(self, title_id: str = "", progress: Progress = None,
             stop: threading.Event | None = None) -> list[dict]:
        """Missing numbers, only where the entries provably form a sequence.

        Counted PER TRANSLATION: 1–10 in English beside 5–7 in Ukrainian is two
        sequences, not a hole in one. Nothing is ever reported beyond the
        highest number held — what the series is up to is not ours to know."""
        # One title is the title page asking about itself — no progress to
        # report and nothing to interrupt.
        if title_id:
            row = self.index.get(title_id)
            docs = [(title_id, row[0])] if row else []
        else:
            docs = [(tid, doc) for tid, doc, _cover in self._walk(progress, stop)]
        out: list[dict] = []
        for tid, doc in docs:
            if doc.meta.chapterOrder == "manual":
                continue  # a hand-made order says the sequence is the owner's
            scopes: dict[tuple[str, str], list[str]] = {}
            for ch in doc.chapters:
                scopes.setdefault((ch.lang, ch.group), []).append(ch.num)
            for (lang, group), nums in scopes.items():
                missing = _missing_numbers(nums, self._GAP_MIN_ROWS,
                                           self._GAP_NUMERIC, self._GAP_DENSITY)
                if missing:
                    out.append({"titleId": tid, "title": doc.meta.title, "lang": lang,
                                "group": group, "missing": missing})
        return out

    def check(self, *, deep: bool = False, backfill: bool = False, name: str = "check",
              progress: Progress = None, stop: threading.Event | None = None) -> dict:
        """Is anything broken, is anything wasting space, is anything missing.

        One pass, three answers. Duplicates and gaps read the index and cost
        nothing on top of the walk, so asking for them separately was only ever
        a choice the person had to make on the app's behalf."""
        started = time.monotonic()
        found = self.verify(deep=deep, backfill=backfill, progress=progress, stop=stop)
        out = compose(found, self.duplicates(), self.gaps(), self.index.count())
        out["at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        # The REPORT is stored, not just its summary: it is already capped, and
        # a count with no rows would mean re-reading the whole library to find
        # out which titles it was talking about.
        self._record(name, started, _check_outcome(out), stopped=out["stopped"], last_check=out)
        return out

    def delete_leftovers(self, title_ids: list[str] | None = None, progress: Progress = None,
                         stop: threading.Event | None = None) -> dict:
        """Remove the files that belong to no entry at all.

        `title_ids` is a SCOPE, not a list of things to delete: within each one
        the leftovers are worked out again from the entries the title actually
        has, so nothing arriving from outside can name a file for removal. The
        scope comes from the last check, which already knows where they were —
        rediscovering that costs a lock and a directory listing per title in the
        library, which on a network vault is minutes to delete two files.

        With no scope it falls back to the whole library, because a sweep with
        nothing to go on still has to be right."""
        started = time.monotonic()
        deleted = failed = 0
        freed = 0
        seen = 0
        survivors: list[dict] = []
        scope = list(title_ids) if title_ids is not None else             [tid for tid, _d, _c in self.index.all_docs()]
        total = len(scope)
        if progress is not None:
            progress(0, total)
        for title_id in scope:
            if stop is not None and stop.is_set():
                break
            seen += 1
            if progress is not None:
                progress(seen, total)
            with self.vault.title_lock(title_id):
                fresh = self.vault.load(title_id)
                if fresh is None:
                    continue
                stems = {safe_id(c.id) for c in fresh.chapters}
                d = self.vault._chapters_dir(title_id)
                gone = [*self._strays(title_id, fresh, stems)]
                for stem in self.vault.chapter_sidecars(title_id):
                    if stem not in stems:
                        gone.append({"name": f"{stem}.json"})
                for item in gone:
                    target = d / str(item["name"])
                    size = _size_of(target)
                    try:
                        target.unlink()
                    except OSError:
                        failed += 1
                        # what would not go is still a leftover, and the report
                        # has to keep saying so
                        survivors.append({**item, "bytes": size})
                        continue
                    deleted += 1
                    freed += size
        stopped = _stopped(stop, seen, total)
        self._record("delete leftovers", started,
                     f"{deleted} file(s) deleted, {_mb(freed)} freed" if deleted
                     else "nothing to delete", stopped=stopped)
        if not stopped:
            self._forget_swept(set(scope), survivors)
        return {"deleted": deleted, "failed": failed, "bytes": freed}

    def _forget_swept(self, scope: set[str], survivors: list[dict]) -> None:
        """Replace the swept titles' leftovers in the stored report.

        Only ever called for a sweep that finished its whole scope, and it does
        no arithmetic: for every title visited, `survivors` IS the truth now, so
        the block is rebuilt from what is left rather than by subtracting from
        counts whose row list is capped. A sweep put down early corrects
        nothing — the log says it was stopped, and inventing a number would be
        worse than an out-of-date one."""
        stored = self.vault.health().get("lastCheck")
        if not stored:
            return
        left = stored.get("leftovers") or {}
        outside = [r for r in (left.get("rows") or []) if r.get("titleId") not in scope]
        rows = outside + [{"titleId": r["titleId"], "title": r["title"],
                           "name": r.get("name", ""), "bytes": int(r.get("bytes") or 0)}
                          for r in survivors]
        # `files` may have exceeded the capped rows, but only for titles this
        # sweep did not visit; what it did visit is now counted exactly.
        beyond = max(0, int(left.get("files") or 0) - len(left.get("rows") or []))
        stored["leftovers"] = {
            **left,
            "files": len(rows) + beyond,
            "bytes": sum(int(r.get("bytes") or 0) for r in rows),
            "titles": len({r["titleId"] for r in rows}),
            "titleIds": sorted({r["titleId"] for r in rows}),
            "rows": rows,
        }
        self.vault.write_health(last_check=stored)

    def _stamp_digest(self, title_id: str, chapter_id: str, digest: str) -> None:
        """Record a digest without touching `rev` — nothing about the content
        changed, so no cache may miss on account of it."""
        with self.vault.title_lock(title_id):
            # re-read UNDER the lock (an ingest may have landed while the pass
            # was hashing) — but read the one file, not the whole title's worth
            side = self.vault.chapter_sidecar(title_id, chapter_id)
            if side is None or side.get("sha256"):
                return  # ingested (or stamped) while the pass was reading
            self.vault.write_chapter_sidecar(title_id, chapter_id, {**side, "sha256": digest})

    @property
    def root(self) -> Path:
        return self.vault.root

    def sync_in_background(self) -> None:
        """Verify against disk without holding anything up. A first-ever open has
        an empty index and fills it here, which the UI shows as progress; every
        later open finds nothing to do and the user never learns it happened."""
        if self._sync_thread is not None and self._sync_thread.is_alive():
            return
        self.sync_state.update(running=True, done=0, total=0, changed=0)

        def run() -> None:
            try:
                self.sync(lambda done, total: self.sync_state.update(done=done, total=total))
            except Exception:  # noqa: BLE001 — a failed verification must not take the app down
                log.exception("background sync failed")
            finally:
                self.sync_state["running"] = False

        self._sync_thread = threading.Thread(target=run, name="lb-sync", daemon=True)
        self._sync_thread.start()

    def sync(self, progress=None) -> int:
        """Bring the index in line with the vault WITHOUT re-reading it whole.
        Every title is stat-ed (cheap) and only the ones whose document or
        chapter directory moved since they were indexed are loaded again — a
        launch must not cost one JSON parse per title, or opening the app grows
        with the library. `progress(done, total)` ticks per title actually read.
        Returns how many titles were re-read."""
        started = time.perf_counter()
        indexed = self.index.stamps()
        scanned = self.vault.scan()
        on_disk = {tid: (doc_at, ch_at, self._cover_of(tid, name, cover_at, cover_size))
                   for tid, (doc_at, ch_at, name, cover_at, cover_size) in scanned.items()}
        statted = time.perf_counter()
        for gone in indexed.keys() - on_disk.keys():
            self.index.remove(gone)
        stale = [tid for tid, stamp in on_disk.items() if indexed.get(tid) != stamp]
        if progress:
            progress(0, len(stale))
        rows = []
        for i, tid in enumerate(stale):
            if self._closing.is_set():
                # the library is being closed (a switch, or the app quitting):
                # stop reading rather than write into a database about to close
                log.info("sync stopped after %d of %d titles", i, len(stale))
                break
            try:
                doc = self.vault.load(tid)
            except Exception:  # noqa: BLE001 — one malformed title.json never blocks a launch
                continue
            if doc is not None:
                # stamped before the read, exactly as _index does
                stamp = on_disk[tid]
                # what this scan saw in the index, so the write can tell "nobody
                # touched it since" from "someone else wrote a newer row"
                was = indexed.get(tid, (0, 0, ""))
                rows.append((tid, doc, stamp[0], self._sidecars(tid), stamp[1], stamp[2], was))
            if progress:
                progress(i + 1, len(stale))
        # nothing is serving yet at construction time, so one batch is safe here
        # where per-title locking is the rule everywhere else
        if self._closing.is_set():
            return 0
        self.index.upsert_many(rows)
        self.sync_state["changed"] = len(rows) + len(indexed.keys() - on_disk.keys())
        # the numbers a slow launch has to be diagnosed with — a network vault
        # costs a round trip per stat, and that is invisible from the outside
        log.info("opened %s: %d titles (scan %.1fs, re-read %d in %.1fs)",
                 self.vault.root, len(on_disk), statted - started,
                 len(rows), time.perf_counter() - statted)
        return len(rows)

    def rescan(self, progress: Progress = None,
               stop: threading.Event | None = None) -> None:
        """Re-read every title from disk and refresh the index. `progress(done,
        total)` ticks per title file read — reading the files IS the slow part.
        ONE unreadable document must never abort the scan (or, at startup, the
        whole app): it is skipped, exactly like the layout migration does.

        This runs while the app is in USE (Settings → Rebuild), so it is not a
        DELETE + INSERT of the whole table: each row is written only while the
        stamps this pass read still hold, and a title committed meanwhile keeps
        the newer row."""
        started = time.monotonic()
        if progress is not None:
            progress(0, self.index.count())  # the scan below is the slow part
        scanned = self.vault.scan()
        ids = sorted(scanned)
        docs = {}
        touched = {}
        media = {}
        media_at = {}
        cover = {}
        for i, tid in enumerate(ids):
            if stop is not None and stop.is_set():
                # a rebuild put down halfway leaves the index as it was: half a
                # table is worse than a stale one, and the pass is re-runnable
                return
            try:
                doc = self.vault.load(tid)
            except Exception:  # noqa: BLE001 — malformed title.json
                doc = None
            if doc is not None:
                docs[tid] = doc
                doc_at, ch_at, name, cover_at, cover_size = scanned[tid]
                touched[tid] = doc_at
                media[tid] = self._sidecars(tid)
                media_at[tid] = ch_at
                cover[tid] = self._cover_of(tid, name, cover_at, cover_size)
            if progress:
                progress(i + 1, len(ids))
        self.index.rebuild(docs, touched, media, media_at, cover)
        self._record("rebuild index", started, f"{len(docs)} title(s) re-read")

    def close(self) -> None:
        # tell the sweep to stop, give it a moment, then close the index it uses
        self._closing.set()
        for thread in (self._normalize_thread, self._sync_thread):
            if thread is not None and thread.is_alive():
                thread.join(timeout=10)
        self.index.close()

    # ---- DTO composition ----

    @classmethod
    def _cover_of(cls, title_id: str, name: str, mtime: int, size: int) -> str:
        """The cover endpoint URL, versioned so a re-captured cover busts every
        cache. Composed from a scan, never from a fresh stat."""
        v = cover_version(name, mtime, size)
        return f"/api/titles/{title_id}/cover?v={v}" if v else ""

    def _cover_url(self, title_id: str) -> str:
        """The same URL for ONE title, straight after a write."""
        path = self.vault.cover_path(title_id)
        if path is None:
            return ""
        stat = path.stat()
        return self._cover_of(title_id, path.name, stat.st_mtime_ns, stat.st_size)

    def _index(self, title_id: str, doc: TitleDoc) -> None:
        """Refresh the index for one title. ALWAYS called inside that title's
        lock: a doc read in a critical section and indexed after it is released
        can overwrite a newer writer's row with stale data. The chapter sidecars
        are read HERE, once per write, so no listing ever has to touch them."""
        # The write that just happened may not have moved the chapters directory
        # far enough for an mtime-keyed cache to notice (two changes can share one
        # timestamp), and a listing composed from a stale sidecar shows a chapter
        # that is no longer there. After a write the cache is simply wrong.
        self._sidecar_cache.pop(title_id, None)
        # stamp BEFORE reading: a change that lands in between then looks newer
        # than the row, so the next launch re-reads it — the other order would
        # store fresh stamps over stale content and never notice
        stamp = self.vault.chapters_stamp(title_id)
        self.index.upsert(title_id, doc, self.vault.touched_at(title_id),
                          self._sidecars(title_id), stamp, self._cover_url(title_id))

    def _sidecars(self, title_id: str) -> dict[str, dict]:
        """Chapter sidecars, cached per title against the chapters dir's mtime.
        A library listing composes every title's DTO, and re-globbing + parsing
        one JSON file per chapter each time is what makes a big library crawl."""
        d = self.vault.chapters_dir(title_id)
        try:
            stamp = d.stat().st_mtime_ns
        except OSError:
            self._sidecar_cache.pop(title_id, None)
            return {}
        hit = self._sidecar_cache.get(title_id)
        if hit is not None and hit[0] == stamp:
            return hit[1]
        loaded = self.vault.chapter_sidecars(title_id)
        self._sidecar_cache[title_id] = (stamp, loaded)
        return loaded

    def _out_now(self, title_id: str, doc: TitleDoc) -> TitleOut:
        """One title composed right after a write, when the sidecars on disk are
        newer than whatever the index row still carries."""
        self._sidecar_cache.pop(title_id, None)  # the write is newer than any cache
        return self._out(title_id, doc, self._sidecars(title_id), self._cover_url(title_id))

    def _out(self, title_id: str, doc: TitleDoc, sidecars: dict[str, dict], cover: str) -> TitleOut:
        media_map = {c.id: sidecars[safe_id(c.id)] for c in doc.chapters if safe_id(c.id) in sidecars}
        return TitleOut.from_doc(title_id, doc, cover, media_map)

    # ---- reads (via the index) ----

    def query(self, **kwargs) -> list[TitleOut]:
        return [self._out(tid, doc, media, cover)
                for tid, doc, media, cover in self.index.query(**kwargs)]

    def get(self, title_id: str) -> TitleOut | None:
        row = self.index.get(title_id)
        return self._out(title_id, *row) if row else None

    def facets(self, selection: dict | None = None) -> dict[str, list[dict]]:
        return self.index.facet_counts(selection or {})

    def retype_candidates(self, field_id: str, to_type: str, join: str = ", ") -> list[str]:
        """Which titles hold a value of the wrong shape for this field.

        Decided from the INDEX, which already carries every document — so a save
        that changes nothing costs no disk at all. The value is checked again
        under the lock before anything is written; this only says where to look.
        """
        sep = join or ", "
        return [tid for tid, doc, _c in self.index.all_docs()
                if _retyped(doc.meta.custom.get(field_id), to_type, sep) is not None]

    def clear_field(self, field_id: str, progress: Progress = None,
                    stop: threading.Event | None = None) -> dict:
        """Remove a field's stored values from every title that holds one.

        The scope comes from the index, so only titles that actually hold a
        value are opened. Nothing else about them is touched — this is the
        second half of "remove the field", for when you mean the data too."""
        started = time.monotonic()
        scope = [tid for tid, doc, _c in self.index.all_docs()
                 if field_id in doc.meta.custom]
        total = len(scope)
        cleared = seen = 0
        if progress is not None:
            progress(0, total)
        for title_id in scope:
            if stop is not None and stop.is_set():
                break
            seen += 1
            if progress is not None:
                progress(seen, total)
            with self.vault.title_lock(title_id):
                doc = self.vault.save_meta_value(
                    title_id, lambda d: d.meta.custom.pop(field_id, None) is not None)
                if doc is not None:
                    self._index(title_id, doc)
                    cleared += 1
        stopped = _stopped(stop, seen, total)
        self._record(f"clear {field_id}", started,
                     f"{cleared} title(s) cleared" if cleared else "nothing to clear",
                     stopped=stopped)
        return {"cleared": cleared, "stopped": stopped}

    def join_conflicts(self, field_id: str, join: str, limit: int = 5) -> dict:
        """Values that would not survive being joined on this separator.

        Folding a list into text is only reversible while no value CONTAINS the
        separator: `["Ito, Junji", "Mori"]` joined on ", " reads back as three
        names, and nothing afterwards can tell that it was two. The separator is
        the caller's to choose, so this says which choice destroys data."""
        sep = (join or ", ").strip() or (join or ", ")
        bad: list[dict] = []
        total = 0
        for tid, doc, _c in self.index.all_docs():
            value = doc.meta.custom.get(field_id)
            if not isinstance(value, list):
                continue
            hit = [v for v in value if isinstance(v, str) and sep in v]
            if not hit:
                continue
            total += 1
            if len(bad) < limit:
                bad.append({"titleId": tid, "title": doc.meta.title, "value": hit[0]})
        return {"total": total, "examples": bad}

    @staticmethod
    def retype_moves_data(was: str, now: str) -> bool:
        """Whether a type change has anything to convert at all.

        Text, description, number and date all store the SAME thing — one
        string. Only a list is shaped differently, so only a change into or out
        of one moves data. Asking the library otherwise means deserialising
        every document to be told there is nothing to do."""
        return "list" in (was, now) and was != now

    def retype_field(self, field_id: str, to_type: str, join: str = ", ",
                     progress: Progress = None,
                     stop: threading.Event | None = None) -> dict:
        """Convert what titles already hold when a field changes type.

        Changing a definition without touching the values leaves the vault
        holding a list under a field that now says text — which every screen
        then renders by whatever its own accident is. The definition and the
        data move together or the field is broken.

        `join` is the separator a list is folded into text with, and the one a
        text is split back on. It is the caller's because only they know what
        the values look like: a comma is right for tags and wrong for names
        that contain commas."""
        started = time.monotonic()
        sep = join or ", "
        scope = self.retype_candidates(field_id, to_type, sep)
        total = len(scope)
        changed = seen = 0
        if progress is not None:
            progress(0, total)
        for title_id in scope:
            if stop is not None and stop.is_set():
                break
            seen += 1
            if progress is not None:
                progress(seen, total)
            # decided from the value found UNDER the lock, in one read: loading
            # the document to look and again to write is a title.json read per
            # title for nothing
            def convert(d: TitleDoc) -> bool:
                have = d.meta.custom.get(field_id)
                fresh = _retyped(have, to_type, sep)
                if fresh is None or fresh == have:
                    return False
                d.meta.custom[field_id] = fresh
                return True

            with self.vault.title_lock(title_id):
                doc = self.vault.save_meta_value(title_id, convert)
                if doc is not None:
                    self._index(title_id, doc)
                    changed += 1
        stopped = _stopped(stop, seen, total)
        self._record(f"retype {field_id}", started,
                     f"{changed} title(s) converted" if changed else "nothing to convert",
                     stopped=stopped)
        return {"converted": changed, "stopped": stopped}

    def field_usage(self) -> dict[str, int]:
        """How many titles hold a value in each field, over the WHOLE library.

        Counted here rather than in the browser, which only ever has the page it
        is currently showing: reading the count off that made every field report
        whatever the library happened to be filtered to."""
        used: dict[str, int] = {f.id: 0 for f in fields.registry()}
        for _tid, doc, _cover in self.index.all_docs():
            for f in fields.registry():
                if f.values is not None:
                    if fields.facet_values(f, doc):
                        used[f.id] += 1
                    continue
                value = (doc.meta.custom.get(f.id) if not f.builtin
                         else getattr(doc.meta, f.attr, None) if f.attr else None)
                if value if not isinstance(value, list) else len(value):
                    used[f.id] += 1
        return used

    def count(self) -> int:
        return self.index.count()

    def browse(self, field_id: str, selection: dict | None = None) -> list[BrowseGroup]:
        """Titles grouped by one LIST field. The registry decides what can be an
        axis: a number or a date has nothing to group by, so only lists qualify.

        `authors` is not a separate code path — it is this, plus the two things
        only people have: the role they played and the favourite mark."""
        f = fields.by_id().get(field_id)
        if f is None or f.type != "list":
            return []
        people = field_id == "authors"
        # Filtering a browse means "which groups survive, and what is left inside
        # them" — so it is the same selection the library is under, applied to the
        # documents BEFORE they are grouped. A group with nothing left disappears.
        docs = (self.index.query(**selection) if selection else
                [(tid, doc, {}, cover) for tid, doc, cover in self.index.all_docs()])
        agg: dict[str, dict] = {}
        for tid, doc, _media, cover in docs:
            m = doc.meta
            for value in fields.facet_values(f, doc):
                name = value.strip()
                if not name:
                    continue
                g = agg.setdefault(name, {"works": {}, "chapters": 0, "tags": Counter(),
                                          "author": False, "artist": False})
                if people:
                    g["author"] = g["author"] or name in m.authors
                    g["artist"] = g["artist"] or name in m.artists
                if tid not in g["works"]:
                    g["works"][tid] = AuthorWork(id=tid, title=m.title, cover=cover)
                    g["chapters"] += len(doc.chapters)
                    g["tags"].update(m.tags)
        favs = self.vault.author_favorites() if people else set()
        out: list[BrowseGroup] = []
        for name, g in sorted(agg.items()):
            gid = safe_id(name)
            role = None
            if people:
                role = "both" if g["author"] and g["artist"] else ("artist" if g["artist"] else "author")
            out.append(BrowseGroup(
                id=gid, field=field_id, value=name, role=role, fav=people and gid in favs,
                works=list(g["works"].values()), titles=len(g["works"]),
                chapters=g["chapters"], topTags=[t for t, _ in g["tags"].most_common(5)]))
        return out

    def set_author_favorite(self, author_id: str, value: bool) -> bool:
        """Write-through favorite mark; True when the author currently exists."""
        self.vault.set_author_favorite(author_id, value)
        return any(g.id == author_id for g in self.browse("authors"))

    def sources(self) -> list[Source]:
        """Sites aggregated from the titles' source bindings. Recipe detail is
        joined on in the router (the recipe store is app-level state)."""
        agg: dict[str, int] = {}
        for _, doc, _cover in self.index.all_docs():
            domain = doc.meta.source.domain or (urlsplit(doc.meta.source.url).hostname or "")
            if domain:
                agg[domain] = agg.get(domain, 0) + 1
        return [
            Source(id=safe_id(domain), domain=domain, homepage=f"https://{domain}/", titles=n)
            for domain, n in sorted(agg.items())
        ]

    # ---- writes (vault first; index follows) ----

    def create(self, draft: DraftIn) -> TitleOut:
        """Commit a NEW draft, assigning a unique filesystem-safe id."""
        with self._create_lock:
            base = safe_id(draft.meta.title or "untitled")
            tid, n = base, 2
            while self.vault.exists(tid):
                tid, n = f"{base}-{n}", n + 1
            doc = self.vault.commit_meta(tid, draft, create=True)
            assert doc is not None
            with self.vault.title_lock(tid):  # the rule holds for the first write too
                self._index(tid, doc)
        return self._out_now(tid, doc)

    def commit(self, title_id: str, draft: DraftIn) -> TitleOut | None:
        """Commit a draft into an existing title. Replaces the meta layers,
        preserves the user layer (enforced in the vault)."""
        # ONE critical section: reconciling against a doc another writer is
        # about to replace would resurrect or drop rows
        with self.vault.title_lock(title_id):
            old = self.vault.load(title_id)
            if old is not None:
                self._reconcile_chapters(title_id, old, draft)
            doc = self.vault.commit_meta(title_id, draft)
            if doc is None:
                return None
            self._index(title_id, doc)
        return self._out_now(title_id, doc)

    def _reconcile_chapters(self, title_id: str, old: TitleDoc, draft: DraftIn) -> None:
        """A meta commit must never orphan what the user already owns. Media
        archives and read states are keyed by chapter id, but a re-captured row
        arrives with a FRESH id — so a row that is the same chapter (same URL,
        else same num+lang+group) adopts its old id. And a media-backed row
        missing from the draft entirely (edited from a stale view) is put back:
        downloaded chapters are removed only by the explicit delete endpoints."""
        def key(c) -> tuple[str, str, str]:
            return (c.num.strip().casefold(), c.lang.strip().casefold(), c.group.strip().casefold())

        old_ids = {c.id for c in old.chapters}
        incoming_ids = {c.id for c in draft.chapters}
        spare = {c.id: c for c in old.chapters if c.id not in incoming_ids}
        by_url = {c.url: c.id for c in spare.values() if c.url}
        by_key: dict[tuple, list[str]] = {}
        for c in spare.values():
            by_key.setdefault(key(c), []).append(c.id)

        def claim(adopted_id: str) -> None:
            # an adopted id leaves BOTH lookup maps — otherwise a later draft
            # row matches it again via the other map and gets dropped as a twin
            row = spare.pop(adopted_id)
            if row.url:
                by_url.pop(row.url, None)
            bucket = by_key.get(key(row))
            if bucket and adopted_id in bucket:
                bucket.remove(adopted_id)

        rows, taken = [], set()
        for c in draft.chapters:
            cid = c.id or self.chapter_id_for(c.num, c.lang, c.group)
            if cid not in old_ids:
                adopted = by_url.get(c.url) if c.url else None
                if adopted is None and by_key.get(key(c)):
                    adopted = by_key[key(c)][0]
                if adopted is not None:
                    claim(adopted)
                    cid = adopted
            if cid in taken:
                continue  # the draft itself repeats one id — trust the first copy
            taken.add(cid)
            c.id = cid
            rows.append(c)

        # collapse twins that share the chapter identity with a row anchored by
        # an OLD id (the fresh copy merges away, topping up empty fields)
        final: list = []
        at: dict[tuple, int] = {}
        for c in rows:
            j = at.get(key(c))
            if j is None:
                at[key(c)] = len(final)
                final.append(c)
                continue
            other = final[j]
            if c.url and other.url and c.url != other.url:
                final.append(c)  # genuinely different uploads of the same number
                continue
            anchor = other if other.id in old_ids else (c if c.id in old_ids else None)
            if anchor is None:
                final.append(c)  # two brand-new rows — trust the draft
                continue
            fresh = c if anchor is other else other
            for f in ("url", "title", "date"):
                if not getattr(anchor, f) and getattr(fresh, f):
                    setattr(anchor, f, getattr(fresh, f))
            final[j] = anchor
        rows = final

        sidecars = self.vault.chapter_sidecars(title_id)
        kept = {safe_id(c.id) for c in rows}
        for c in old.chapters:
            # media-backed = a sidecar OR a bare archive (a crash may have left
            # the zip without its sidecar) — either way the row must survive
            has_media = (safe_id(c.id) in sidecars
                         or self.vault.chapter_media_path(title_id, c.id) is not None)
            if not has_media or safe_id(c.id) in kept:
                continue
            if draft.meta.chapterOrder == "manual":
                rows.append(c)
            else:
                k = _chapter_num_key(c.num)
                idx = next((i for i, r in enumerate(rows) if _chapter_num_key(r.num) > k), len(rows))
                rows.insert(idx, c)
            kept.add(safe_id(c.id))
        draft.chapters = rows

    def patch_user(self, title_id: str, patch: UserPatch) -> TitleOut | None:
        with self.vault.title_lock(title_id):
            doc = self.vault.patch_user(title_id, patch)
            if doc is None:
                return None
            self._index(title_id, doc)
        return self._out_now(title_id, doc)

    def set_cover(self, title_id: str, data: bytes, ext: str, source_url: str = "") -> TitleOut | None:
        """Store captured cover bytes; record where they came from in the meta
        layer (the cover is part of the draft flow, but its bytes arrive as a
        side-channel upload right after the commit)."""
        with self.vault.title_lock(title_id):
            doc = self.vault.load(title_id)
            if doc is None:
                return None
            self.vault.write_cover(title_id, data, ext)
            if source_url and doc.meta.coverSource != source_url:
                doc.meta.coverSource = source_url
                draft = DraftIn(meta=doc.meta, provenance=doc.provenance, chapters=doc.chapters)
                doc = self.vault.commit_meta(title_id, draft) or doc
            self._index(title_id, doc)
        return self._out_now(title_id, doc)

    def delete_cover(self, title_id: str) -> TitleOut | None:
        with self.vault.title_lock(title_id):
            doc = self.vault.load(title_id)
            if doc is None:
                return None
            self.vault.delete_cover(title_id)
            if doc.meta.coverSource:
                doc.meta.coverSource = ""
                draft = DraftIn(meta=doc.meta, provenance=doc.provenance, chapters=doc.chapters)
                doc = self.vault.commit_meta(title_id, draft) or doc
            self._index(title_id, doc)
        return self._out_now(title_id, doc)

    # ---- chapter media (downloads) ----

    def _recommit(self, title_id: str, doc: TitleDoc) -> TitleOut | None:
        """Persist a mutated doc's meta layers (user layer preserved by the vault)."""
        draft = DraftIn(meta=doc.meta, provenance=doc.provenance, chapters=doc.chapters)
        saved = self.vault.commit_meta(title_id, draft)
        if saved is None:
            return None
        self._index(title_id, saved)
        return self._out_now(title_id, saved)

    @staticmethod
    def _norm(s: str) -> str:
        return s.strip().casefold()

    @staticmethod
    def chapter_id_for(num: str, lang: str, group: str) -> str:
        """The id a chapter identity gets. The ONLY place an id is derived: a
        client that invents its own would disagree with the row reconciliation
        writes, and then address media that lives under a different id."""
        n = Library._norm
        digest = hashlib.sha1(f"{n(num)}|{n(lang)}|{n(group)}".encode()).hexdigest()[:8]
        return safe_id(f"ch-{num or 'x'}-{digest}")

    def _row_for(self, doc: TitleDoc, *, num: str, lang: str, group: str,
                 url: str = "") -> tuple[ChapterRow, bool]:
        """The chapter row matching (num, lang, group), created in place when the
        list lacks it. Returns (row, created) — a created row means the caller
        must recommit the document, not just refresh the index."""
        n = self._norm
        row = next((c for c in doc.chapters
                    if n(c.num) == n(num) and n(c.lang) == n(lang) and n(c.group) == n(group)), None)
        if row is not None:
            if url and not row.url:
                row.url = url  # a supplied source link tops up the existing row
            return row, False
        row = ChapterRow(id=self.chapter_id_for(num, lang, group), num=num, lang=lang, group=group, url=url)
        if doc.meta.chapterOrder == "manual":
            doc.chapters.append(row)  # the user's arrangement is sacred
        else:
            key = _chapter_num_key(num)
            idx = next((i for i, c in enumerate(doc.chapters) if _chapter_num_key(c.num) > key), len(doc.chapters))
            doc.chapters.insert(idx, row)
        return row, True

    def attach_chapter_media(
        self, title_id: str, *, num: str, lang: str, group: str,
        src: Path, sidecar: dict, url: str = "", chapter_id: str = "",
    ) -> TitleOut | None:
        """Attach downloaded media to a chapter: to the EXACT row when
        `chapter_id` is given (attach/replace on an existing row), else to the
        row matching (num, lang, group) — created when the list lacks it.
        An ARCHIVE replaces the row's media (normalized to zip in the vault);
        a SINGLE IMAGE (unpacked media) appends as the next page instead, so
        page-by-page downloads accumulate into one chapter zip; a VIDEO file
        becomes the row's media as it stands — an episode is not a zip."""
        # one critical section end-to-end: two completing downloads for the same
        # title otherwise load the same doc and the later recommit drops the
        # other's freshly added row
        with self.vault.title_lock(title_id):
            doc = self.vault.load(title_id)
            if doc is None:
                return None
            if chapter_id:
                row = next((c for c in doc.chapters if c.id == chapter_id), None)
                if row is None:
                    return None
                if url and not row.url:
                    row.url = url  # a supplied source link tops up the pinned row
            else:
                # An archive that already describes itself gets to name its own
                # entry — but only where the caller supplied nothing. A label
                # typed by hand is a decision, and a file never overrides one.
                if not num.strip():
                    said = comicinfo.read_from(src)
                    num = said.get("num") or num
                    lang = lang or said.get("lang", "")
                    group = group or said.get("group", "")
                row, _ = self._row_for(doc, num=num, lang=lang, group=group, url=url)
            suffix = Path(sidecar.get("filename") or src.name).suffix.lower()
            if suffix in media.IMAGE_EXTS:
                # unpacked media: the image becomes the chapter zip's next page
                path = self.vault.chapter_media_path(title_id, row.id)
                if path is None:
                    path = self.vault.chapter_archive_target(title_id, row.id)
                media.renumber_and_append(path, [(src.read_bytes(), suffix)])
                src.unlink(missing_ok=True)
                # through the ONE writer: it stamps pages, size and the archive
                # mtime the page URLs are versioned by
                self._write_media_sidecar(title_id, row.id, path, patch=sidecar)
            else:
                # the ingest decides by the name the file ARRIVED under: a video
                # is stored as itself, an archive is normalized to zip
                stored = self.vault.ingest_chapter_media(
                    title_id, row.id, src, sidecar,
                    filename=str(sidecar.get("filename") or src.name))
                self._mirror_comicinfo(title_id, doc, row, stored)
            return self._recommit(title_id, doc)

    def _mirror_comicinfo(self, title_id: str, doc: TitleDoc, row: ChapterRow,
                          path: Path, sidecars: dict[str, dict] | None = None) -> bool:
        """Put this chapter's ComicInfo.xml into its archive, and re-describe the
        file afterwards.

        The order matters: the digest and size were stamped for the file as it
        arrived, and this rewrites it. Leaving them would make the very next
        revision pass report every freshly stored chapter as corrupt."""
        if not path.is_file() or media.is_video(path):
            return False
        data = comicinfo.build(doc, row)
        stamp = hashlib.sha256(data).hexdigest()[:16]
        # Reading the title's sidecars is a glob and a JSON parse per chapter, so
        # a caller looping over chapters reads them ONCE and hands them in — this
        # method asking for itself made that a read per chapter, per chapter.
        known = self.vault.chapter_sidecars(title_id) if sidecars is None else sidecars
        side = known.get(safe_id(row.id))
        # What was last written is recorded, so a chapter whose metadata has not
        # moved is skipped without opening its archive at all. Comparing inside
        # the zip would be correct too — and would cost a read of every archive
        # in the library to discover that nothing needs doing.
        if side is not None and side.get("comicinfo") == stamp:
            return False
        try:
            if not comicinfo.write_into(path, data):
                # already identical inside: record that, so the next pass can
                # tell without looking
                if side is not None:
                    self.vault.write_chapter_sidecar(title_id, row.id,
                                                     {**side, "comicinfo": stamp})
                return False
        except (media.UnsupportedArchiveError, OSError) as e:
            log.warning("comicinfo for %s/%s not written: %s", title_id, row.id, e)
            return False
        side = known.get(safe_id(row.id))
        if side is not None:
            # NOT a revision bump: the pages did not change, so no cached page
            # may miss on account of a metadata file appearing beside them
            self.vault.write_chapter_sidecar(title_id, row.id, {
                **side, "size": path.stat().st_size, "sha256": media.digest_of(path),
                "comicinfo": stamp})
        return True

    # ---- page capture (sources that serve pages, not archives) ----
    # The reader page IS the source: images are grabbed one page-view at a time
    # while the human reads, into the chapter row THEY armed — same explicit
    # binding as an armed archive download, never a guess from the URL. Each
    # stored page remembers its image KEY (the file name the site served, not
    # the full URL — CDN links carry rotating tokens), so re-visiting a page
    # fetches and writes nothing.

    def stored_page_keys(self, title_id: str, chapter_id: str) -> list[str]:
        """Which page keys this chapter already holds. Empty when the archive is
        missing or unreadable — damaged media is re-captured, not trusted."""
        path = self.vault.chapter_media_path(title_id, chapter_id)
        if path is None:
            return []
        entries = media.image_entries(path)
        if not entries:
            return []
        return [k for k in self._page_keys(title_id, chapter_id, len(entries)) if k]

    def capture_chapter_pages(
        self, title_id: str, chapter_id: str, *, page_url: str,
        images: list[tuple[bytes, str, str]],
    ) -> tuple[TitleOut, int] | None:
        """Append captured page images (data, ext, key) to an EXISTING chapter
        row. Keys already stored are skipped. Returns the title and how many
        pages were actually added; None when the title or row is gone."""
        with self.vault.title_lock(title_id):
            doc = self.vault.load(title_id)
            if doc is None or not any(c.id == chapter_id for c in doc.chapters):
                return None
            path = self.vault.chapter_media_path(title_id, chapter_id)
            if path is None:
                path = self.vault.chapter_archive_target(title_id, chapter_id)
            entries = media.image_entries(path)
            # a chapter whose archive cannot be read is not trusted: its keys go
            # too, so the pages are captured again rather than assumed present
            keys = self._page_keys(title_id, chapter_id, len(entries)) if entries else []
            known = {k for k in keys if k}
            fresh = []
            for data, ext, key in images:
                if key in known or not data:
                    continue
                known.add(key)
                fresh.append((data, ext, key))
            if fresh:
                media.renumber_and_append(path, [(d, e) for d, e, _ in fresh])
                self._write_media_sidecar(
                    title_id, chapter_id, path,
                    keys=[*keys, *[k for _, _, k in fresh]],
                    defaults={"importedFrom": "page-capture"},
                    patch={"pageUrl": page_url})
            self._index(title_id, doc)
            return self._out_now(title_id, doc), len(fresh)

    def delete_chapter_media(self, title_id: str, chapter_id: str) -> TitleOut | None:
        with self.vault.title_lock(title_id):
            doc = self.vault.load(title_id)
            if doc is None:
                return None
            self.vault.delete_chapter_media(title_id, chapter_id)
            self._index(title_id, doc)
        return self._out_now(title_id, doc)

    def delete_chapter_row(self, title_id: str, chapter_id: str) -> TitleOut | None:
        """Remove the chapter row AND its downloaded media."""
        with self.vault.title_lock(title_id):
            doc = self.vault.load(title_id)
            if doc is None:
                return None
            before = len(doc.chapters)
            doc.chapters = [c for c in doc.chapters if c.id != chapter_id]
            if len(doc.chapters) == before and self.vault.chapter_media_path(title_id, chapter_id) is None:
                return None  # neither a row nor media — unknown chapter
            self.vault.delete_chapter_media(title_id, chapter_id)
            return self._recommit(title_id, doc)

    @staticmethod
    def _cached_thumb(key: str, source: Callable[[], tuple[bytes, str]], width: int,
                      cap: float | None = None) -> tuple[bytes, str]:
        """A downscaled JPEG cached on disk under `key` (which the caller stamps
        with the chapter/cover version, so an edited file misses the cache by
        construction). `source` is called only on a MISS — a cache hit must not
        read the original, which on a network vault is the entire request cost.
        Undecodable formats serve the original."""
        from ..config_store import config_dir
        cfile = config_dir() / "cache" / "thumbs" / f"{key}.jpg"
        if cfile.is_file():
            try:
                return cfile.read_bytes(), "image/jpeg"
            except OSError:
                pass  # a half-written or vanished cache entry: just re-make it
        data, ct = source()
        thumb = media.thumbnail(data, width, cap)
        if thumb is None:
            return data, ct
        cfile.parent.mkdir(parents=True, exist_ok=True)
        # unique temp name: two requests for the same tile are normal, and a
        # shared one would interleave into a corrupt cached JPEG
        tmp = cfile.with_name(f"{cfile.name}.{uuid.uuid4().hex[:8]}.tmp")
        try:
            tmp.write_bytes(thumb)
            os.replace(tmp, cfile)
        except OSError:
            tmp.unlink(missing_ok=True)
        return thumb, "image/jpeg"

    def cover_thumb(self, title_id: str, width: int) -> tuple[bytes, str] | None:
        """A cached downscaled cover — grids and lists never load the original."""
        path = self.vault.cover_path(title_id)
        if path is None:
            return None
        ct = media.CT_BY_EXT.get(path.suffix.lower(), "application/octet-stream")
        stat = path.stat()
        key = cache_key("cover", cover_version(path.name, stat.st_mtime_ns, stat.st_size),
                        safe_id(title_id), width)
        return self._cached_thumb(key, lambda: (path.read_bytes(), ct), width)

    def chapter_video_path(self, title_id: str, chapter_id: str) -> Path | None:
        """The stored episode, or None when this chapter is page media."""
        path = self.vault.chapter_media_path(title_id, chapter_id)
        return path if path is not None and media.is_video(path) else None

    def set_video_duration(self, title_id: str, chapter_id: str, seconds: float) -> TitleOut | None:
        """Record what the player measured. Not a page operation: it describes
        the same bytes, so it must NOT bump the media revision — doing so would
        invalidate every cache of a file that did not change."""
        if seconds <= 0:
            return self.get(title_id)
        with self.vault.title_lock(title_id):
            doc = self.vault.load(title_id)
            if doc is None or not any(c.id == chapter_id for c in doc.chapters):
                return None
            side = dict(self._sidecars(title_id).get(safe_id(chapter_id), {}))
            if not side:
                return self._out_now(title_id, doc)
            known = abs(float(side.get("duration") or 0.0) - seconds) < 0.5
            # Episodes stored before the app looked inside them carry no codec:
            # fill that in HERE, at first play, rather than on a listing (which
            # must touch no files) or on the serving path.
            probe_needed = side.get("kind") == "video" and "codec" not in side
            if known and not probe_needed:
                return self._out_now(title_id, doc)
            if probe_needed:
                path = self.vault.chapter_media_path(title_id, chapter_id)
                if path is not None:
                    probe = media.probe_mp4(path)
                    side["codec"] = probe.get("codec", "")
                    side["faststart"] = probe.get("faststart", False)
            side["duration"] = round(seconds, 3)
            self.vault.write_chapter_sidecar(title_id, chapter_id, side)
            self._index(title_id, doc)
        return self._out_now(title_id, doc)

    # ---- episode stills --------------------------------------------------
    #
    # The app has no video decoder. The WINDOW does: it plays these files, so it
    # can also seek them, draw the frames and hand the bytes back. That happens
    # once per episode — one pass yields both the tile's poster and the contact
    # sheet the title page shows — and from then on they are files in the vault
    # that no tile and no preview ever re-reads from the video stream.

    def save_chapter_frames(self, title_id: str, chapter_id: str, kind: str,
                            data: bytes, grid: str = "") -> bool:
        if kind not in FRAME_KINDS or media.sniff_ext(data) != "jpg":  # a JPEG, or nothing
            return False
        # ONE critical section. Read → change → write of a sidecar outside the
        # title's lock is how a download completing at the same moment gets its
        # `pages`, `size` and `rev` overwritten by a copy that was read before it
        # landed — the chapter would then read as having no media at all.
        with self.vault.title_lock(title_id):
            sidecars = self.vault.chapter_sidecars(title_id)
            stem = safe_id(chapter_id)
            if stem not in sidecars:
                return False
            self.vault.write_chapter_frames(title_id, chapter_id, kind, data)
            side = dict(sidecars[stem])
            # A sheet records the GRID it was cut in. The window slices that one
            # file into tiles, so a sheet cut in another geometry is not a sheet
            # it can read — it says so by not matching, and the episode is re-cut.
            side[kind] = grid if kind == "sheet" else True
            # the STILLS version, not the media revision: see stills_version()
            side["stills"] = int(side.get("stills") or 0) + 1
            self.vault.write_chapter_sidecar(title_id, chapter_id, side)
            self._sidecar_cache.pop(title_id, None)
            doc = self.vault.load(title_id)
            if doc is not None:
                self._index(title_id, doc)
        return True

    def chapter_frames(self, title_id: str, chapter_id: str, kind: str,
                       width: int | None = None) -> tuple[bytes, str] | None:
        if kind not in FRAME_KINDS:
            return None
        path = self.vault.chapter_frames_path(title_id, chapter_id, kind)
        if not path.is_file():
            return None
        try:
            if not width:
                return path.read_bytes(), "image/jpeg"
            key = cache_key(kind, str(path.stat().st_mtime_ns),
                            safe_id(title_id), safe_id(chapter_id), width)
            return self._cached_thumb(key, lambda: (path.read_bytes(), "image/jpeg"), width, None)
        except OSError as e:
            log.warning("%s %s/%s is unreadable: %s", kind, title_id, chapter_id, e)
            return None

    def chapter_pages(self, title_id: str, chapter_id: str) -> list[str] | None:
        path = self.vault.chapter_media_path(title_id, chapter_id)
        if path is None:
            return None
        return media.image_entries(path)

    def chapter_page(self, title_id: str, chapter_id: str, index: int,
                     width: int | None = None, cap: float | None = None) -> tuple[bytes, str] | None:
        """One page, or None when it cannot be served.

        A page that will not decompress (a truncated download, a bad CRC) is a
        MISSING page, not a broken server: one damaged entry must not answer a
        thumbnail request with a traceback, and a grid that asks for eight of
        them must not fill the log eight times over."""
        path = self.vault.chapter_media_path(title_id, chapter_id)
        if path is None:
            return None
        try:
            entries = media.image_entries(path)
            if not (0 <= index < len(entries)):
                return None
            if not width:
                return media.read_entry(path, entries[index])
        except (zipfile.BadZipFile, OSError, KeyError) as e:
            log.warning("page %s/%s#%s is unreadable: %s", title_id, chapter_id, index, e)
            return None
        # keyed by the chapter's own version — every page op bumps it, so an
        # edited chapter misses this cache by construction
        key = cache_key("page", chapter_version(self._sidecars(title_id).get(safe_id(chapter_id))),
                        safe_id(title_id), safe_id(chapter_id), width,
                        f"c{cap:g}" if cap else "", index)
        try:
            return self._cached_thumb(key, lambda: media.read_entry(path, entries[index]), width, cap)
        except (zipfile.BadZipFile, OSError, KeyError) as e:
            log.warning("page %s/%s#%s is unreadable: %s", title_id, chapter_id, index, e)
            return None

    # ---- the sidecar: ONE writer for every page operation ----
    # pages/size describe the file that is actually stored, and `pageKeys` runs
    # PARALLEL to the pages ('' where a page has no source key, e.g. a hand-added
    # image). Every op that moves pages around must move their keys with them,
    # or page capture would think it still holds a page it no longer has.

    def _page_keys(self, title_id: str, chapter_id: str, count: int) -> list[str]:
        """The chapter's page keys, padded/trimmed to `count` so the list always
        lines up with the archive's pages."""
        side = self.vault.chapter_sidecars(title_id).get(safe_id(chapter_id), {})
        keys = [k if isinstance(k, str) else "" for k in (side.get("pageKeys") or [])]
        del keys[count:]
        keys.extend([""] * (count - len(keys)))
        return keys

    def _write_media_sidecar(self, title_id: str, chapter_id: str, path: Path, *,
                             created: bool = False, keys: list[str] | None = None,
                             patch: dict | None = None, defaults: dict | None = None) -> None:
        """Refresh a chapter's sidecar after a page operation. A sidecar that
        doesn't exist yet is seeded with `defaults`, or with a local-import
        provenance (added by hand — no web source). `patch` overwrites: the
        latest source of a page wins."""
        side = self.vault.chapter_sidecars(title_id).get(safe_id(chapter_id), {})
        if created or not side:
            seed = defaults or {"importedFrom": "local"}
            side = {"fileUrl": "", "pageUrl": "", "filename": path.name,
                    "downloadedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    **seed, **side}
        if patch:
            side.update({k: v for k, v in patch.items() if v})
        # The archive is being rewritten anyway, so its mirror is refreshed HERE
        # — before the file is described. Anywhere later would describe bytes
        # that are about to change.
        doc = self.vault.load(title_id)
        row = next((c for c in (doc.chapters if doc else []) if c.id == chapter_id), None)
        if doc is not None and row is not None and not media.is_video(path):
            try:
                data = comicinfo.build(doc, row)
                comicinfo.write_into(path, data)
                side["comicinfo"] = hashlib.sha256(data).hexdigest()[:16]
            except (media.UnsupportedArchiveError, OSError) as e:
                log.warning("comicinfo for %s/%s not written: %s", title_id, chapter_id, e)
        pages = len(media.image_entries(path))
        side["pages"] = pages
        side["size"] = path.stat().st_size
        # every page op rewrites the archive, so the digest is restamped with
        # the revision — a stale one would report healthy content as corrupt
        side["sha256"] = media.digest_of(path)
        side["content"] = media.content_digest(path)
        # The version every cache of this chapter's pages is keyed by. A COUNTER,
        # not a file timestamp: page count says nothing (delete two, add two) and
        # an mtime can be carried over by a copy or rounded off by a filesystem.
        side["rev"] = int(side.get("rev") or 0) + 1
        if keys is not None:
            del keys[pages:]
            keys.extend([""] * (pages - len(keys)))
            side["pageKeys"] = keys
        self.vault.write_chapter_sidecar(title_id, chapter_id, side)

    def add_chapter_pages(self, title_id: str, chapter_id: str,
                          files: list[tuple[bytes, str]]) -> TitleOut | None:
        """Append loose images to an entry's archive — created on first add, so
        a bare row becomes an image set."""
        with self.vault.title_lock(title_id):
            doc = self.vault.load(title_id)
            if doc is None or not any(c.id == chapter_id for c in doc.chapters):
                return None
            path = self.vault.chapter_media_path(title_id, chapter_id)
            created = path is None
            keys = [] if created else self._page_keys(title_id, chapter_id, len(media.image_entries(path)))
            if path is None:
                path = self.vault.chapter_archive_target(title_id, chapter_id)
            media.renumber_and_append(path, files)
            # hand-added images carry no source key — the blanks keep the list
            # aligned so a later capture can still tell its own pages apart
            self._write_media_sidecar(title_id, chapter_id, path, created=created,
                                      keys=[*keys, *([""] * len(files))])
            self._index(title_id, doc)
        return self._out_now(title_id, doc)

    def reorder_chapter_pages(self, title_id: str, chapter_id: str, order: list[int]) -> TitleOut | None:
        """Rearrange the pages inside an entry's archive (raises ValueError on a
        bad permutation)."""
        with self.vault.title_lock(title_id):
            doc = self.vault.load(title_id)
            path = self.vault.chapter_media_path(title_id, chapter_id)
            if doc is None or path is None:
                return None
            keys = self._page_keys(title_id, chapter_id, len(media.image_entries(path)))
            media.reorder_entries(path, order)
            self._write_media_sidecar(title_id, chapter_id, path,
                                      keys=[keys[i] for i in order if 0 <= i < len(keys)])
            self._index(title_id, doc)
        return self._out_now(title_id, doc)

    def move_chapter_pages(self, title_id: str, src_id: str, dst_id: str,
                           indices: list[int]) -> TitleOut | None:
        """Move selected pages between two entries' archives; the target archive
        is created when the row has none. An emptied source archive is removed
        (the row stays)."""
        with self.vault.title_lock(title_id):
            doc = self.vault.load(title_id)
            ids = {c.id for c in doc.chapters} if doc else set()
            if doc is None or src_id not in ids or dst_id not in ids or src_id == dst_id:
                return None
            src = self.vault.chapter_media_path(title_id, src_id)
            if src is None:
                return None
            entries = media.image_entries(src)
            moved = sorted({i for i in indices if 0 <= i < len(entries)})
            names = {entries[i] for i in moved}
            if not names:
                return self._out_now(title_id, doc)
            dst = self.vault.chapter_media_path(title_id, dst_id)
            created = dst is None
            if dst is None:
                dst = self.vault.chapter_archive_target(title_id, dst_id)
            # the pages' keys travel WITH them, or the source would keep claiming
            # pages it gave away and the target would re-capture them
            src_keys = self._page_keys(title_id, src_id, len(entries))
            dst_keys = [] if created else self._page_keys(title_id, dst_id, len(media.image_entries(dst)))
            src_left, _ = media.move_images(src, dst, names)
            self._write_media_sidecar(title_id, dst_id, dst, created=created,
                                      keys=[*dst_keys, *[src_keys[i] for i in moved]])
            if src_left == 0:
                self.vault.delete_chapter_media(title_id, src_id)
            else:
                self._write_media_sidecar(title_id, src_id, src,
                                          keys=[k for i, k in enumerate(src_keys) if i not in set(moved)])
            self._index(title_id, doc)
        return self._out_now(title_id, doc)

    def delete_chapter_pages(self, title_id: str, chapter_id: str, indices: list[int]) -> TitleOut | None:
        with self.vault.title_lock(title_id):
            doc = self.vault.load(title_id)
            path = self.vault.chapter_media_path(title_id, chapter_id)
            if doc is None or path is None:
                return None
            entries = media.image_entries(path)
            gone = sorted({i for i in indices if 0 <= i < len(entries)})
            doomed = {entries[i] for i in gone}
            if doomed:
                keys = self._page_keys(title_id, chapter_id, len(entries))
                left = media.remove_entries(path, doomed)
                # nothing left AND nothing else inside — drop the archive, like
                # move does; a downloaded-but-empty chapter is a lie in the UI.
                # An archive still holding a translator's files is kept.
                if left == 0 and not media.junk_entry_names(path):
                    self.vault.delete_chapter_media(title_id, chapter_id)
                else:
                    self._write_media_sidecar(
                        title_id, chapter_id, path,
                        keys=[k for i, k in enumerate(keys) if i not in set(gone)])
            self._index(title_id, doc)
        return self._out_now(title_id, doc)

    def delete(self, title_id: str) -> bool:
        # the index entry goes even if the directory could not be fully removed,
        # or the library would keep serving a title whose files are half-gone
        try:
            return self.vault.delete(title_id)
        finally:
            self.index.remove(title_id)
            self._sidecar_cache.pop(title_id, None)
