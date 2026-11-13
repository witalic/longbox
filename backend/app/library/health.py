"""The vault's health: what has been done to the library, and the one check
that asks whether it is still whole (ARCHITECTURE.md, "Integrity, and the passes that
check it").

Every pass that walks the library leaves a line in the record (`health.json`),
whoever runs it; the check answers three questions in one walk — is anything
broken, is anything wasting space, is anything missing — and sweeping the
leftovers it found is the one thing here that deletes.
"""
from __future__ import annotations

import threading
import time
import zipfile
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from . import media
from .models import ChapterRow, TitleDoc, chapter_num_key
from .vault import derived_owner, safe_id

if TYPE_CHECKING:
    from .service import Library


# How a long pass reports itself: (titles done, titles in the library).
Progress = Callable[[int, int], None] | None


def cut_short(stop: threading.Event | None, seen: int, total: int) -> bool:
    """Whether a pass was cut short. A report that covered part of the library
    must say so — "nothing wrong" is a very different claim from "nothing wrong
    in the first two hundred"."""
    return bool(stop is not None and stop.is_set() and seen < total)


def _missing_numbers(nums: list[str], min_rows: int, numeric: float,
                     density: float) -> list[int]:
    """The integers absent from a run of labels — or nothing at all, if this is
    not provably a run. `chapter_num_key` already owns what "a number in a
    label" means; this reads the same leading value and refuses everything else.

    Fractions never count as a gap (10.5 is an extra, not a missing 11) but they
    do count as entries held: a scope that is half extras is still a sequence."""
    if len(nums) < min_rows:
        return []
    values = [key[1] for key in map(chapter_num_key, nums) if key[0] == 0]
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


def _finding(title_id: str, doc: TitleDoc, ch: ChapterRow, kind: str, detail: str) -> dict:
    """One problem, named by the thing a human would go looking for: the work
    and the entry, never the path on disk."""
    return {"titleId": title_id, "title": doc.meta.title, "chapterId": ch.id,
            "num": ch.num, "lang": ch.lang, "group": ch.group,
            "kind": kind, "detail": detail}




class Health:
    def __init__(self, lib: Library) -> None:
        # each is made once, with the library, and lives as long as it does
        self.vault = lib.vault
        self.index = lib.index
        self.copies = lib.copies

    # ---- the record of what has been done ------------------------------------
    #
    # Every operation that touches the vault leaves a line: when it ran, how
    # long it took, and what came of it in one sentence. Without it a report is
    # a thing you have to re-earn every time you open the panel, and "has this
    # library ever been checked" has no answer at all.

    def record(self, op: str, started: float, outcome: str, *, stopped: bool = False,
               last_check: dict | None = None) -> None:
        """A line in what has been done to this library — every pass that walks
        it leaves one, here or elsewhere (service.py, operations.py)."""
        self.vault.write_health(
            entry={"op": op, "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   "seconds": round(time.monotonic() - started, 1),
                   "outcome": outcome, "stopped": stopped},
            last_check=last_check)

    def read(self) -> dict:
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
        docs = self.index.docs()
        total = len(docs)
        if progress is not None:
            progress(0, total)
        for done, row in enumerate(docs, 1):
            if stop is not None and stop.is_set():
                return
            yield row
            if progress is not None:
                progress(done, total)

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
        for title_id, doc in self._walk(progress, stop):
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
                "stopped": cut_short(stop, seen, self.index.count()),
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
                if self.copies.writing(p):
                    continue  # a playable copy being written right now, not a leftover
                out.append({"titleId": title_id, "title": doc.meta.title, "chapterId": "",
                            "num": "", "lang": "", "group": "", "kind": "stray",
                            "name": name, "bytes": _size_of(p),
                            "detail": f"{name} — an interrupted write"})
                continue
            if p.suffix == ".json":
                continue  # a sidecar with no row is reported as an orphan above
            stem = derived_owner(name) or p.stem
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
        for title_id, doc, sidecars, _covers in rows:
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
                "stopped": cut_short(stop, seen, total)}

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
            docs = list(self._walk(progress, stop))
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
        self.record(name, started, _check_outcome(out), stopped=out["stopped"], last_check=out)
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
        scope = (list(title_ids) if title_ids is not None
                 else [tid for tid, _d in self.index.docs()])
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
        stopped = cut_short(stop, seen, total)
        self.record("delete leftovers", started,
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
