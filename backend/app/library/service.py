"""The library service ties the on-disk vault (source of truth) to the SQLite
index (rebuildable cache). Reads go through the index; writes go to the vault
first, then the index — so a re-scan of disk reproduces the exact same state.
The service also composes the flat wire DTO from the layered documents.
"""
from __future__ import annotations

import hashlib
import re
import threading
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from . import media
from .index import LibraryIndex
from .models import Author, AuthorWork, ChapterRow, DraftIn, Source, TitleDoc, TitleOut, UserPatch
from .vault import Vault, safe_id


def _chapter_num_key(num: str) -> tuple:
    """Smart chapter order: numeric ascending ("2" < "10", 5.5 between 5 and 6),
    non-numeric ("Extra") after, alphabetically."""
    m = re.match(r"\s*(\d+(?:[.,]\d+)?)", num)
    if m:
        return (0, float(m.group(1).replace(",", ".")))
    return (1, num.strip().casefold())


class Library:
    def __init__(self, root: Path) -> None:
        self.vault = Vault(root)
        self.index = LibraryIndex(root / "index.db")
        # two concurrent creates may derive the same fresh id — serialize the
        # pick-unique-id → first-commit step
        self._create_lock = threading.Lock()
        self.rescan()
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
        retries archives whose conversion failed before."""
        changed = self.vault.normalize_chapter_archives(force=force)
        self.vault.mark_normalized()
        if changed:
            self.rescan()  # converted chapters now carry real page counts
        return changed

    @property
    def root(self) -> Path:
        return self.vault.root

    def rescan(self, progress=None) -> None:
        """Rebuild the index purely from the on-disk vault. `progress(done,
        total)` ticks per title file read — reading the files IS the slow part."""
        ids = self.vault.list_ids()
        docs = {}
        for i, tid in enumerate(ids):
            doc = self.vault.load(tid)
            if doc is not None:
                docs[tid] = doc
            if progress:
                progress(i + 1, len(ids))
        self.index.rebuild(docs)

    def close(self) -> None:
        self.index.close()

    # ---- DTO composition ----

    def _cover_url(self, title_id: str) -> str:
        """Local cover endpoint when bytes exist, versioned by file mtime so a
        re-captured cover busts the UI cache."""
        path = self.vault.cover_path(title_id)
        if path is None:
            return ""
        return f"/api/titles/{title_id}/cover?v={path.stat().st_mtime_ns:x}"

    def _out(self, title_id: str, doc: TitleDoc) -> TitleOut:
        sidecars = self.vault.chapter_sidecars(title_id)
        media_map = {c.id: sidecars[safe_id(c.id)] for c in doc.chapters if safe_id(c.id) in sidecars}
        return TitleOut.from_doc(title_id, doc, self._cover_url(title_id), media_map)

    # ---- reads (via the index) ----

    def query(self, **kwargs) -> list[TitleOut]:
        return [self._out(tid, doc) for tid, doc in self.index.query(**kwargs)]

    def get(self, title_id: str) -> TitleOut | None:
        doc = self.index.get(title_id)
        return self._out(title_id, doc) if doc else None

    def facets(self, selection: dict | None = None) -> dict[str, list[dict]]:
        return self.index.facet_counts(selection or {})

    def count(self) -> int:
        return self.index.count()

    def authors(self) -> list[Author]:
        """People aggregated from the titles' authors[] and artists[], with the
        role derived from where they appear, plus cover art and common tags."""
        agg: dict[str, dict] = {}
        for tid, doc in self.index.all_docs():
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
                        a["works"][tid] = AuthorWork(id=tid, title=m.title, cover=self._cover_url(tid))
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
        for _, doc in self.index.all_docs():
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
        self.index.upsert(tid, doc)
        return self._out(tid, doc)

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
        self.index.upsert(title_id, doc)
        return self._out(title_id, doc)

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
        doc = self.vault.patch_user(title_id, patch)
        if doc is None:
            return None
        self.index.upsert(title_id, doc)
        return self._out(title_id, doc)

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
        self.index.upsert(title_id, doc)
        return self._out(title_id, doc)

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
        self.index.upsert(title_id, doc)
        return self._out(title_id, doc)

    # ---- chapter media (downloads) ----

    def _recommit(self, title_id: str, doc: TitleDoc) -> TitleOut | None:
        """Persist a mutated doc's meta layers (user layer preserved by the vault)."""
        draft = DraftIn(meta=doc.meta, provenance=doc.provenance, chapters=doc.chapters)
        saved = self.vault.commit_meta(title_id, draft)
        if saved is None:
            return None
        self.index.upsert(title_id, saved)
        return self._out(title_id, saved)

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
        if path is None or not media.image_entries(path):
            return []
        side = self.vault.chapter_sidecars(title_id).get(safe_id(chapter_id), {})
        return [k for k in (side.get("pageKeys") or []) if isinstance(k, str)]

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
            side = self.vault.chapter_sidecars(title_id).get(safe_id(chapter_id), {})
            known = set(side.get("pageKeys") or []) if media.image_entries(path) else set()
            fresh = []
            for data, ext, key in images:
                if key in known or not data:
                    continue
                known.add(key)
                fresh.append((data, ext, key))
            if fresh:
                media.renumber_and_append(path, [(d, e) for d, e, _ in fresh])
                side.setdefault("importedFrom", "page-capture")
                side.setdefault("downloadedAt", datetime.now(timezone.utc).isoformat(timespec="seconds"))
                side["pageUrl"] = page_url or side.get("pageUrl", "")
                side["pageKeys"] = [*(side.get("pageKeys") or []), *[k for _, _, k in fresh]]
                side["pages"] = len(media.image_entries(path))
                side["size"] = path.stat().st_size
                self.vault.write_chapter_sidecar(title_id, chapter_id, side)
            self.index.upsert(title_id, doc)
            return self._out(title_id, doc), len(fresh)

    def delete_chapter_media(self, title_id: str, chapter_id: str) -> TitleOut | None:
        with self.vault.title_lock(title_id):
            doc = self.vault.load(title_id)
            if doc is None:
                return None
            self.vault.delete_chapter_media(title_id, chapter_id)
        self.index.upsert(title_id, doc)
        return self._out(title_id, doc)

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

    def cover_thumb(self, title_id: str, width: int) -> tuple[bytes, str] | None:
        """A cached downscaled cover — grids and lists never load the original.
        Same disk cache as page thumbnails, keyed by the cover file's mtime."""
        path = self.vault.cover_path(title_id)
        if path is None:
            return None
        data = path.read_bytes()
        ct = media.CT_BY_EXT.get(path.suffix.lower(), "application/octet-stream")
        from ..config_store import config_dir
        key = f"cover-{safe_id(title_id)}-{path.stat().st_mtime_ns:x}-{width}.jpg"
        cfile = config_dir() / "cache" / "thumbs" / key
        if cfile.is_file():
            return cfile.read_bytes(), "image/jpeg"
        thumb = media.thumbnail(data, width)
        if thumb is None:
            return data, ct  # undecodable format — serve the original
        cfile.parent.mkdir(parents=True, exist_ok=True)
        tmp = cfile.with_name(cfile.name + ".tmp")
        tmp.write_bytes(thumb)
        tmp.replace(cfile)
        return thumb, "image/jpeg"

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
        # thumbnails are cached on disk, keyed by the archive's mtime — editing
        # the archive (page deletion) invalidates the whole set automatically
        from ..config_store import config_dir
        capkey = f"-c{cap:g}" if cap else ""
        key = f"{safe_id(title_id)}-{safe_id(chapter_id)}-{path.stat().st_mtime_ns:x}-{width}{capkey}-{index}.jpg"
        cfile = config_dir() / "cache" / "thumbs" / key
        if cfile.is_file():
            return cfile.read_bytes(), "image/jpeg"
        thumb = media.thumbnail(data, width, cap)
        if thumb is None:
            return data, ct  # undecodable format — serve the original
        cfile.parent.mkdir(parents=True, exist_ok=True)
        tmp = cfile.with_name(cfile.name + ".tmp")
        tmp.write_bytes(thumb)
        tmp.replace(cfile)
        return thumb, "image/jpeg"

    def _touch_media_sidecar(self, title_id: str, chapter_id: str, path: Path, created: bool) -> None:
        """Refresh pages/size after a page operation; a freshly created archive
        gets a local-import sidecar (added by hand — no web provenance)."""
        side = self.vault.chapter_sidecars(title_id).get(safe_id(chapter_id), {})
        if created or not side:
            side = {"fileUrl": "", "pageUrl": "", "filename": path.name, "importedFrom": "local",
                    "downloadedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"), **side}
        side["pages"] = len(media.image_entries(path))
        side["size"] = path.stat().st_size
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
            if path is None:
                path = self.vault.chapter_archive_target(title_id, chapter_id)
            media.renumber_and_append(path, files)
            self._touch_media_sidecar(title_id, chapter_id, path, created)
        self.index.upsert(title_id, doc)
        return self._out(title_id, doc)

    def reorder_chapter_pages(self, title_id: str, chapter_id: str, order: list[int]) -> TitleOut | None:
        """Rearrange the pages inside an entry's archive (raises ValueError on a
        bad permutation)."""
        with self.vault.title_lock(title_id):
            doc = self.vault.load(title_id)
            path = self.vault.chapter_media_path(title_id, chapter_id)
            if doc is None or path is None:
                return None
            media.reorder_entries(path, order)
            self._touch_media_sidecar(title_id, chapter_id, path, False)
        self.index.upsert(title_id, doc)
        return self._out(title_id, doc)

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
            names = {entries[i] for i in indices if 0 <= i < len(entries)}
            if not names:
                return self._out(title_id, doc)
            dst = self.vault.chapter_media_path(title_id, dst_id)
            created = dst is None
            if dst is None:
                dst = self.vault.chapter_archive_target(title_id, dst_id)
            src_left, _ = media.move_images(src, dst, names)
            self._touch_media_sidecar(title_id, dst_id, dst, created)
            if src_left == 0:
                self.vault.delete_chapter_media(title_id, src_id)
            else:
                self._touch_media_sidecar(title_id, src_id, src, False)
        self.index.upsert(title_id, doc)
        return self._out(title_id, doc)

    def delete_chapter_pages(self, title_id: str, chapter_id: str, indices: list[int]) -> TitleOut | None:
        with self.vault.title_lock(title_id):
            doc = self.vault.load(title_id)
            path = self.vault.chapter_media_path(title_id, chapter_id)
            if doc is None or path is None:
                return None
            entries = media.image_entries(path)
            doomed = {entries[i] for i in indices if 0 <= i < len(entries)}
            if doomed:
                left = media.remove_entries(path, doomed)
                if left == 0:
                    # nothing left — drop the archive + sidecar, like move does;
                    # a downloaded-but-empty chapter is a lie in the UI
                    self.vault.delete_chapter_media(title_id, chapter_id)
                else:
                    side = self.vault.chapter_sidecars(title_id).get(safe_id(chapter_id), {})
                    side["pages"] = left
                    side["size"] = path.stat().st_size
                    self.vault.write_chapter_sidecar(title_id, chapter_id, side)
        self.index.upsert(title_id, doc)
        return self._out(title_id, doc)

    def delete(self, title_id: str) -> bool:
        existed = self.vault.delete(title_id)
        self.index.remove(title_id)
        return existed
