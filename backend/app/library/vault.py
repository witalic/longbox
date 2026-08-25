"""The on-disk vault — the source of truth (design/state-model.md §8).

Layout: one directory per title under the library root:

    <root>/<title-id>/title.json      the layered document
    <root>/<title-id>/cover.<ext>     captured cover bytes
    <root>/<title-id>/chapters/       reserved for chapter media (next phase)

Writes are atomic (`tmp → rename`) and serialized per title, so a meta commit and
a user-layer write-through can never interleave and roll each other back. The
SQLite index is a rebuildable cache over these files; deleting it never loses
content.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
import time
import zipfile
from collections import defaultdict
from pathlib import Path

from . import media
from .migrations import migrate
from .models import CustomFieldDef, DraftIn, TitleDoc, UserPatch

_SAFE_ID = re.compile(r"[^a-z0-9._-]+")


def _unlink_stubborn(path: Path, tries: int = 5) -> None:
    """Delete a file Windows may still be holding.

    A stream that just ended releases its handle a moment later, so a short
    retry turns the common case — deleting the episode you were watching — from
    a failure into a pause. What does NOT go away is reported as itself, not as
    a traceback out of the API."""
    for attempt in range(tries):
        try:
            path.unlink(missing_ok=True)
            return
        except PermissionError:
            if attempt == tries - 1:
                raise media.MediaInUseError(path.name)
            time.sleep(0.12)

# The stills an episode can carry, cut by the window (frames.ts) and kept in the
# vault beside the media. They live in the chapters directory next to the file
# they came from, so nothing here may ever be mistaken for the media itself.
FRAME_KINDS = ("poster", "sheet")

# Ukrainian/Russian Cyrillic → Latin, so a Cyrillic title gets a readable slug.
_CYR = {
    "а": "a", "б": "b", "в": "v", "г": "h", "ґ": "g", "д": "d", "е": "e", "є": "ie",
    "ж": "zh", "з": "z", "и": "y", "і": "i", "ї": "i", "й": "i", "к": "k", "л": "l",
    "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch", "ь": "",
    "ю": "iu", "я": "ia", "ъ": "", "ы": "y", "э": "e", "ё": "e",
}

_COVER_EXTS = ("jpg", "jpeg", "png", "webp", "gif", "avif")

# Bump when the archive-normalization pass learns something new — every vault
# then earns exactly ONE more sweep.
NORMALIZE_VERSION = 1
FASTSTART_VERSION = 1


def _translit(s: str) -> str:
    return "".join(_CYR.get(c, c) for c in s)


def safe_id(raw: str) -> str:
    """A filesystem-safe id derived from a title/slug. Cyrillic is transliterated;
    scripts we can't transliterate (CJK, …) fall back to a stable short hash so
    distinct titles don't all collide on the same slug."""
    slug = _SAFE_ID.sub("-", _translit(raw.strip().lower())).strip("-.")
    if slug:
        return slug
    return "t-" + hashlib.sha1(raw.strip().encode("utf-8")).hexdigest()[:10]


def type_dir_name(type_str: str) -> str:
    """The per-TYPE shelf a title lives on: `<root>/<type-dir>/<title-id>/`.
    Typeless titles land in `other`."""
    t = (type_str or "").strip().lower()
    return safe_id(t) if t else "other"


def _atomic_write(path: Path, data: bytes) -> None:
    tmp = media.tmp_path(path)
    tmp.write_bytes(data)
    try:
        media.replace_atomically(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)  # only survives a rename that never happened


