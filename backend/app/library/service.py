"""The library service ties the on-disk vault (source of truth) to the SQLite
index (rebuildable cache). Reads go through the index; writes go to the vault
first, then the index — so a re-scan of disk reproduces the exact same state.
The service also composes the flat wire DTO from the layered documents.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import threading
import time
import uuid
import zipfile
from collections import Counter
from collections.abc import Callable, Iterator
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from ..scraper.recipes import RecipeStore
from ..scraper.sources import SourcePrefs
from . import cards, comicinfo, copies, fields, media, previews, subtitles
from .app_docs import AppSettings, AppState
from .health import Health, Progress, cut_short
from .loose import LooseStore
from . import rules as rules_mod
from .index import LibraryIndex
from .pages import PageStore, excerpt_of, image_url, vocab_of
from .pages import key as page_key
from .rules import RuleStore
from .models import (
    AuthorWork, BrowseGroup, ChapterRow, DraftIn, Source, TitleDoc, TitleOut, UserPatch,
    chapter_num_key,
)
from .vault import APP_DIR, FRAME_KINDS, UnreadableDocument, Vault, safe_id
from .versions import cache_key, chapter_version, cover_version, page_image_version, stills_version


log = logging.getLogger("longbox.library")


# Whether the last session ended settled: written open at launch, closed on
# the way out — only when the launch's check finished, every index write landed
# and no write was still on its way — with the index file as it was left. Still
# open at the next launch, or the index changed since (an older build, which
# keeps no marker, opened the vault), the index may be missing a write: only
# then is the whole vault verified.
SESSION_DOC = f"{APP_DIR}/session.json"
SESSION_SCHEMA = 1


def _stamp_of(path: Path) -> str:
    try:
        st = path.stat()
    except OSError:
        return ""
    return f"{st.st_mtime_ns}.{st.st_size}"


# What a preview URL's `v=` may be — the versions this sidecar hands out: a
# cover's mtime.size.ext, a chapter's rev.size.pages[.cSIZE.MADE], an episode's
# stills count[.digest]. A client's version is only a cache key to TRY first;
# anything else goes the long way, and it is checked so a version can never
# carry a path into the cache's file name.
_V_COVER = re.compile(r"[0-9a-f]{1,20}\.\d{1,20}\.[A-Za-z0-9]{1,8}")
_V_CHAPTER = re.compile(r"\d{1,20}\.\d{1,20}\.\d{1,20}(\.c\d{1,20}\.\d{1,20})?")
_V_STILLS = re.compile(r"\d{1,20}(\.[0-9a-f]{12})?")


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


def _place_copy(src: Path, dst: Path, link: bool) -> None:
    """A copy of `src` at `dst`. With `link`, a hard link wherever the volume
    allows one: the same bytes under a second name, so a file moved onto the
    vault's own drive is never copied — and the original still exists until
    whoever asked for the move deletes it.

    Never a link to a read-only file: a link shares its attributes, and a
    read-only file in the vault is one no atomic rewrite can replace."""
    if link and os.access(src, os.W_OK):
        try:
            os.link(src, dst)
            return
        except OSError:
            pass
    shutil.copyfile(src, dst)


def _free_id(cid: str, taken: set[str]) -> str:
    """`cid`, or the first variant of it nobody holds.

    An id is derived from the number a chapter was CREATED under, and then never
    again — the media file is named after it. Renumber that chapter and it keeps
    the id anyway, so the number it freed up derives an id somebody else is
    still using. The row is not a duplicate and must not be treated as one: give
    it a name of its own. Ids are opaque; all they owe anyone is uniqueness and
    never changing afterwards."""
    if cid not in taken:
        return cid
    n = 2
    while f"{cid}-{n}" in taken:
        n += 1
    return f"{cid}-{n}"


class Library:
    def __init__(self, root: Path, *, defer_sync: bool = False) -> None:
        self.vault = Vault(root)
        # the registry is per-library: point it at THIS vault's definitions
        fields.set_custom(self.vault.custom_fields())
        # …and so is what it knows about its sites, and how its owner keeps them
        self.recipes = RecipeStore(self.vault)
        self.source_prefs = SourcePrefs(self.vault)
        self.rules = RuleStore(self.vault)
        self.pages = PageStore(self.vault)
        self.loose = LooseStore(self.vault)
        # …and what the app itself keeps: nothing of it stays on this computer
        self.settings = AppSettings(self.vault)
        self.state = AppState(self.vault)
        # the index file as this session found it — the session marker's check
        self._index_found = _stamp_of(root / "index.db")
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
        # before anything indexes a title: every index write offers its episodes
        self.copies = copies.CopyQueue(self, self._closing)
        self.vault.let_go = self.copies.release
        # …and its tile preview, made ahead of the grid that will ask for it
        self.previews = previews.Previews(self, self._closing)
        # what has been done to the library, and whether it is still whole
        self.health = Health(self)
        # What the index already holds needs no disk at all, so the library is
        # SERVED FIRST and verified after: the scan below only answers "did
        # anything change on disk", and nothing has to wait for that answer.
        # (It cannot be skipped: the vault is the source of truth, and files can
        # be dropped in, edited or deleted while the app is closed — or by
        # another machine, on a shared drive.)
        self.sync_state = {"running": False, "done": 0, "total": 0, "changed": 0}
        self._session_marked = False  # sync_in_background marks a serving session open
        self._settled = False  # ...and its check finished
        self._closed = False
        self._sync_thread: threading.Thread | None = None
        if not defer_sync:
            self.sync()
        # The zip invariant is enforced at INGEST, so the archive sweep is a
        # one-time migration for pre-invariant (or hand-dropped) content: it
        # runs only for a vault that never had it, off the startup path (large
        # repacks are slow), and marks the vault when done. Settings re-runs it.
        self._normalize_thread: threading.Thread | None = None
        # Reported with the sync: a window opened while a pass runs has already
        # drawn what the pass is rewriting, and only this says when to ask again.
        self.migration_state = {"running": False, "changed": 0}
        if self.vault.needs_normalize() or self.vault.needs_faststart():
            self.migration_state["running"] = True
            self.copies.hold()  # until the pass has said what each episode is
            self._normalize_thread = threading.Thread(
                target=self._migrations_bg, name="lb-migrate", daemon=True)
            self._normalize_thread.start()

    def _migrations_bg(self) -> None:
        """One-time vault passes, in sequence — never in parallel: both walk the
        whole vault, and on a network share two sweeps only get in each other's
        way."""
        changed = 0
        try:
            if self.vault.needs_normalize():
                changed += max(0, self.normalize_archives())
            if self.vault.needs_faststart():
                changed += self.refresh_episodes()
        except Exception:  # noqa: BLE001 — a failed pass must never take the app down
            log.exception("one-time vault pass failed")
        finally:
            self.migration_state.update(running=False, changed=changed)
            self.copies.resume()

    def refresh_episodes(self) -> int:
        """Bring stored episodes up to what the app records about them, once per
        vault. Anything arriving now gets it at ingest."""
        # the index knows every episode already; an empty one (a first open, a
        # dropped cache) knows nothing, and the vault is walked instead
        known = self.index.episodes() if self.index.count() else None
        changed, touched = self.vault.refresh_stored_episodes(known, stop=self._closing)
        if not self._closing.is_set():
            self.vault.mark_faststart()
        self._reindex(touched)
        return changed

    def _reindex(self, title_ids: list[str], stop: threading.Event | None = None) -> None:
        """Refresh the index rows for titles a PASS just rewrote.

        Not `sync()`. Sync exists to find changes nobody announced — a file
        dropped into the vault by hand — and it finds them by comparing
        timestamps. A filesystem records those at the resolution of a clock
        tick (~15ms on Windows, a whole second on some shares), so a pass whose
        write lands in the same tick as the row it is replacing is invisible to
        it: the episode gets repaired on disk and the app goes on describing the
        broken one. A pass knows exactly which titles it touched. There is
        nothing to detect."""
        for tid in title_ids:
            if self._closing.is_set() or (stop is not None and stop.is_set()):
                return
            with self.vault.title_lock(tid):
                doc = self.vault.load(tid)
                if doc is not None:
                    self._index(tid, doc)

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
        ids = [tid for tid, _doc in self.index.docs()] or None
        if progress is not None:
            progress(0, len(ids or []))
        try:
            changed, touched = self.vault.normalize_chapter_archives(
                force=force, progress=progress, ids=ids, stop=stop or self._closing)
            if not self._closing.is_set():
                self.vault.mark_normalized()
            self._reindex(touched, stop)
            self.health.record("convert archives", started,
                         f"{changed} converted" if changed else "everything already zip")
            return changed
        finally:
            self._normalize_lock.release()

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
        stopped = cut_short(stop, seen, total)
        self.health.record("update metadata", started,
                     f"{changed} archive(s) updated" if changed else "already up to date",
                     stopped=stopped)
        return {"written": changed, "stopped": stopped}

    @property
    def root(self) -> Path:
        return self.vault.root

    def sync_in_background(self) -> None:
        """Bring the index in line with the vault without holding anything up.

        The index lives in the vault and every write of longbox — on any
        computer — updates it, so a launch only checks what a write of its own
        cannot have told it: titles added or removed by hand (one listing per
        shelf). The whole vault is verified — a listing of every title's
        folder, minutes on a network disk — only when the index is empty, or
        the last session never closed and may have missed a write. An edit
        made by hand inside a title's folder waits for Rebuild (Maintenance)."""
        if self._sync_thread is not None and self._sync_thread.is_alive():
            return
        self.sync_state.update(running=True, done=0, total=0, changed=0)

        def run() -> None:
            try:
                if self._session_open() or self.index.count() == 0:
                    self.sync(lambda done, total: self.sync_state.update(done=done, total=total))
                    settled = True
                else:
                    settled = self.quick_sync()
                self.copies.fill()  # the queue is not kept across launches: offered again from the index
                # a check a close cut short is owed again: the session stays open
                self._settled = settled and not self._closing.is_set()
            except Exception:  # noqa: BLE001 — a failed verification must not take the app down
                log.exception("background sync failed")
            finally:
                self.sync_state["running"] = False
            # the tile previews content stored before writes made them — once per
            # vault, and only after the verification, never competing with it
            if self.vault.needs_previews():
                try:
                    self.previews.backfill()
                except Exception:  # noqa: BLE001 — a preview pass must not take the app down
                    log.exception("making the tile previews failed")

        self._sync_thread = threading.Thread(target=run, name="lb-sync", daemon=True)
        self._sync_thread.start()

    def _session_open(self) -> bool:
        """Mark this session open; say whether the index may be missing a write:
        the last session never settled, left no marker (an older build), or the
        index file changed after it was left."""
        try:
            last = self.vault.read_doc(SESSION_DOC)
            settled = (last.get("open") is False and bool(self._index_found)
                       and last.get("index") == self._index_found)
            with self.vault.doc_transaction(SESSION_DOC, schema=SESSION_SCHEMA) as doc:
                doc["open"] = True
                doc.pop("index", None)
            self._session_marked = True
            return not settled
        except Exception:  # noqa: BLE001 — a marker that cannot be read or written: verify
            return True

    def _session_close(self, settled: bool) -> None:
        """After the index is closed: the session settled, and the file it left."""
        if not (settled and self._session_marked and self._settled) or self.index.missed:
            return  # left open: the next launch verifies the whole vault
        try:
            with self.vault.doc_transaction(SESSION_DOC, schema=SESSION_SCHEMA) as doc:
                doc["open"] = False
                doc["index"] = _stamp_of(self.vault.root / "index.db")
        except Exception:  # noqa: BLE001 — the next launch then verifies, which is the safe side
            log.warning("the session could not be marked closed")

    def quick_sync(self) -> bool:
        """Titles added or removed by hand while the app was closed: the shelves
        listed, the new titles read, the gone ones dropped. Says whether every
        shelf was seen."""
        started = time.perf_counter()
        # the index first: a title created while the shelves are listed is then
        # in neither, never "gone"
        indexed = self.index.ids()
        on_disk, complete = self.vault.title_dirs()
        # a shelf that could not be listed is not a shelf emptied
        missing = indexed - on_disk if complete and on_disk else set()
        new = on_disk - indexed
        gone = 0
        for tid in missing:
            # …and one moved to another shelf mid-listing is still there
            with self.vault.title_lock(tid):
                if not self.vault.exists(tid):
                    self.index.remove(tid)
                    gone += 1
        added = 0
        for tid in sorted(new):
            if self._closing.is_set():
                break
            with self.vault.title_lock(tid):
                try:
                    doc = self.vault.load(tid)
                except Exception:  # noqa: BLE001 — one malformed title.json never blocks a launch
                    continue
                if doc is not None:
                    self._index(tid, doc)
                    added += 1
        self.sync_state["changed"] = added + gone
        log.info("opened %s quietly: %d titles on the shelves, %d new, %d gone (%.1fs)",
                 self.vault.root, len(on_disk), added, gone, time.perf_counter() - started)
        return complete

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
        on_disk = {tid: (doc_at, ch_at, self._covers_of(tid, stamps))
                   for tid, (doc_at, ch_at, stamps) in scanned.items()}
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
        DELETE + INSERT of the whole table: each row is written only while it
        stands as it did when this pass began, and a title committed, made or
        deleted meanwhile keeps what that write left (`LibraryIndex.rebuild`)."""
        started = time.monotonic()
        if progress is not None:
            progress(0, self.index.count())  # the scan below is the slow part
        held = self.index.revisions()  # before anything is read: a write after it is newer
        scanned = self.vault.scan()
        ids = sorted(scanned)
        docs = {}
        touched = {}
        media = {}
        media_at = {}
        covers = {}
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
                doc_at, ch_at, stamps = scanned[tid]
                touched[tid] = doc_at
                # off the disk, not the cache: a sidecar edited in place moves no
                # stamp the cache is keyed by, and a change made by hand is what
                # a rebuild is for
                self._sidecar_cache.pop(tid, None)
                media[tid] = self._sidecars(tid)
                media_at[tid] = ch_at
                covers[tid] = self._covers_of(tid, stamps)
            if progress:
                progress(i + 1, len(ids))
        self.index.rebuild(held, docs, touched, media, media_at, covers)
        self.health.record("rebuild index", started, f"{len(docs)} title(s) re-read")

    def lose(self) -> None:
        """The vault stopped answering (app/vault_watch.py). Everything that works
        on it in the background stops where it is — the verification, the
        one-time passes, a playable copy's ffmpeg — and nothing picks up again in
        this process: the app restarts into the vault once it answers."""
        self._session_marked = False  # a write may have been dropped: the next launch verifies
        self._stop_work()

    def closing(self) -> None:
        """The app is on its way out: every pass of the library stops at its next step."""
        self._closing.set()

    def _stop_work(self) -> bool:
        """Stop the background work; say whether its writers have stopped."""
        self._closing.set()
        stopped = self.copies.close()
        self.previews.close()  # it writes previews only, never the index
        for thread in (self._normalize_thread, self._sync_thread):
            if thread is not None and thread.is_alive():
                thread.join(timeout=10)
                stopped = stopped and not thread.is_alive()
        return stopped

    def close(self, *, settled: bool = True) -> None:
        """Stop the background work, close the index, and mark the session
        closed. `settled=False`: a write may still be on its way (the sidecar
        could not wait it out), so the session is left open."""
        if self._closed:
            return
        self._closed = True
        stopped = self._stop_work()
        self.index.close()
        self._session_close(settled and stopped)

    # ---- DTO composition ----

    @classmethod
    def _covers_of(cls, title_id: str, stamps: tuple[tuple[str, int, int], ...]) -> str:
        """A title's cover endpoint URLs as the index stores them: space-separated
        and each versioned, so a re-captured cover busts every cache. Composed
        from a scan, never from a fresh stat. The first cover keeps the URL it
        has always had — a slot only names itself once there is a second one."""
        out = []
        for slot, (name, mtime, size) in enumerate(stamps):
            v = cover_version(name, mtime, size)
            if v:
                out.append(f"/api/titles/{title_id}/cover?v={v}"
                           + (f"&i={slot}" if slot else ""))
        return " ".join(out)

    def _covers_url(self, title_id: str) -> str:
        """The same URLs for ONE title, straight after a write."""
        stamps = []
        for path in self.vault.cover_paths(title_id):
            try:
                stat = path.stat()
            except OSError:
                # A position is what `&i=` means. Skipping one would renumber
                # every cover after it; stopping keeps the ones before it right.
                break
            stamps.append((path.name, stat.st_mtime_ns, stat.st_size))
        return self._covers_of(title_id, tuple(stamps))

    def _indexed(self, title_id: str, doc: TitleDoc, sidecars_moved: bool = True) -> TitleOut:
        """Index a write and answer with the title as that read it: a write
        reads the chapter sidecars and the covers ONCE, not again for its answer."""
        sides, covers = self._index(title_id, doc, sidecars_moved)
        return self._out(title_id, doc, sides, covers)

    def _index(self, title_id: str, doc: TitleDoc,
               sidecars_moved: bool = True) -> tuple[dict[str, dict], str]:
        """Refresh the index for one title. ALWAYS called inside that title's
        lock: a doc read in a critical section and indexed after it is released
        can overwrite a newer writer's row with stale data. The chapter sidecars
        are read HERE, once per write, so no listing ever has to touch them."""
        # The write that just happened may not have moved the chapters directory
        # far enough for an mtime-keyed cache to notice (two changes can share one
        # timestamp), and a listing composed from a stale sidecar shows a chapter
        # that is no longer there. After a write the cache is simply wrong —
        # unless the write is one that touches no sidecar.
        if sidecars_moved:
            self._sidecar_cache.pop(title_id, None)
        try:
            # stamp BEFORE reading: a change that lands in between then looks
            # newer than the row, so the next verification re-reads it — the
            # other order would store fresh stamps over stale content
            stamp = self.vault.chapters_stamp(title_id)
            sides = self._sidecars(title_id)
            covers = self._covers_url(title_id)
            self.index.upsert(title_id, doc, self.vault.touched_at(title_id), sides, stamp, covers)
        except BaseException:
            self.index.missed = True  # the vault holds a write the index does not
            raise
        # whatever just arrived or changed, an episode the window cannot show
        # goes to the playable-copy queue from here — the one place every write passes
        self.copies.offer(title_id, doc, sides)
        self.previews.offer(title_id, self.tile_source(doc, sides, covers))
        return sides, covers

    def _sidecars(self, title_id: str) -> dict[str, dict]:
        """Chapter sidecars, cached per title against the chapters dir's mtime.
        A library listing composes every title's DTO, and re-globbing + parsing
        one JSON file per chapter each time is what makes a big library crawl."""
        d = self.vault.chapters_dir(title_id)
        try:
            stamp = d.stat().st_mtime_ns
        except FileNotFoundError:  # nothing downloaded; any other failure is no answer
            self._sidecar_cache.pop(title_id, None)
            return {}
        hit = self._sidecar_cache.get(title_id)
        if hit is not None and hit[0] == stamp:
            return hit[1]
        loaded = self.vault.chapter_sidecars(title_id)
        self._sidecar_cache[title_id] = (stamp, loaded)
        return loaded

    def _out_held(self, title_id: str, doc: TitleDoc) -> TitleOut:
        """A title whose chapters and covers nothing changed: composed from what
        the index holds of them — no sidecar read again, no cover listed."""
        held = self.index.media_of(title_id)
        if held is None:
            return self._out_now(title_id, doc)
        sides, _at, covers = held
        return self._out(title_id, doc, sides, covers)

    def _out_now(self, title_id: str, doc: TitleDoc) -> TitleOut:
        """One title composed right after a write, when the sidecars on disk are
        newer than whatever the index row still carries."""
        self._sidecar_cache.pop(title_id, None)  # the write is newer than any cache
        return self._out(title_id, doc, self._sidecars(title_id), self._covers_url(title_id))

    def _out(self, title_id: str, doc: TitleDoc, sidecars: dict[str, dict], covers: str) -> TitleOut:
        return cards.title_out(title_id, doc, sidecars, covers, self.index.rev_of(title_id))

    # ---- tile previews, made ahead (library/previews.py) ----

    @staticmethod
    def tile_source(doc: TitleDoc, sidecars: dict[str, dict], covers: str) -> previews.Source | None:
        """What a title's tile shows first, with the version it is cached under
        — read off what the index holds, so its key needs no disk."""
        own = covers.split()
        if own:
            return ("cover", "", parse_qs(urlsplit(own[0]).query).get("v", [""])[0])
        stand_ins = cards.stand_in_sources(doc, sidecars)
        if not stand_ins:
            return None
        kind, chapter_id, side = stand_ins[0]
        if kind == "poster":
            return ("poster", chapter_id, stills_version(side))
        return ("page", chapter_id, chapter_version(side))

    def tile_sources(self) -> Iterator[tuple[str, previews.Source]]:
        """Every title's, from the index — no disk."""
        for title_id, doc, sides, covers in self.index.query(sort="title"):
            source = self.tile_source(doc, sides, covers)
            if source is not None:
                yield title_id, source

    def thumb_names(self) -> set[str]:
        """Every preview the cache holds — ONE listing, where a stat per title
        would be thousands of round trips to a network vault."""
        try:
            with os.scandir(self._thumb_file("x").parent) as items:
                return {e.name for e in items}
        except OSError:
            return set()

    def tile_preview(self, title_id: str, source: previews.Source, made: set[str] | None = None) -> None:
        """Make the preview a tile shows first, unless it is made: a lookup in
        `made` (the cache listed once) or one stat — never the preview read back."""
        kind, chapter_id, version = source
        w = previews.TILE_W
        if version:
            # the first cover's file is `cover.<ext>` (vault._cover_name): its stem is in the key
            key = (self._cover_key(title_id, version, "cover", w) if kind == "cover"
                   else self._frames_key(kind, title_id, chapter_id, version, w) if kind == "poster"
                   else self._page_key(title_id, chapter_id, version, w, None, 0))
            name = f"{key}.jpg"
            if (name in made) if made is not None else self._thumb_file(key).is_file():
                return
        if kind == "cover":
            self.cover_thumb(title_id, w)
        elif kind == "page":
            self.chapter_page(title_id, chapter_id, 0, w)
        else:
            self.chapter_frames(title_id, chapter_id, "poster", w)

    # ---- reads (via the index) ----

    def cards(self, **selection) -> str:
        """The listing: the cards of the titles a selection holds, as ONE JSON
        array — kept ready by the index, so a listing parses no document."""
        return "[" + ",".join(self.index.cards(**selection)) + "]"

    def get(self, title_id: str) -> TitleOut | None:
        row = self.index.get(title_id)
        return self._out(title_id, *row) if row else None

    def get_many(self, ids: list[str]) -> list[TitleOut]:
        """Whole titles, in the order asked; one that is gone is left out."""
        return [t for t in (self.get(tid) for tid in dict.fromkeys(ids)) if t is not None]

    def facets(self, selection: dict | None = None) -> dict[str, list[dict]]:
        return self.index.facet_counts(selection or {})

    def retype_candidates(self, field_id: str, to_type: str, join: str = ", ") -> list[str]:
        """Which titles hold a value of the wrong shape for this field.

        Decided from the INDEX, which already carries every document — so a save
        that changes nothing costs no disk at all. The value is checked again
        under the lock before anything is written; this only says where to look.
        """
        sep = join or ", "
        return [tid for tid, doc in self.index.docs()
                if _retyped(doc.meta.custom.get(field_id), to_type, sep) is not None]

    def clear_field(self, field_id: str, progress: Progress = None,
                    stop: threading.Event | None = None) -> dict:
        """Remove a field's stored values from every title that holds one.

        The scope comes from the index, so only titles that actually hold a
        value are opened. Nothing else about them is touched — this is the
        second half of "remove the field", for when you mean the data too."""
        started = time.monotonic()
        scope = [tid for tid, doc in self.index.docs()
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
        stopped = cut_short(stop, seen, total)
        self.health.record(f"clear {field_id}", started,
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
        for tid, doc in self.index.docs():
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
        stopped = cut_short(stop, seen, total)
        self.health.record(f"retype {field_id}", started,
                     f"{changed} title(s) converted" if changed else "nothing to convert",
                     stopped=stopped)
        return {"converted": changed, "stopped": stopped}

    def field_usage(self) -> dict[str, int]:
        """How many titles hold a value in each field, over the WHOLE library.

        Counted here rather than in the browser, which only ever has the page it
        is currently showing: reading the count off that made every field report
        whatever the library happened to be filtered to."""
        used: dict[str, int] = {f.id: 0 for f in fields.registry()}
        for _tid, doc in self.index.docs():
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

        `authors` is not a separate code path — it is this, plus the one thing
        only people have: the favourite mark."""
        f = fields.by_id().get(field_id)
        if f is None or f.type != "list":
            return []
        people = field_id == "authors"
        # Filtering a browse means "which groups survive, and what is left inside
        # them" — so it is the same selection the library is under, applied to the
        # titles BEFORE they are grouped. A group with nothing left disappears.
        # Grouped off the stored values, each title shown by its card.
        agg: dict[str, dict] = {}
        shown: dict[str, dict] = {}
        for value, tid, card in self.index.grouped(field_id, **(selection or {})):
            name = value.strip()
            if not name:
                continue
            g = agg.setdefault(name, {"works": {}, "chapters": 0, "tags": Counter()})
            if tid not in g["works"]:
                c = shown.get(tid) or shown.setdefault(tid, json.loads(card))
                g["works"][tid] = AuthorWork(id=tid, title=c["title"], cover=c["cover"])
                g["chapters"] += c["chapterCount"]
                g["tags"].update(c["tags"])
        favs = self.vault.author_favorites() if people else set()
        vocab = vocab_of(field_id)
        try:
            written = self.pages.pages(vocab) if vocab else {}
        except UnreadableDocument:
            written = {}  # the page itself says what is wrong with the file
        out: list[BrowseGroup] = []
        for name, g in sorted(agg.items()):
            gid = safe_id(name)
            page = written.get(page_key(name))
            out.append(BrowseGroup(
                id=gid, field=field_id, value=name, fav=people and gid in favs,
                works=list(g["works"].values()), titles=len(g["works"]),
                chapters=g["chapters"], topTags=[t for t, _ in g["tags"].most_common(5)],
                described=page is not None, excerpt=excerpt_of(page.desc) if page else "",
                image=image_url(field_id, name, page.image) if page else ""))
        return out

    def set_author_favorite(self, author_id: str, value: bool) -> bool:
        """Write-through favorite mark; True when the author currently exists."""
        self.vault.set_author_favorite(author_id, value)
        return any(g.id == author_id for g in self.browse("authors"))

    def sources(self) -> list[Source]:
        """Sites aggregated from the titles' source bindings. Recipe detail is
        joined on in the router (the recipe store is app-level state)."""
        agg = self.index.source_counts()
        return [
            Source(id=safe_id(domain), domain=domain, homepage=f"https://{domain}/", titles=n)
            for domain, n in sorted(agg.items())
        ]

    # ---- writes (vault first; index follows) ----

    def create(self, draft: DraftIn) -> TitleOut:
        """Commit a NEW draft, assigning a unique filesystem-safe id."""
        rules, fired = self.incoming_rules(), []
        for c in draft.chapters:
            c.lang, c.group = self.ruled_identity(rules, c.lang, c.group,
                                                  c.url or draft.meta.source.url, fired)
            c.section = self.ruled_section(rules, c.section, c.url or draft.meta.source.url, fired)
        self._count_fired(fired)
        with self._create_lock:
            base = safe_id(draft.meta.title or "untitled")
            tid, n = base, 2
            while self.vault.exists(tid):
                tid, n = f"{base}-{n}", n + 1
            doc = self.vault.commit_meta(tid, draft, create=True)
            assert doc is not None
            with self.vault.title_lock(tid):  # the rule holds for the first write too
                return self._indexed(tid, doc)

    def commit(self, title_id: str, draft: DraftIn) -> TitleOut | None:
        """Commit a draft into an existing title. Replaces the meta layers,
        preserves the user layer (enforced in the vault)."""
        # ONE critical section: reconciling against a doc another writer is
        # about to replace would resurrect or drop rows
        with self.vault.title_lock(title_id):
            old = self.vault.load(title_id)
            if old is None:
                return None
            self._reconcile_chapters(title_id, old, draft)
            doc = self.vault.commit_meta(title_id, draft, existing=old)
            if doc is None:
                return None
            # a meta commit writes no sidecar: the ones just read still hold
            return self._indexed(title_id, doc, sidecars_moved=False)

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

        # Who currently answers to each id, and as WHAT. A row keeps the id it
        # was created under for life (its media file is named after it), so an
        # id derived from a number says nothing about the number its holder
        # wears today.
        held = {c.id: key(c) for c in old.chapters}
        held.update({c.id: key(c) for c in draft.chapters if c.id})

        rules, fired = self.incoming_rules(), []
        rows, taken = [], set()
        for c in draft.chapters:
            raw = (c.lang, c.group)
            if not c.id:  # a new row: the rules decide its language and group — and so its id
                c.lang, c.group = self.ruled_identity(rules, c.lang, c.group,
                                                      c.url or draft.meta.source.url, fired)
                c.section = self.ruled_section(rules, c.section, c.url or draft.meta.source.url, fired)
            cid = c.id or self.chapter_id_for(c.num, c.lang, c.group)
            adopted = None
            if cid not in old_ids:
                adopted = by_url.get(c.url) if c.url else None
                # the spelling as it arrived first: a row stored before a rule
                # existed is still this chapter, and must not be doubled
                for lang, group in dict.fromkeys((raw, (c.lang, c.group))):
                    bucket = by_key.get((c.num.strip().casefold(), lang.strip().casefold(),
                                         group.strip().casefold()))
                    if adopted is None and bucket:
                        adopted = bucket[0]
            if adopted is not None:
                claim(adopted)
                cid = adopted
            elif not c.id and held.get(cid, key(c)) != key(c):
                # The id this number derives is held by a row that has since
                # been RENUMBERED. This is a different chapter, not a repeat of
                # that one — dropping it as a twin is how "add chapter 2" does
                # nothing at all after chapter 2 was renamed to 3.
                cid = _free_id(cid, set(held) | taken)
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
            for f in ("url", "title", "date", "section"):
                if not getattr(anchor, f) and getattr(fresh, f):
                    setattr(anchor, f, getattr(fresh, f))
            final[j] = anchor
        rows = final

        sidecars = self._sidecars(title_id)
        kept = {safe_id(c.id) for c in rows}
        stored: set[str] | None = None  # one listing, and only if a row has no sidecar
        for c in old.chapters:
            if safe_id(c.id) in kept:
                continue
            # media-backed = a sidecar OR a bare archive (a crash may have left
            # the zip without its sidecar) — either way the row must survive
            if safe_id(c.id) not in sidecars:
                if stored is None:  # named as the filesystem matches them: in any case
                    stored = {stem.casefold() for stem in self.vault.chapter_files(title_id)}
                if safe_id(c.id).casefold() not in stored:
                    continue
            if draft.meta.chapterOrder == "manual":
                rows.append(c)
            else:
                k = chapter_num_key(c.num)
                idx = next((i for i, r in enumerate(rows) if chapter_num_key(r.num) > k), len(rows))
                rows.insert(idx, c)
            kept.add(safe_id(c.id))
        draft.chapters = rows
        self._count_fired(fired)

    def patch_user(self, title_id: str, patch: UserPatch) -> TitleOut | None:
        with self.vault.title_lock(title_id):
            doc = self.vault.patch_user(title_id, patch)
            if doc is None:
                return None
            held = self.index.media_of(title_id)
            if held is None:  # not indexed yet: the whole way
                return self._indexed(title_id, doc)
            # The user layer moves no chapter and no cover, so the row's media
            # and covers are still what is on disk. Reading them again was every
            # chapter's sidecar — twice — for a read mark, a star, a video position.
            sides, sides_at, covers = held
            self.index.upsert(title_id, doc, self.vault.touched_at(title_id), sides, sides_at, covers)
        return self._out(title_id, doc, sides, covers)

    def set_cover(self, title_id: str, data: bytes, ext: str, source_url: str = "",
                  append: bool = False) -> TitleOut | None:
        """Store captured cover bytes; record where they came from in the meta
        layer (the cover is part of the draft flow, but its bytes arrive as a
        side-channel upload right after the commit).

        `coverSource` describes the FIRST cover only — it is one field, and a
        title that wears five images cannot honestly claim one origin for them
        all. Appending therefore leaves it alone. Returns None when the title is
        gone, and the title unchanged when it already carries the maximum."""
        with self.vault.title_lock(title_id):
            doc = self.vault.load(title_id)
            if doc is None:
                return None
            if append:
                if self.vault.add_cover(title_id, data, ext) is None:
                    return self._out_held(title_id, doc)
            else:
                self.vault.write_cover(title_id, data, ext)
                if source_url and doc.meta.coverSource != source_url:
                    doc.meta.coverSource = source_url
                    draft = DraftIn(meta=doc.meta, provenance=doc.provenance, chapters=doc.chapters)
                    doc = self.vault.commit_meta(title_id, draft) or doc
            return self._indexed(title_id, doc)

    def delete_cover(self, title_id: str, slot: int | None = None) -> TitleOut | None:
        """Remove one cover, or — with no slot — every cover this title has."""
        with self.vault.title_lock(title_id):
            doc = self.vault.load(title_id)
            if doc is None:
                return None
            self.vault.delete_cover(title_id, slot)
            # the provenance belongs to the first cover: it is only stale once
            # that one is gone, which a delete of any other slot cannot do
            if doc.meta.coverSource and not self.vault.cover_paths(title_id):
                doc.meta.coverSource = ""
                draft = DraftIn(meta=doc.meta, provenance=doc.provenance, chapters=doc.chapters)
                doc = self.vault.commit_meta(title_id, draft) or doc
            return self._indexed(title_id, doc)

    def reorder_covers(self, title_id: str, order: list[int]) -> TitleOut | None:
        with self.vault.title_lock(title_id):
            doc = self.vault.load(title_id)
            if doc is None:
                return None
            self.vault.set_cover_order(title_id, order)
            return self._indexed(title_id, doc)

    # ---- chapter media (downloads) ----

    def _recommit(self, title_id: str, doc: TitleDoc) -> TitleOut | None:
        """Persist a mutated doc's meta layers (user layer preserved by the vault)."""
        draft = DraftIn(meta=doc.meta, provenance=doc.provenance, chapters=doc.chapters)
        saved = self.vault.commit_meta(title_id, draft)
        if saved is None:
            return None
        return self._indexed(title_id, saved)

    @staticmethod
    def _norm(s: str) -> str:
        return s.strip().casefold()

    def incoming_rules(self) -> list:
        """The library's rules, read ONCE for a whole commit or ingest. Rules that
        cannot be read let identities through as typed rather than refuse the
        write — the Rules view says what is wrong with the file."""
        try:
            return self.rules.rules()
        except UnreadableDocument:
            return []

    @staticmethod
    def ruled_identity(rules: list, lang: str, group: str, url: str, fired: list[str]) -> tuple[str, str]:
        """A NEW chapter's language and group as the rules leave them — the
        identity it is then named by. Rows already stored are never rewritten
        here: that is what applying a rule to existing titles is for."""
        if not rules:
            return lang, group
        out, hits = rules_mod.apply(rules, {"language": lang, "group": group},
                                    rules_mod.host_of(url))
        fired += [f.rule for f in hits]
        return str(out["language"]), str(out["group"])

    @staticmethod
    def ruled_section(rules: list, section: str, url: str, fired: list[str]) -> str:
        """An arriving section as the rules leave it. It names no entry, so it is
        ruled wherever it lands — a new row, or a row that had none — and a row
        already holding one is never rewritten here."""
        if not rules or not section:
            return section
        out, hits = rules_mod.apply(rules, {"section": section}, rules_mod.host_of(url))
        fired += [f.rule for f in hits]
        return str(out["section"])

    def _count_fired(self, fired: list[str]) -> None:
        if fired:
            self.index.rule_fired(fired, datetime.now(timezone.utc).isoformat(timespec="seconds"))

    @staticmethod
    def chapter_id_for(num: str, lang: str, group: str) -> str:
        """The id a chapter identity gets — the FIRST choice for one, and the
        only place an id is derived: a client that invents its own would
        disagree with the row reconciliation writes, and then address media that
        lives under a different id. `_free_id` settles the rest."""
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
        # no row wears this identity, yet a RENUMBERED one may still hold the id
        # it derives — and the media file named after that id
        cid = _free_id(self.chapter_id_for(num, lang, group), {safe_id(c.id) for c in doc.chapters})
        row = ChapterRow(id=cid, num=num, lang=lang, group=group, url=url)
        if doc.meta.chapterOrder == "manual":
            doc.chapters.append(row)  # the user's arrangement is sacred
        else:
            key = chapter_num_key(num)
            idx = next((i for i, c in enumerate(doc.chapters) if chapter_num_key(c.num) > key), len(doc.chapters))
            doc.chapters.insert(idx, row)
        return row, True

    def _ruled_row_for(self, doc: TitleDoc, *, num: str, lang: str, group: str,
                       url: str = "") -> tuple[ChapterRow, bool]:
        """`_row_for`, with a NEW row's language and group named by the rules."""
        n = self._norm
        if not any(n(c.num) == n(num) and n(c.lang) == n(lang) and n(c.group) == n(group)
                   for c in doc.chapters):
            fired: list[str] = []
            lang, group = self.ruled_identity(self.incoming_rules(), lang, group,
                                              url or doc.meta.source.url, fired)
            self._count_fired(fired)
        return self._row_for(doc, num=num, lang=lang, group=group, url=url)

    def name_entry(self, title_id: str, *, num: str, lang: str, group: str, title: str) -> bool:
        """The entry a download named after its file goes into — made as the
        download starts, when the file's name is known, so the list shows it by
        that name while it arrives (and the ingest finds it as any other).
        `title` sits beside a typed number; without one the name IS the label."""
        def change(doc: TitleDoc) -> bool:
            row, created = self._ruled_row_for(doc, num=num, lang=lang, group=group)
            if title and row.title != title:
                row.title = title
                return True
            return created
        return self.amend(title_id, change) is not None

    def attach_chapter_media(
        self, title_id: str, *, num: str, lang: str, group: str,
        src: Path, sidecar: dict, url: str = "", chapter_id: str = "", section: str = "",
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
                row, _ = self._ruled_row_for(doc, num=num, lang=lang, group=group, url=url)
            if section and not row.section:
                # a part named with the file fills an entry that has none
                fired = []
                row.section = self.ruled_section(self.incoming_rules(), section,
                                                 url or doc.meta.source.url, fired)
                self._count_fired(fired)
            arrived = str(sidecar.get("filename") or src.name)
            suffix = Path(arrived).suffix.lower()
            if suffix in subtitles.SUBTITLE_EXTS:
                # a subtitle file for an episode becomes its subtitles — the
                # video stays (design/subtitles.md, D4)
                side = self.vault.chapter_sidecar(title_id, row.id) or {}
                if side.get("kind") != "video":
                    raise subtitles.UnreadableSubtitle(
                        "subtitles belong to an episode — download its video first")
                self._add_subtitle(title_id, row.id, side, src.read_bytes(), arrived,
                                   file_url=str(sidecar.get("fileUrl") or ""))
                src.unlink(missing_ok=True)
            elif suffix in media.IMAGE_EXTS:
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

    # ---- a folder the owner already has (library/folder_import.py) ----

    def amend(self, title_id: str, change: Callable[[TitleDoc], bool]) -> TitleOut | None:
        """Load → change → commit one title as one critical section. `change`
        says whether it changed anything; nothing is written when it did not."""
        with self.vault.title_lock(title_id):
            doc = self.vault.load(title_id)
            if doc is None:
                return None
            if not change(doc):
                return self._out_held(title_id, doc)
            return self._recommit(title_id, doc)

    def import_entry(self, title_id: str, sources: list[Path], *, num: str, lang: str,
                     group: str, title: str, section: str, date: str, sidecar: dict,
                     filename: str, link: bool, replace: bool) -> tuple[str, str]:
        """Store one entry of a folder in a title: a video or an archive as the
        file it is, a folder of images (`sources` are its pages) packed into a
        zip. The originals are only ever read — copied, or hard-linked with
        `link` — so a move deletes them after this returns, never before.

        The identity is taken as given: the plan already ran it through the
        rules, and named a stored entry by the spelling it is stored under.
        Returns the outcome — "added", "replaced", or "exists" when the entry
        turned up meanwhile and `replace` is off — and the entry's id."""
        root = self.vault.root.resolve()
        for folder in {s.parent for s in sources}:
            if folder.resolve().is_relative_to(root):
                # a junction can make the vault look like any folder at all; what
                # a move would then delete is the library's own media
                raise ValueError(f"{folder} is inside the library itself")
        with self.vault.title_lock(title_id):
            doc = self.vault.load(title_id)
            if doc is None:
                raise FileNotFoundError(f"{title_id} is no longer in the library")
            n = self._norm
            held = next((c for c in doc.chapters
                         if n(c.num) == n(num) and n(c.lang) == n(lang) and n(c.group) == n(group)), None)
            if held is not None and not replace:
                return "exists", held.id
            pack = len(sources) != 1 or sources[0].suffix.lower() in media.IMAGE_EXTS
            incoming = self.vault.incoming_path(title_id, ".zip" if pack else sources[0].suffix.lower())
            try:
                if pack:
                    media.pack_images(sources, incoming)
                else:
                    _place_copy(sources[0], incoming, link)
                row, _ = self._row_for(doc, num=num, lang=lang, group=group)
                for f, v in (("title", title), ("section", section), ("date", date)):
                    if v and not getattr(row, f):
                        setattr(row, f, v)
                stored = self.vault.ingest_chapter_media(title_id, row.id, incoming, sidecar,
                                                         filename=filename)
            except BaseException:
                incoming.unlink(missing_ok=True)
                raise
            self._mirror_comicinfo(title_id, doc, row, stored)
            self._recommit(title_id, doc)
            return ("added" if held is None else "replaced"), row.id

    def flush_stored(self, title_id: str, chapter_id: str = "") -> None:
        """Put what a move stored on the disk itself — a chapter's media, or with
        no `chapter_id` the title's covers — before the original goes: a copy
        still in the system's memory dies with a power cut, and the original
        would be gone with it. Every cover, not the first: a cover added
        meanwhile can take the first place, and flushing one already on the
        disk costs next to nothing."""
        with self.vault.title_lock(title_id):
            paths = ([self.vault.chapter_media_path(title_id, chapter_id)] if chapter_id
                     else self.vault.cover_paths(title_id))
            if not paths or None in paths:
                raise FileNotFoundError(f"{title_id}: nothing stored to flush")
            for path in paths:
                media.flush(path)

    def own_media(self, title_id: str, chapter_id: str) -> None:
        """Give a chapter's file an inode of its own. A move links the original
        into the vault and deletes it afterwards; when the delete fails, the two
        names are still one file — and an edit outside would change the vault
        behind its digests. Copying it over itself parts them."""
        with self.vault.title_lock(title_id):
            path = self.vault.chapter_media_path(title_id, chapter_id)
            if path is None or path.stat().st_nlink < 2:
                return
            tmp = media.tmp_path(path)
            shutil.copyfile(path, tmp)
            media.replace_atomically(tmp, path)

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
            return self._indexed(title_id, doc), len(fresh)

    def delete_chapter_media(self, title_id: str, chapter_id: str) -> TitleOut | None:
        with self.vault.title_lock(title_id):
            doc = self.vault.load(title_id)
            if doc is None:
                return None
            self.vault.delete_chapter_media(title_id, chapter_id)
            return self._indexed(title_id, doc)

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

    def _thumb_file(self, key: str) -> Path:
        return self.vault.root / APP_DIR / "cache" / "thumbs" / f"{key}.jpg"

    def _cached(self, key: str) -> tuple[bytes, str] | None:
        # one read, no stat first: on a network vault every call is a round trip
        try:
            return self._thumb_file(key).read_bytes(), "image/jpeg"
        except OSError:
            return None  # not made yet, or vanished: made again

    def _cached_thumb(self, key: str, source: Callable[[], tuple[bytes, str]], width: int,
                      cap: float | None = None) -> tuple[bytes, str]:
        """A downscaled JPEG cached on disk under `key` (which the caller stamps
        with the chapter/cover version, so an edited file misses the cache by
        construction). `source` is called only on a MISS — a cache hit must not
        read the original, which on a network vault is the entire request cost —
        and misses are made one at a time (library/previews.py). Undecodable
        formats serve the original. The cache is the vault's own, so a vault
        opened on another computer brings its previews along."""
        hit = self._cached(key)
        if hit is not None:
            return hit
        with self.previews.making():
            # made while this one waited its turn: a grid asks for a tile twice
            hit = self._cached(key)
            if hit is not None:
                return hit
            data, ct = source()
            thumb = media.thumbnail(data, width, cap)
            if thumb is None:
                return data, ct
            cfile = self._thumb_file(key)
            try:
                self.vault.make_dir(cfile.parent)
            except OSError:
                return thumb, "image/jpeg"  # a cache that cannot be written is only a miss next time
            # unique temp name: a request the lock does not cover (another
            # process on a shared vault) must not interleave into a corrupt JPEG
            tmp = cfile.with_name(f"{cfile.name}.{uuid.uuid4().hex[:8]}.tmp")
            try:
                tmp.write_bytes(thumb)
                os.replace(tmp, cfile)
            except OSError:
                tmp.unlink(missing_ok=True)
            return thumb, "image/jpeg"

    def page_image(self, vocab: str, name: str, width: int) -> tuple[bytes, str] | None:
        """A value page's image — downscaled and cached when `width` is given."""
        path = self.pages.image_dir(vocab) / Path(name).name
        if not name or not path.is_file():
            return None
        ct = media.CT_BY_EXT.get(path.suffix.lower(), "application/octet-stream")
        if not width:
            return path.read_bytes(), ct
        key = cache_key("page", page_image_version(path.name), safe_id(vocab), width)
        return self._cached_thumb(key, lambda: (path.read_bytes(), ct), width)

    def cover_thumb(self, title_id: str, width: int, slot: int = 0, version: str = "") -> tuple[bytes, str] | None:
        """A cached downscaled cover — grids and lists never load the original.
        With the version the URL carries, a made preview is served by its key
        alone: no listing of the title's folder, no stat of the cover."""
        if version and _V_COVER.fullmatch(version):
            # the slot's file name is `cover.<ext>` / `cover-<n>.<ext>` (vault._cover_name)
            hit = self._cached(self._cover_key(title_id, version, "cover" if slot == 0 else f"cover-{slot + 1}", width))
            if hit is not None:
                return hit
        path = self.vault.cover_path(title_id, slot)
        if path is None:
            return None
        ct = media.CT_BY_EXT.get(path.suffix.lower(), "application/octet-stream")
        stat = path.stat()
        # The FILE NAME is in the key, not just its extension. A cover version is
        # mtime.size.ext, and two covers of one title routinely agree on all
        # three — written in the same breath, same format, same size — so
        # without the name the second one is served the first one's thumbnail.
        key = self._cover_key(title_id, cover_version(path.name, stat.st_mtime_ns, stat.st_size),
                              path.stem, width)
        return self._cached_thumb(key, lambda: (path.read_bytes(), ct), width)

    @staticmethod
    def _cover_key(title_id: str, version: str, stem: str, width: int) -> str:
        return cache_key("cover", version, safe_id(title_id), stem, width)

    @staticmethod
    def _frames_key(kind: str, title_id: str, chapter_id: str, version: str, width: int) -> str:
        return cache_key(kind, version, safe_id(title_id), safe_id(chapter_id), width)

    @staticmethod
    def _page_key(title_id: str, chapter_id: str, version: str, width: int,
                  cap: float | None, index: int) -> str:
        return cache_key("page", version, safe_id(title_id), safe_id(chapter_id), width,
                         f"c{cap:g}" if cap else "", index)

    # ---- an episode's subtitles (design/subtitles.md §3) ----------------------

    def _add_subtitle(self, title_id: str, chapter_id: str, side: dict, data: bytes, arrived: str, *,
                      lang: str = "", label: str = "", default: bool | None = None,
                      file_url: str = "") -> None:
        """Store one subtitle beside its episode and list it in the episode's
        sidecar. Called inside the title's lock, with that sidecar in hand."""
        ext = Path(arrived).suffix.lower()
        if ext not in subtitles.SUBTITLE_EXTS:
            raise subtitles.UnreadableSubtitle(f"{arrived} is not a subtitle file (srt, vtt, ass or ssa)")
        vtt = subtitles.to_webvtt(data, ext).encode("utf-8")  # refused before the vault changes
        tracks = [dict(t) for t in side.get("subtitles") or []]
        n = max((int(t.get("id") or 0) for t in tracks), default=0) + 1
        lang = (lang or subtitles.guess_lang(arrived)).strip().lower()
        first = not tracks
        if default:
            for t in tracks:
                t["default"] = False
        self.vault.write_subtitle(title_id, chapter_id, n, vtt, data, ext)
        tracks.append({"id": str(n), "lang": lang, "label": label.strip() or subtitles.label_of(lang),
                       "default": bool(default) or first, "origName": Path(arrived).name,
                       "format": ext.lstrip("."), "sha": hashlib.sha256(vtt).hexdigest()[:12],
                       **({"fileUrl": file_url} if file_url else {})})
        self.vault.write_chapter_sidecar(title_id, chapter_id, {**side, "subtitles": tracks})

    def _episode(self, title_id: str, chapter_id: str) -> tuple[TitleDoc, dict] | None:
        doc = self.vault.load(title_id)
        if doc is None or not any(c.id == chapter_id for c in doc.chapters):
            return None
        return doc, dict(self.vault.chapter_sidecar(title_id, chapter_id) or {})

    def add_subtitle(self, title_id: str, chapter_id: str, data: bytes, arrived: str, *,
                     lang: str = "", label: str = "", default: bool | None = None,
                     file_url: str = "") -> TitleOut | None:
        with self.vault.title_lock(title_id):
            got = self._episode(title_id, chapter_id)
            if got is None:
                return None
            doc, side = got
            if side.get("kind") != "video":
                raise subtitles.UnreadableSubtitle("subtitles belong to an episode — this entry has no video")
            self._add_subtitle(title_id, chapter_id, side, data, arrived, lang=lang, label=label,
                               default=default, file_url=file_url)
            return self._indexed(title_id, doc)

    def subtitle_from_downloads(self, title_id: str, chapter_id: str, name: str, *,
                                lang: str = "", label: str = "",
                                default: bool | None = None) -> TitleOut | None:
        """A subtitle in the library's downloads, filed under its episode — and
        gone from the downloads once it is."""
        path = self.loose.path(name)
        if path is None:
            raise FileNotFoundError(name)
        came = self.loose.arrival(name)
        out = self.add_subtitle(title_id, chapter_id, path.read_bytes(), name, lang=lang, label=label,
                                default=default, file_url=came.fileUrl if came else "")
        if out is not None:
            self.loose.remove(name)
        return out

    def update_subtitle(self, title_id: str, chapter_id: str, sub_id: str, *, lang: str | None = None,
                        label: str | None = None, default: bool | None = None) -> TitleOut | None:
        with self.vault.title_lock(title_id):
            got = self._episode(title_id, chapter_id)
            if got is None:
                return None
            doc, side = got
            tracks = [dict(t) for t in side.get("subtitles") or []]
            track = next((t for t in tracks if str(t.get("id")) == sub_id), None)
            if track is None:
                return None
            if lang is not None:
                track["lang"] = lang.strip().lower()
            if label is not None:
                track["label"] = label.strip() or subtitles.label_of(track.get("lang", ""))
            if default is not None:
                for t in tracks:
                    t["default"] = default and t is track
            self.vault.write_chapter_sidecar(title_id, chapter_id, {**side, "subtitles": tracks})
            return self._indexed(title_id, doc)

    def remove_subtitle(self, title_id: str, chapter_id: str, sub_id: str) -> TitleOut | None:
        with self.vault.title_lock(title_id):
            got = self._episode(title_id, chapter_id)
            if got is None:
                return None
            doc, side = got
            tracks = [dict(t) for t in side.get("subtitles") or []]
            gone = next((t for t in tracks if str(t.get("id")) == sub_id), None)
            if gone is None:
                return None
            self.vault.delete_subtitle(title_id, chapter_id, int(sub_id))
            tracks.remove(gone)
            # an episode with subtitles starts with one of them
            if gone.get("default") and tracks:
                tracks[0]["default"] = True
            self.vault.write_chapter_sidecar(title_id, chapter_id, {**side, "subtitles": tracks})
            return self._indexed(title_id, doc)

    def subtitle_file(self, title_id: str, chapter_id: str, sub_id: str) -> Path | None:
        side = self.vault.chapter_sidecar(title_id, chapter_id) or {}
        if not any(str(t.get("id")) == sub_id for t in side.get("subtitles") or []) or not sub_id.isdigit():
            return None
        path = self.vault.subtitle_path(title_id, chapter_id, int(sub_id))
        return path if path.is_file() else None

    def chapter_video_path(self, title_id: str, chapter_id: str) -> Path | None:
        """The file the window plays: the episode's playable copy where it has one
        (library/copies.py), else the stored episode; None for page media."""
        path = self.vault.chapter_media_path(title_id, chapter_id)
        if path is None or not media.is_video(path):
            return None
        copy = self.vault.chapter_copy_path(title_id, chapter_id)
        return copy if copy.is_file() else path

    def commit_copy(self, title_id: str, chapter_id: str, tmp: Path, of: str, held: dict) -> bool:
        """Put a finished playable copy beside its episode — only while the episode
        is still the bytes the copy was made from. A re-download that landed
        during the conversion wins, and gets a copy of its own."""
        with self.vault.title_lock(title_id):
            doc = self.vault.load(title_id)
            side = self.vault.chapter_sidecar(title_id, chapter_id)
            if doc is None or side is None or copies.original_of(side) != of:
                return False
            final = self.vault.chapter_copy_path(title_id, chapter_id)
            media.replace_atomically(tmp, final)
            side.pop("copyFailed", None)
            # when it was made rides along: a copy redone at the same size must
            # not come back under the version of the one it replaced
            side["copy"] = {**held, "size": final.stat().st_size, "made": time.time_ns()}
            self.vault.write_chapter_sidecar(title_id, chapter_id, side)
            self._index(title_id, doc)
        return True

    def record_copy_failed(self, title_id: str, chapter_id: str, of: str, error: str) -> None:
        """Remember a copy that could not be made OF THESE BYTES, so no launch tries
        the same hopeless file again on its own."""
        with self.vault.title_lock(title_id):
            doc = self.vault.load(title_id)
            side = self.vault.chapter_sidecar(title_id, chapter_id)
            if doc is None or side is None or copies.original_of(side) != of:
                return
            side["copyFailed"] = {"of": of, "error": error}
            self.vault.write_chapter_sidecar(title_id, chapter_id, side)
            self._index(title_id, doc)

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
                return self._out_held(title_id, doc)
            known = abs(float(side.get("duration") or 0.0) - seconds) < 0.5
            # Episodes stored before the app looked inside them carry no codec:
            # fill that in HERE, at first play, rather than on a listing (which
            # must touch no files) or on the serving path.
            probe_needed = side.get("kind") == "video" and "codec" not in side
            if known and not probe_needed:
                return self._out_held(title_id, doc)
            if probe_needed:
                path = self.vault.chapter_media_path(title_id, chapter_id)
                if path is not None:
                    side.update(media.probe_video(path))
            side["duration"] = round(seconds, 3)
            self.vault.write_chapter_sidecar(title_id, chapter_id, side)
            return self._indexed(title_id, doc)

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
            side["stillsSha"] = {**(side.get("stillsSha") or {}),
                                 kind: hashlib.sha256(data).hexdigest()[:16]}
            self.vault.write_chapter_sidecar(title_id, chapter_id, side)
            self._sidecar_cache.pop(title_id, None)
            doc = self.vault.load(title_id)
            if doc is not None:
                self._index(title_id, doc)
        return True

    def chapter_frames(self, title_id: str, chapter_id: str, kind: str,
                       width: int | None = None, version: str = "") -> tuple[bytes, str] | None:
        if kind not in FRAME_KINDS:
            return None
        if width and version and _V_STILLS.fullmatch(version):
            hit = self._cached(self._frames_key(kind, title_id, chapter_id, version, width))
            if hit is not None:
                return hit
        path = self.vault.chapter_frames_path(title_id, chapter_id, kind)
        if not path.is_file():
            return None
        try:
            if not width:
                return path.read_bytes(), "image/jpeg"
            # the version the URL carries — a file's timestamp is not one
            key = self._frames_key(kind, title_id, chapter_id,
                                   stills_version(self._sidecars(title_id).get(safe_id(chapter_id))), width)
            return self._cached_thumb(key, lambda: (path.read_bytes(), "image/jpeg"), width, None)
        except OSError as e:
            log.warning("%s %s/%s is unreadable: %s", kind, title_id, chapter_id, e)
            return None

    def chapter_pages(self, title_id: str, chapter_id: str) -> list[str] | None:
        path = self.vault.chapter_media_path(title_id, chapter_id)
        if path is None:
            return None
        return media.image_entries(path)

    def chapter_page(self, title_id: str, chapter_id: str, index: int, width: int | None = None,
                     cap: float | None = None, version: str = "") -> tuple[bytes, str] | None:
        """One page, or None when it cannot be served.

        A page that will not decompress (a truncated download, a bad CRC) is a
        MISSING page, not a broken server: one damaged entry must not answer a
        thumbnail request with a traceback, and a grid that asks for eight of
        them must not fill the log eight times over."""
        if width and version and _V_CHAPTER.fullmatch(version):
            # the version the URL carries: a made preview costs one read
            hit = self._cached(self._page_key(title_id, chapter_id, version, width, cap, index))
            if hit is not None:
                return hit
        path = self.vault.chapter_media_path(title_id, chapter_id)
        if path is None:
            return None
        key = ""
        if width:
            # keyed by the chapter's own version — every page op bumps it, so an
            # edited chapter misses this cache by construction. Looked up BEFORE
            # the zip is opened: a cached preview must not cost its archive a read
            key = self._page_key(title_id, chapter_id,
                                 chapter_version(self._sidecars(title_id).get(safe_id(chapter_id))),
                                 width, cap, index)
            hit = self._cached(key)
            if hit is not None:
                return hit
        try:
            entries = media.image_entries(path)
            if not (0 <= index < len(entries)):
                return None
            if not width:
                return media.read_entry(path, entries[index])
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
            return self._indexed(title_id, doc)

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
            return self._indexed(title_id, doc)

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
                return self._out_held(title_id, doc)
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
            return self._indexed(title_id, doc)

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
            return self._indexed(title_id, doc)

    def delete(self, title_id: str) -> bool:
        # the index entry goes even if the directory could not be fully removed,
        # or the library would keep serving a title whose files are half-gone
        try:
            return self.vault.delete(title_id)
        finally:
            self.index.remove(title_id)
            self._sidecar_cache.pop(title_id, None)
