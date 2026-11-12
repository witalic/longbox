"""The library's downloads folder (design/subtitles.md §2).

What the browser downloads with no entry armed for it lands here, in
`<vault>/downloads/` — beside the shelves and never read as one
(vault.VAULT_FOLDERS). Any file is kept: a subtitle, an archive, an image or a
video waits to be filed into an entry, anything else just waits. Where each
file came from is kept in `downloads/index.json`; a file put in the folder by
hand is listed too."""
from __future__ import annotations

import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel

from . import media, subtitles
from .vault import DOWNLOADS_DIR, UnreadableDocument, Vault

DIR = DOWNLOADS_DIR
DOC = f"{DIR}/index.json"
SCHEMA = 1  # the shape index.json is written in (Vault.doc_transaction)

KINDS = (("subtitle", subtitles.SUBTITLE_EXTS), ("archive", media.ARCHIVE_EXTS),
         ("image", media.IMAGE_EXTS), ("video", media.VIDEO_EXTS))


def kind_of(name: str) -> str:
    """What a file is to the library — 'file' for one no entry takes."""
    ext = Path(name).suffix.lower()
    return next((kind for kind, exts in KINDS if ext in exts), "file")


# the folder's own files, never a download: its index, a write in flight
# (media.tmp_path), and what a file manager leaves in a folder it has shown
_INDEX = Path(DOC).name
_IN_FLIGHT = re.compile(r"\.[0-9a-f]{8}\.tmp$")
_OS_FILES = {"desktop.ini", "thumbs.db", ".ds_store"}


def _is_download(name: str) -> bool:
    low = name.lower()
    return low != _INDEX and low not in _OS_FILES and not _IN_FLIGHT.search(low)


class Arrival(BaseModel):
    """Where a file in the folder came from."""
    name: str
    fileUrl: str = ""
    pageUrl: str = ""
    at: str = ""


class LooseFile(BaseModel):
    name: str
    kind: str
    size: int
    at: str             # when it arrived (ISO) — or, put there by hand, when it last changed
    fileUrl: str = ""
    pageUrl: str = ""
    lang: str = ""      # a subtitle's language, read off its name


# what Windows refuses in a name, and what a path could be made of
_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def clean_name(name: str) -> str:
    base = _BAD.sub("_", Path(name.replace("\\", "/")).name).strip(" .")
    return base or "download"


class LooseStore:
    def __init__(self, vault: Vault) -> None:
        self.vault = vault

    @property
    def folder(self) -> Path:
        return self.vault.root / DIR

    def _arrivals(self) -> dict[str, Arrival]:
        try:
            doc = self.vault.read_doc(DOC)
            return {a.name: a for a in Vault.doc_items(doc, DOC, "files", Arrival, "a download",
                                                       schema=SCHEMA)}
        except UnreadableDocument:
            return {}  # where they came from is a nicety; the files are what matter

    def files(self) -> list[LooseFile]:
        """Every download the folder holds, newest first — one listing, whose
        entries carry their stat data (no round trip per file on a share)."""
        try:
            with os.scandir(self.folder) as items:
                entries = [e for e in items if e.is_file() and _is_download(e.name)]
        except OSError:
            return []
        known = self._arrivals()
        out = []
        for p in entries:
            kind = kind_of(p.name)
            st = p.stat()
            came = known.get(p.name)
            at = came.at if came and came.at else datetime.fromtimestamp(
                st.st_mtime, timezone.utc).isoformat(timespec="seconds")
            out.append(LooseFile(name=p.name, kind=kind, size=st.st_size, at=at,
                                 fileUrl=came.fileUrl if came else "",
                                 pageUrl=came.pageUrl if came else "",
                                 lang=subtitles.guess_lang(p.name) if kind == "subtitle" else ""))
        return sorted(out, key=lambda f: f.at, reverse=True)

    def count(self) -> int:
        """How many downloads the folder holds — what the activity corner
        shows, off one listing: no file stat-ed, no record read."""
        try:
            with os.scandir(self.folder) as items:
                return sum(1 for e in items if e.is_file() and _is_download(e.name))
        except OSError:
            return 0

    def path(self, name: str) -> Path | None:
        """A file in the folder by its name — never a path out of it."""
        if clean_name(name) != name:
            return None
        p = self.folder / name
        return p if p.is_file() and _is_download(name) else None

    def arrival(self, name: str) -> Arrival | None:
        return self._arrivals().get(name)

    def take(self, src: Path, filename: str, file_url: str = "", page_url: str = "") -> LooseFile:
        """Move a finished download in, under the name it came with — a name
        already there — or one of the folder's own — gets ` (2)`, ` (3)`… rather
        than replacing it."""
        name = clean_name(filename or src.name)
        self.vault.make_dir(self.folder)
        stem, ext = Path(name).stem, Path(name).suffix
        final, n = self.folder / name, 1
        while final.exists() or not _is_download(final.name):
            n += 1
            final = self.folder / f"{stem} ({n}){ext}"
        tmp = media.tmp_path(final)
        shutil.move(str(src), tmp)  # may cross filesystems (temp → vault)
        media.replace_atomically(tmp, final)
        at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            with self.vault.doc_transaction(DOC, schema=SCHEMA) as doc:
                kept = [a for a in Vault.doc_items(doc, DOC, "files", Arrival, "a download", schema=SCHEMA)
                        if (self.folder / a.name).is_file()]  # files removed by hand leave the list
                doc["files"] = [*(a.model_dump() for a in kept),
                                Arrival(name=final.name, fileUrl=file_url, pageUrl=page_url, at=at).model_dump()]
        except UnreadableDocument:
            pass  # the file is in; only where it came from goes unrecorded
        return LooseFile(name=final.name, kind=kind_of(final.name), size=final.stat().st_size, at=at,
                         fileUrl=file_url, pageUrl=page_url,
                         lang=subtitles.guess_lang(final.name) if kind_of(final.name) == "subtitle" else "")

    def remove(self, name: str) -> bool:
        p = self.path(name)
        if p is None:
            return False
        p.unlink()
        try:
            with self.vault.doc_transaction(DOC, schema=SCHEMA) as doc:
                doc["files"] = [a.model_dump() for a in Vault.doc_items(doc, DOC, "files", Arrival,
                                                                         "a download", schema=SCHEMA)
                                if a.name != name]
        except UnreadableDocument:
            pass
        return True
