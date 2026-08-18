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
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from . import media
from .index import LibraryIndex
from .models import Author, AuthorWork, ChapterRow, DraftIn, Source, TitleDoc, TitleOut, UserPatch
from .vault import Vault, safe_id


log = logging.getLogger("longbox.library")


def _chapter_num_key(num: str) -> tuple:
    """Smart chapter order: numeric ascending ("2" < "10", 5.5 between 5 and 6),
    non-numeric ("Extra") after, alphabetically."""
    m = re.match(r"\s*(\d+(?:[.,]\d+)?)", num)
    if m:
        return (0, float(m.group(1).replace(",", ".")))
    return (1, num.strip().casefold())


class Library:
    def __init__(self, root: Path, *, defer_sync: bool = False) -> None:
        self.vault = Vault(root)
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
        if self.vault.needs_normalize():
            self._normalize_thread = threading.Thread(
                target=self._normalize_archives_bg, name="lb-normalize", daemon=True)
            self._normalize_thread.start()

    def _normalize_archives_bg(self) -> None:
        try:
            self.normalize_archives()
        except Exception:  # noqa: BLE001 — a failed pass must never take the app down
            pass

    def normalize_archives(self, *, force: bool = False) -> int:
        """Run the archive sweep and mark the vault as normalized. `force`
        retries archives whose conversion failed before. Returns -1 when another
        pass already holds the sweep."""
        if not self._normalize_lock.acquire(blocking=False):
            return -1
        try:
            changed = self.vault.normalize_chapter_archives(force=force, stop=self._closing)
            if not self._closing.is_set():
                self.vault.mark_normalized()
            if changed and not self._closing.is_set():
                self.rescan()  # converted chapters now carry real page counts
            return changed
        finally:
            self._normalize_lock.release()

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
        on_disk = {tid: (doc_at, ch_at, self._cover_of(tid, name, cover_at))
                   for tid, (doc_at, ch_at, name, cover_at) in scanned.items()}
        statted = time.perf_counter()
        for gone in indexed.keys() - on_disk.keys():
            self.index.remove(gone)
        stale = [tid for tid, stamp in on_disk.items() if indexed.get(tid) != stamp]
        if progress:
            progress(0, len(stale))
        rows = []
        for i, tid in enumerate(stale):
            try:
                doc = self.vault.load(tid)
            except Exception:  # noqa: BLE001 — one malformed title.json never blocks a launch
                continue
            if doc is not None:
                # stamped before the read, exactly as _index does
                stamp = on_disk[tid]
                rows.append((tid, doc, stamp[0], self._sidecars(tid), stamp[1], stamp[2]))
            if progress:
                progress(i + 1, len(stale))
        # nothing is serving yet at construction time, so one batch is safe here
        # where per-title locking is the rule everywhere else
        self.index.upsert_many(rows)
        self.sync_state["changed"] = len(rows) + len(indexed.keys() - on_disk.keys())
        # the numbers a slow launch has to be diagnosed with — a network vault
        # costs a round trip per stat, and that is invisible from the outside
        log.info("opened %s: %d titles (scan %.1fs, re-read %d in %.1fs)",
                 self.vault.root, len(on_disk), statted - started,
                 len(rows), time.perf_counter() - statted)
        return len(rows)

    def rescan(self, progress=None) -> None:
        """Rebuild the index purely from the on-disk vault. `progress(done,
        total)` ticks per title file read — reading the files IS the slow part.
        ONE unreadable document must never abort the scan (or, at startup, the
        whole app): it is skipped, exactly like the layout migration does."""
        scanned = self.vault.scan()
        ids = sorted(scanned)
        docs = {}
        touched = {}
        media = {}
        media_at = {}
        cover = {}
        for i, tid in enumerate(ids):
            try:
                doc = self.vault.load(tid)
            except Exception:  # noqa: BLE001 — malformed title.json
                doc = None
            if doc is not None:
                docs[tid] = doc
                doc_at, ch_at, name, cover_at = scanned[tid]
                touched[tid] = doc_at
                media[tid] = self._sidecars(tid)
                media_at[tid] = ch_at
                cover[tid] = self._cover_of(tid, name, cover_at)
            if progress:
                progress(i + 1, len(ids))
        self.index.rebuild(docs, touched, media, media_at, cover)

    def close(self) -> None:
        # tell the sweep to stop, give it a moment, then close the index it uses
        self._closing.set()
        for thread in (self._normalize_thread, self._sync_thread):
            if thread is not None and thread.is_alive():
                thread.join(timeout=5)
        self.index.close()

    # ---- DTO composition ----

    @staticmethod
    def _cover_of(title_id: str, name: str, mtime: int) -> str:
        """The cover endpoint URL, versioned by the file's mtime so a re-captured
        cover busts the UI cache. Composed from a scan, never from a fresh stat."""
        return f"/api/titles/{title_id}/cover?v={mtime:x}" if name else ""

    def _cover_url(self, title_id: str) -> str:
        """The same URL for ONE title, straight after a write."""
        path = self.vault.cover_path(title_id)
        if path is None:
            return ""
        return self._cover_of(title_id, path.name, path.stat().st_mtime_ns)

    def _index(self, title_id: str, doc: TitleDoc) -> None:
        """Refresh the index for one title. ALWAYS called inside that title's
        lock: a doc read in a critical section and indexed after it is released
        can overwrite a newer writer's row with stale data. The chapter sidecars
        are read HERE, once per write, so no listing ever has to touch them."""
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

    def count(self) -> int:
        return self.index.count()

    def authors(self) -> list[Author]:
        """People aggregated from the titles' authors[] and artists[], with the
        role derived from where they appear, plus cover art and common tags."""
        agg: dict[str, dict] = {}
        for tid, doc, cover in self.index.all_docs():
            m = doc.meta
            for role_key, names in (("author", m.authors), ("artist", m.artists)):
                for raw in names:
                    name = raw.strip()
                    if not name:
                        continue
                    a = agg.setdefault(name, {"works": {}, "author": False, "artist": False,
                                              "chapters": 0, "tags": Counter()})
                    a[role_key] = True
                    if tid not in a["works"]:
                        a["works"][tid] = AuthorWork(id=tid, title=m.title, cover=cover)
                        a["chapters"] += len(doc.chapters)
                        a["tags"].update(m.tags)
        favs = self.vault.author_favorites()
        out: list[Author] = []
        for name, a in sorted(agg.items()):
            role = "both" if a["author"] and a["artist"] else ("artist" if a["artist"] else "author")
            aid = safe_id(name)
            out.append(Author(id=aid, name=name, role=role, fav=aid in favs,
                              works=list(a["works"].values()),
                              titles=len(a["works"]), chapters=a["chapters"],
                              topTags=[tag for tag, _ in a["tags"].most_common(5)]))
        return out

    def set_author_favorite(self, author_id: str, value: bool) -> bool:
        """Write-through favorite mark; True when the author currently exists."""
        self.vault.set_author_favorite(author_id, value)
        return any(a.id == author_id for a in self.authors())

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
            cid = c.id
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
        digest = hashlib.sha1(f"{n(num)}|{n(lang)}|{n(group)}".encode()).hexdigest()[:8]
        row = ChapterRow(id=safe_id(f"ch-{num or 'x'}-{digest}"), num=num, lang=lang, group=group, url=url)
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
        page-by-page downloads accumulate into one chapter zip."""
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
                row, _ = self._row_for(doc, num=num, lang=lang, group=group, url=url)
            suffix = Path(sidecar.get("filename") or src.name).suffix.lower()
            if suffix in media.IMAGE_EXTS:
                # unpacked media: the image becomes the chapter zip's next page
                path = self.vault.chapter_media_path(title_id, row.id)
                if path is None:
                    path = self.vault.chapter_archive_target(title_id, row.id)
                media.renumber_and_append(path, [(src.read_bytes(), suffix)])
                src.unlink(missing_ok=True)
                side = self.vault.chapter_sidecars(title_id).get(safe_id(row.id), {})
                side.update({k: v for k, v in sidecar.items() if v})  # latest source wins
                side["pages"] = len(media.image_entries(path))
                side["size"] = path.stat().st_size
                self.vault.write_chapter_sidecar(title_id, row.id, side)
            else:
                self.vault.ingest_chapter_media(title_id, row.id, src, sidecar)
            return self._recommit(title_id, doc)

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
    def _cached_thumb(key: str, data: bytes, ct: str, width: int,
                      cap: float | None = None) -> tuple[bytes, str]:
        """A downscaled JPEG for `data`, cached on disk under `key` (which the
        caller stamps with the source file's mtime, so an edited file misses the
        cache by construction). Undecodable formats serve the original."""
        from ..config_store import config_dir
        cfile = config_dir() / "cache" / "thumbs" / f"{key}.jpg"
        if cfile.is_file():
            try:
                return cfile.read_bytes(), "image/jpeg"
            except OSError:
                pass  # a half-written or vanished cache entry: just re-make it
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
        key = f"cover-{safe_id(title_id)}-{path.stat().st_mtime_ns:x}-{width}"
        return self._cached_thumb(key, path.read_bytes(), ct, width)

    def chapter_pages(self, title_id: str, chapter_id: str) -> list[str] | None:
        path = self.vault.chapter_media_path(title_id, chapter_id)
        if path is None:
            return None
        return media.image_entries(path)

    def chapter_page(self, title_id: str, chapter_id: str, index: int,
                     width: int | None = None, cap: float | None = None) -> tuple[bytes, str] | None:
        path = self.vault.chapter_media_path(title_id, chapter_id)
        if path is None:
            return None
        entries = media.image_entries(path)
        if not (0 <= index < len(entries)):
            return None
        data, ct = media.read_entry(path, entries[index])
        if not width:
            return data, ct
        # keyed by the archive's mtime — editing the archive (page deletion)
        # invalidates the whole set by construction
        capkey = f"-c{cap:g}" if cap else ""
        key = f"{safe_id(title_id)}-{safe_id(chapter_id)}-{path.stat().st_mtime_ns:x}-{width}{capkey}-{index}"
        return self._cached_thumb(key, data, ct, width, cap)

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
        pages = len(media.image_entries(path))
        side["pages"] = pages
        side["size"] = path.stat().st_size
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