class Vault:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        # One serialized write point per title (state-model §8): FastAPI runs sync
        # endpoints on a threadpool, so a commit and a write-through may race.
        # RLock so a service-level critical section (title_lock) can call the
        # locking vault methods it is composed of without deadlocking.
        self._locks: dict[str, threading.RLock] = defaultdict(threading.RLock)
        self._locks_guard = threading.Lock()
        self._authors_lock = threading.Lock()
        self._fields_lock = threading.Lock()
        # title-id → type-dir name (the shelf the title currently sits on)
        self._loc: dict[str, str] = {}
        self._migrate_flat_layout()
        # NOTE: normalize_chapter_archives() is NOT called here — it is a ONE-TIME
        # migration per vault (the Library runs it in the background when the
        # marker below says this vault hasn't had it yet), never a startup chore.

    # ---- the library's own fields ----
    #
    # Field definitions describe THIS library's data, so they live with the data
    # and travel with it — not in the app config, which stays behind when the
    # library path changes or the vault moves to another machine.

    def _fields_path(self) -> Path:
        return self.root / "fields.json"

    def custom_fields(self) -> list[CustomFieldDef]:
        """User-defined fields, or none if this vault never had any."""
        p = self._fields_path()
        if not p.is_file():
            return []
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        out: list[CustomFieldDef] = []
        for item in raw.get("fields") or []:
            try:
                out.append(CustomFieldDef.model_validate(item))
            except ValueError:
                continue  # one broken definition must not cost the others
        return out

    def save_custom_fields(self, defs: list[CustomFieldDef]) -> None:
        with self._fields_lock:
            payload = {"schema": 1, "fields": [d.model_dump() for d in defs]}
            _atomic_write(self._fields_path(),
                          json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"))

    # ---- the per-type layout ----

    def _migrate_flat_layout(self) -> None:
        """One-time migration: legacy `<root>/<id>/` directories move onto
        their type shelf `<root>/<type-dir>/<id>/` (typeless → other)."""
        for p in list(self.root.iterdir()):
            if not p.is_dir() or not (p / "title.json").is_file():
                continue
            try:
                doc = TitleDoc.model_validate_json((p / "title.json").read_text(encoding="utf-8"))
                shelf = type_dir_name(doc.meta.type)
            except Exception:  # noqa: BLE001 — an unreadable doc still gets a home
                shelf = "other"
            dest = self.root / shelf / p.name
            if dest.exists():
                continue  # freak collision — leave the legacy dir in place
            try:
                src = p
                if p.name == shelf:
                    # a legacy title dir NAMED like its own shelf ("manga" of
                    # type manga) — moving it into itself would fail; step aside
                    src = self.root / (p.name + ".lb-migrating")
                    p.rename(src)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dest))
                self._loc[p.name] = shelf
            except OSError:
                continue  # a stuck legacy dir must never block startup

    def _scan_one(self, path: str) -> tuple[int, int, str, int, int] | None:
        """One title directory read as a single listing: the document's mtime,
        the chapter directory's mtime, and the cover file with its mtime and
        size (a version built on the timestamp alone can repeat)."""
        doc_at = ch_at = 0
        covers: dict[str, tuple[str, int, int]] = {}
        try:
            with os.scandir(path) as items:
                for it in items:
                    name = it.name.lower()
                    if name == "title.json":
                        doc_at = it.stat().st_mtime_ns
                    elif name == "chapters" and it.is_dir():
                        ch_at = it.stat().st_mtime_ns
                    elif name.startswith("cover."):
                        ext = name.rsplit(".", 1)[-1]
                        if ext in _COVER_EXTS:
                            st = it.stat()
                            covers[ext] = (it.name, st.st_mtime_ns, st.st_size)
        except OSError:
            return None
        if not doc_at:
            return None
        # the same precedence cover_path() applies, so both agree on which file wins
        for ext in _COVER_EXTS:
            if ext in covers:
                return (doc_at, ch_at, *covers[ext])
        return (doc_at, ch_at, "", 0, 0)

    def scan(self) -> dict[str, tuple[int, int, str, int, int]]:
        """Every title in the vault with the stamps a listing needs, in ONE
        directory pass. A scandir entry already carries its stat data, so this
        costs one round trip per directory instead of three per title — the
        difference between "instant" and "ten seconds" on a network vault."""
        out: dict[str, tuple[int, int, str, int, int]] = {}
        try:
            shelves = list(os.scandir(self.root))
        except OSError:
            return out
        for shelf in shelves:
            if not shelf.is_dir():
                continue
            legacy = self._scan_one(shelf.path)  # an unmigrated title dir on the root
            if legacy is not None:
                out[shelf.name] = legacy
                continue
            try:
                with os.scandir(shelf.path) as titles:
                    for t in titles:
                        if not t.is_dir():
                            continue
                        got = self._scan_one(t.path)
                        if got is not None:
                            out[t.name] = got
                            self._loc[t.name] = shelf.name
            except OSError:
                continue
        return out

    def _find(self, sid: str) -> Path | None:
        """The title's CURRENT directory, wherever its shelf is."""
        shelf = self._loc.get(sid)
        if shelf and (self.root / shelf / sid / "title.json").is_file():
            return self.root / shelf / sid
        for td in self.root.iterdir():
            if td.is_dir() and (td / sid / "title.json").is_file():
                self._loc[sid] = td.name
                return td / sid
        if (self.root / sid / "title.json").is_file():
            return self.root / sid  # unmigrated legacy leftover
        return None

    def _relocate_for_type(self, sid: str, type_str: str) -> None:
        """Make the title live on the shelf its TYPE dictates — a type change
        physically moves the directory (and sweeps the emptied shelf)."""
        target = type_dir_name(type_str)
        cur = self._find(sid)
        if cur is None:
            self._loc[sid] = target
            return
        if cur.parent == self.root / target:
            self._loc[sid] = target
            return
        dest = self.root / target / sid
        if dest.exists():
            self._loc[sid] = cur.parent.name if cur.parent != self.root else "other"
            return
        dest.parent.mkdir(parents=True, exist_ok=True)
        old_parent = cur.parent
        shutil.move(str(cur), str(dest))
        self._loc[sid] = target
        try:
            if old_parent != self.root and not any(old_parent.iterdir()):
                old_parent.rmdir()
        except OSError:
            pass

    def _lock(self, title_id: str) -> threading.RLock:
        # keyed by safe_id — the same key the filesystem uses, so two spellings
        # of one id can never write the same title.json under different locks
        with self._locks_guard:
            return self._locks[safe_id(title_id)]

    def title_lock(self, title_id: str) -> threading.RLock:
        """The title's write lock, for service-level load→mutate→commit
        sequences that must be ONE critical section (reentrant, so the locking
        vault methods inside still work)."""
        return self._lock(title_id)

    def _dir(self, title_id: str) -> Path:
        sid = safe_id(title_id)
        found = self._find(sid)
        return found if found is not None else self.root / self._loc.get(sid, "other") / sid

    def _dir_fast(self, title_id: str) -> Path:
        """Where the title's directory is according to the location cache, with
        NO verifying stat. For callers whose next act is a stat anyway: on a
        network vault that verification doubles the round trips, and a stale
        cache entry simply surfaces as the OSError those callers already
        handle."""
        sid = safe_id(title_id)
        shelf = self._loc.get(sid)
        return self.root / shelf / sid if shelf else self._dir(title_id)

    def _doc_path(self, title_id: str) -> Path:
        return self._dir(title_id) / "title.json"

    # ---- documents ----

    def exists(self, title_id: str) -> bool:
        return self._doc_path(title_id).is_file()

    def load(self, title_id: str) -> TitleDoc | None:
        """The title's document, upgraded to the shape this build writes. The
        upgrade happens on the way IN and is not written back here — the next
        commit persists it, so a read never touches the user's files."""
        path = self._doc_path(title_id)
        if not path.is_file():
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw, _ = migrate(raw)
        return TitleDoc.model_validate(raw)

    def touched_at(self, title_id: str) -> int:
        """When the title's document was last written — the recency the library's
        default sort means (a rebuilt cache must not reorder the shelf)."""
        try:
            return (self._dir_fast(title_id) / "title.json").stat().st_mtime_ns
        except OSError:
            return 0

    def list_ids(self) -> list[str]:
        """Scan the type shelves for title directories (holding a title.json)."""
        if not self.root.is_dir():
            return []
        ids: set[str] = set()
        for td in self.root.iterdir():
            if not td.is_dir():
                continue
            if (td / "title.json").is_file():
                ids.add(td.name)  # unmigrated legacy leftover
                continue
            for c in td.iterdir():
                if c.is_dir() and (c / "title.json").is_file():
                    ids.add(c.name)
                    self._loc[c.name] = td.name
        return sorted(ids)

    def _save(self, title_id: str, doc: TitleDoc) -> None:
        self._dir(title_id).mkdir(parents=True, exist_ok=True)
        payload = doc.model_dump_json(indent=2, by_alias=True).encode("utf-8")
        _atomic_write(self._doc_path(title_id), payload)

    def commit_meta(self, title_id: str, draft: DraftIn, *, create: bool = False) -> TitleDoc | None:
        """Write the draft's layers (meta + provenance + chapters), preserving the
        user layer untouched — a commit can never roll back a star or read progress."""
        with self._lock(title_id):
            existing = self.load(title_id)
            if existing is None and not create:
                return None
            # the TYPE decides the shelf — a change moves the whole directory
            self._relocate_for_type(safe_id(title_id), draft.meta.type)
            doc = TitleDoc(meta=draft.meta, provenance=draft.provenance, chapters=draft.chapters)
            if existing is not None:
                doc.user = existing.user
            self._save(title_id, doc)
            return doc

    def patch_user(self, title_id: str, patch: UserPatch) -> TitleDoc | None:
        """Instant write-through of the user layer; the meta layers stay untouched."""
        with self._lock(title_id):
            doc = self.load(title_id)
            if doc is None:
                return None
            if patch.fav is not None:
                doc.user.fav = patch.fav
            if patch.rating is not None:
                doc.user.rating = max(0, min(5, patch.rating))
            if patch.read:
                doc.user.read.update(patch.read)
            if patch.position:
                # a resume point, not a rating: 0 means "start over", so it is
                # stored as sent rather than treated as "no value"
                doc.user.position.update({k: max(0.0, float(v)) for k, v in patch.position.items()})
            self._save(title_id, doc)
            return doc

    def delete(self, title_id: str) -> bool:
        with self._lock(title_id):
            d = self._dir(title_id)
            if not d.is_dir():
                return False
            parent = d.parent
            shutil.rmtree(d)
            self._loc.pop(safe_id(title_id), None)
            try:
                if parent != self.root and not any(parent.iterdir()):
                    parent.rmdir()
            except OSError:
                pass
            return True

    # ---- chapter media (downloaded archives + provenance sidecars) ----
    # VAULT INVARIANT: `chapters/<chapter-id>.zip` is ALWAYS a plain zip — cbz
    # keeps its bytes under the .zip name, rar/7z are repacked at ingest, and
    # an unreadable archive is rejected rather than stored opaque. So every
    # page operation works on every stored chapter. `chapters/<chapter-id>.json`
    # records where the file came from (the download source is independent of
    # the title's metadata source — different chapters and languages may come
    # from different sites).

    def _chapters_dir(self, title_id: str) -> Path:
        return self._dir(title_id) / "chapters"

    def chapter_archive_target(self, title_id: str, chapter_id: str) -> Path:
        """Where a NEW chapter archive lives (zip) — for pages added by hand to
        a row that has no file yet. Ensures the directory exists."""
        d = self._chapters_dir(title_id)
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{safe_id(chapter_id)}.zip"

    def chapters_dir(self, title_id: str) -> Path:
        """Where the title's chapter media lives (may not exist yet)."""
        return self._chapters_dir(title_id)

    def chapters_stamp(self, title_id: str) -> int:
        """When the title's chapter directory last changed. Chapter media is not
        part of `title.json`, so this is the second half of "has anything about
        this title changed since the index saw it"."""
        try:
            return (self._dir_fast(title_id) / "chapters").stat().st_mtime_ns
        except OSError:
            return 0

    def chapter_media_path(self, title_id: str, chapter_id: str) -> Path | None:
        """The chapter's stored file. `.zip` WINS for page media: a leftover from
        an interrupted conversion (the pre-conversion `.7z`, a half-written
        `.tmp`) must never be served in place of the real thing. A video chapter
        has no zip — its own container IS the file."""
        stem = safe_id(chapter_id)
        d = self._chapters_dir(title_id)
        if not d.is_dir():
            return None
        canonical = d / f"{stem}.zip"
        if canonical.is_file():
            return canonical
        for ext in sorted(media.VIDEO_EXTS):
            video = d / f"{stem}{ext}"
            if video.is_file():
                return video
        for p in sorted(d.glob(f"{stem}.*")):
            if (p.is_file() and p.suffix != ".json" and not p.name.endswith(".tmp")
                    and not any(p.name.endswith(f".{k}.jpg") for k in FRAME_KINDS)):
                return p
        return None

    def chapter_frames_path(self, title_id: str, chapter_id: str, kind: str) -> Path:
        """Where an episode's stored stills live: beside their media, in the vault.

        Two kinds, cut in the same pass — `poster` is the single frame a tile
        wears, `sheet` the contact grid a title page shows until playback is
        asked for. Beside the media, not in a cache directory: the vault is the
        source of truth, and a frame the app decoded once should survive a
        rebuilt index, a moved library and a machine change, instead of being
        re-pulled from the video."""
        return self._chapters_dir(title_id) / f"{safe_id(chapter_id)}.{kind}.jpg"

    def write_chapter_frames(self, title_id: str, chapter_id: str, kind: str,
                             data: bytes) -> None:
        with self._lock(title_id):
            self._chapters_dir(title_id).mkdir(parents=True, exist_ok=True)
            _atomic_write(self.chapter_frames_path(title_id, chapter_id, kind), data)

    def chapter_sidecars(self, title_id: str) -> dict[str, dict]:
        """All chapter sidecars, keyed by the chapter file stem (safe id)."""
        d = self._chapters_dir(title_id)
        out: dict[str, dict] = {}
        if not d.is_dir():
            return out
        for p in d.glob("*.json"):
            try:
                out[p.stem] = json.loads(p.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                continue
        return out

    def write_chapter_sidecar(self, title_id: str, chapter_id: str, sidecar: dict) -> None:
        with self._lock(title_id):
            d = self._chapters_dir(title_id)
            d.mkdir(parents=True, exist_ok=True)
            _atomic_write(d / f"{safe_id(chapter_id)}.json",
                          json.dumps(sidecar, indent=2, ensure_ascii=False).encode("utf-8"))

    def ingest_chapter_media(self, title_id: str, chapter_id: str, src: Path, sidecar: dict,
                             *, filename: str = "") -> Path:
        """Move a downloaded/imported file into the vault and write its sidecar.

        PAGE media becomes `<stem>.zip`: zip content (incl. cbz) moves as-is,
        rar/7z are repacked, and an unreadable archive raises
        UnsupportedArchiveError BEFORE anything in the vault is touched. VIDEO
        media is stored as the file itself under its own extension — an episode
        gains nothing from a zip and would pay a full rewrite for every edit.
        The sidecar always describes the file actually stored.
        """
        video_ext = Path(filename or src.name).suffix.lower()
        as_video = video_ext in media.VIDEO_EXTS
        if as_video and not media.looks_like_video(src):
            raise media.UnsupportedArchiveError(
                f"unsupported video file: {video_ext or 'unknown'} content does not match")
        with self._lock(title_id):
            d = self._chapters_dir(title_id)
            d.mkdir(parents=True, exist_ok=True)
            stem = safe_id(chapter_id)
            final = d / (f"{stem}{video_ext}" if as_video else f"{stem}.zip")
            tmp = media.tmp_path(final)
            if as_video:
                shutil.move(str(src), tmp)
            elif zipfile.is_zipfile(src):
                shutil.move(str(src), tmp)  # may cross filesystems (temp → vault)
            else:
                media.repack_to_zip(src, tmp)  # raises before the vault changes
                src.unlink(missing_ok=True)
            # the new archive lands FIRST; only then do the old ones go. The
            # reverse order leaves the chapter with no media at all if anything
            # fails in between.
            media.replace_atomically(tmp, final)
            # The new media is already in place, so the leftovers of a former
            # extension (.webm replaced by .mp4) MUST go: `chapter_media_path`
            # globs, and a stale sibling would be served instead of what was
            # just downloaded. One that is still held open is renamed out of the
            # glob's way — Windows refuses to unlink an open file but renames it
            # happily, and `.tmp` is what the stray sweep collects.
            for old in d.glob(f"{stem}.*"):
                if old == final or old.suffix == ".json":
                    continue
                try:
                    _unlink_stubborn(old, tries=2)
                except media.MediaInUseError:
                    old.rename(media.tmp_path(old))
            # a re-ingest over an existing chapter continues its revision, so a
            # browser holding the previous pages cannot match the new URLs
            prev = self.chapter_sidecars(title_id).get(stem, {})
            probe = media.probe_mp4(final) if as_video else {}
            # Do it now, while the file is arriving anyway: an index behind the
            # media costs a fetch of the file's tail before the first frame, and
            # every far seek pays again. A refusal is not a failure — the
            # episode is stored either way, and the sidecar records which it is.
            if as_video and video_ext in media.FASTSTART_EXTS and not probe.get("faststart"):
                if media.remux_faststart(final):
                    probe = media.probe_mp4(final)
            sidecar = {**sidecar,
                       "kind": "video" if as_video else "pages",
                       **({"codec": probe.get("codec", ""),
                           "faststart": probe.get("faststart", False),
                           # what the app can actually open, decided ONCE at
                           # ingest — a list must not have to guess, and the
                           # human must not find out by clicking
                           "playable": video_ext in media.PLAYABLE_VIDEO_EXTS,
                           "container": video_ext.lstrip(".")} if as_video else {}),
                       "pages": 0 if as_video else len(media.image_entries(final)),
                       "size": final.stat().st_size,
                       "rev": int(prev.get("rev") or 0) + 1}
            _atomic_write(d / f"{stem}.json",
                          json.dumps(sidecar, indent=2, ensure_ascii=False).encode("utf-8"))
            return final

    # ---- the one-time archive-normalization marker ----
    # Ingest enforces the zip invariant for everything NEW, so the sweep is a
    # migration for content that predates it (or was dropped into the vault by
    # hand). It runs once per vault; Settings can re-run it on demand.

    def _vault_meta_path(self) -> Path:
        return self.root / "vault.json"

    def _vault_meta(self) -> dict:
        p = self._vault_meta_path()
        if not p.is_file():
            return {}
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return {}

    def needs_normalize(self) -> bool:
        return int(self._vault_meta().get("zipNormalized", 0)) < NORMALIZE_VERSION

    def mark_normalized(self) -> None:
        meta = {**self._vault_meta(), "zipNormalized": NORMALIZE_VERSION}
        _atomic_write(self._vault_meta_path(),
                      json.dumps(meta, indent=2, ensure_ascii=False).encode("utf-8"))

    def needs_faststart(self) -> bool:
        return int(self._vault_meta().get("videoFaststart", 0)) < FASTSTART_VERSION

    def mark_faststart(self) -> None:
        meta = {**self._vault_meta(), "videoFaststart": FASTSTART_VERSION}
        _atomic_write(self._vault_meta_path(),
                      json.dumps(meta, indent=2, ensure_ascii=False).encode("utf-8"))

    def refresh_stored_episodes(self, *, stop=None) -> int:
        """Bring stored episodes up to what the app now records: the index moved
        in front of the media where that applies, plus the two facts a listing
        must never guess — which container it is, and whether the app can play
        it. Ingest does all of this for anything arriving now; this is the
        one-time pass for what was already in the vault."""
        changed = 0
        for sid in self.list_ids():
            if stop is not None and stop.is_set():
                break  # the library is closing — the rest waits for next time
            home = self._find(sid)
            d = home / "chapters" if home is not None else None
            if d is None or not d.is_dir():
                continue
            for p in list(d.glob("*")):
                ext = p.suffix.lower()
                if ext not in media.VIDEO_EXTS or not p.is_file():
                    continue
                with self._lock(sid):
                    if not p.is_file():
                        continue
                    stamp = {"container": ext.lstrip("."),
                             "playable": ext in media.PLAYABLE_VIDEO_EXTS}
                    rewrote = False
                    if ext in media.FASTSTART_EXTS:
                        # the file is open anyway — record everything it says,
                        # not just the one property this pass came for
                        probe = media.probe_mp4(p)
                        if not probe.get("faststart") and media.remux_faststart(p):
                            rewrote, probe = True, media.probe_mp4(p)
                        stamp["faststart"] = bool(probe.get("faststart"))
                        if probe.get("codec"):
                            stamp["codec"] = probe["codec"]
                    if self._restamp_episode(d, p.stem, stamp, bump=rewrote):
                        changed += 1
        return changed

    def _restamp_episode(self, chapters: Path, stem: str, stamp: dict, *, bump: bool) -> bool:
        """The revision moves ONLY when the bytes did. A remux rearranges them
        without changing their number, so the media version would otherwise
        REPEAT — and a repeated version is what serves a stale range back.
        Describing the same bytes better must invalidate nothing."""
        side_path = chapters / f"{stem}.json"
        side: dict = {}
        if side_path.is_file():
            try:
                side = json.loads(side_path.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                side = {}
        merged = {**side, **stamp}
        if bump:
            merged["rev"] = int(side.get("rev") or 0) + 1
        if merged == side:
            return False
        _atomic_write(side_path, json.dumps(merged, indent=2, ensure_ascii=False).encode("utf-8"))
        return True

    def normalize_chapter_archives(self, *, force: bool = False, stop=None) -> int:
        """Consistency pass over the whole vault (part of the zip invariant):
        cbz files are renamed to .zip, rar/7z get repacked when a reader is
        available. A file nothing can read is left alone AND remembered in the
        sidecar (`convertFailed` = its mtime) so a later pass doesn't burn time
        on the same hopeless conversion; `force` retries those too (the point of
        the manual re-run — e.g. after installing unrar).
        Returns how many archives changed (the caller refreshes the index)."""
        changed = 0
        for sid in self.list_ids():
            if stop is not None and stop.is_set():
                break  # the library is closing — leave the rest for next time
            home = self._find(sid)
            d = home / "chapters" if home is not None else None
            if d is None or not d.is_dir():
                continue
            for p in list(d.glob("*")):
                if not p.is_file() or p.suffix == ".json" or p.name.endswith(".tmp"):
                    continue
                if p.suffix == ".zip" and zipfile.is_zipfile(p):
                    continue
                with self._lock(sid):
                    final = p.with_suffix(".zip")
                    # A stray `ch-5.cbz` next to a stored `ch-5.zip` is NOT this
                    # chapter's archive — converting it would replace 40 captured
                    # pages with whatever was dropped in. The stored zip wins,
                    # exactly as chapter_media_path resolves it; the stray file
                    # stays where it is.
                    if final != p and final.is_file() and zipfile.is_zipfile(final):
                        continue
                    side_path = d / f"{final.stem}.json"
                    side: dict = {}
                    if side_path.is_file():
                        try:
                            side = json.loads(side_path.read_text(encoding="utf-8"))
                        except (ValueError, OSError):
                            side = {}
                    try:
                        mark = f"{p.stat().st_mtime_ns:x}"
                    except OSError:
                        continue
                    if not force and side.get("convertFailed") == mark:
                        continue  # this exact file already failed — don't retry it
                    try:
                        if zipfile.is_zipfile(p):  # a cbz — same container, new name
                            if final != p:
                                os.replace(p, final)
                        else:
                            media.repack_to_zip(p, final)
                            if final != p:
                                p.unlink(missing_ok=True)
                    except (media.UnsupportedArchiveError, OSError, RuntimeError):
                        # remember the failure ONLY where a sidecar already
                        # exists: creating one would make an unreadable file
                        # look like a downloaded chapter to the whole UI
                        if side_path.is_file():
                            side["convertFailed"] = mark
                            _atomic_write(side_path,
                                          json.dumps(side, indent=2, ensure_ascii=False).encode("utf-8"))
                        continue
                    side.pop("convertFailed", None)
                    side["pages"] = len(media.image_entries(final))
                    side["size"] = final.stat().st_size
                    # a converted archive is a new file: everything cached for
                    # the old one has to miss, exactly as after a page edit
                    side["rev"] = int(side.get("rev") or 0) + 1
                    _atomic_write(side_path,
                                  json.dumps(side, indent=2, ensure_ascii=False).encode("utf-8"))
                    changed += 1
        return changed

    def delete_chapter_media(self, title_id: str, chapter_id: str) -> bool:
        """Delete a chapter's files. Raises MediaInUseError when Windows will not
        let go of one — a video the player still holds open, most often the very
        episode the human is watching while pressing delete."""
        with self._lock(title_id):
            stem = safe_id(chapter_id)
            d = self._chapters_dir(title_id)
            if not d.is_dir():
                return False
            files = sorted(d.glob(f"{stem}.*"))
            if not files:
                return False
            # ORDER MATTERS. The media is the file something may still hold, so
            # it goes first and takes the whole delete down with it if it will
            # not go — leaving the chapter exactly as it was. Deleting the
            # sidecar first (glob order) left entries that had lost every fact
            # about themselves while their video was still sitting there.
            media_path = self.chapter_media_path(title_id, chapter_id)
            if media_path is not None:
                _unlink_stubborn(media_path)
            for p in files:
                if p != media_path:
                    _unlink_stubborn(p)
            return True

    # ---- the authors user layer (favorites) ----
    # Authors are DERIVED from titles and have no directory of their own; the
    # user's favorite marks live in one small vault-level file (write-through,
    # atomic, keyed by the author's stable id).

    def _authors_path(self) -> Path:
        return self.root / "authors.json"

    def author_favorites(self) -> set[str]:
        p = self._authors_path()
        if not p.is_file():
            return set()
        try:
            return set(json.loads(p.read_text(encoding="utf-8")).get("favorites", []))
        except (ValueError, OSError):
            return set()

    def set_author_favorite(self, author_id: str, value: bool) -> set[str]:
        with self._authors_lock:  # read-modify-write: two toggles must not race
            favs = self.author_favorites()
            if value:
                favs.add(author_id)
            else:
                favs.discard(author_id)
            _atomic_write(self._authors_path(), json.dumps({"favorites": sorted(favs)}, indent=2).encode("utf-8"))
            return favs

    # ---- covers ----

    def cover_path(self, title_id: str) -> Path | None:
        d = self._dir(title_id)
        for ext in _COVER_EXTS:
            p = d / f"cover.{ext}"
            if p.is_file():
                return p
        return None

    def write_cover(self, title_id: str, data: bytes, ext: str) -> Path:
        with self._lock(title_id):
            d = self._dir(title_id)
            d.mkdir(parents=True, exist_ok=True)
            before = None
            for old in _COVER_EXTS:
                p = d / f"cover.{old}"
                try:
                    st = p.stat()
                    before = (st.st_mtime_ns, st.st_size)
                except OSError:
                    pass
                p.unlink(missing_ok=True)
            path = d / f"cover.{ext}"
            _atomic_write(path, data)
            # The cover is cached under (mtime, size). A replacement of the same
            # size landing inside one filesystem tick would reuse that key — and
            # a network share can round timestamps to whole seconds. Make the
            # stamp differ by construction rather than hope it does.
            st = path.stat()
            if before == (st.st_mtime_ns, st.st_size):
                bump = st.st_mtime_ns + 10_000_000  # 10ms: past any rounding
                os.utime(path, ns=(bump, bump))
            return path

    def delete_cover(self, title_id: str) -> None:
        with self._lock(title_id):
            for ext in _COVER_EXTS:
                (self._dir(title_id) / f"cover.{ext}").unlink(missing_ok=True)
