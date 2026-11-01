"""The library's downloads folder (design/subtitles.md §2).

What the browser downloads with no entry armed for it lands here, in
`<vault>/downloads/`, until it is filed — beside the shelves and never read as
one (vault.VAULT_FOLDERS). Only what the library can use is kept: subtitles,
archives, images and videos. Where each file came from is kept in
`downloads/index.json`; a file put in the folder by hand is listed too."""
from __future__ import annotations

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
    """What a file is to the library — '' for one it cannot use."""
    ext = Path(name).suffix.lower()
    return next((kind for kind, exts in KINDS if ext in exts), "")


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
        """Everything the folder holds that the library can use, newest first."""
        try:
            entries = [p for p in self.folder.iterdir() if p.is_file()]
        except OSError:
            return []
        known = self._arrivals()
        out = []
        for p in entries:
            kind = kind_of(p.name)
            if not kind or p.name.endswith(".tmp"):
                continue
            st = p.stat()
            came = known.get(p.name)
            at = came.at if came and came.at else datetime.fromtimestamp(
                st.st_mtime, timezone.utc).isoformat(timespec="seconds")
            out.append(LooseFile(name=p.name, kind=kind, size=st.st_size, at=at,
                                 fileUrl=came.fileUrl if came else "",
                                 pageUrl=came.pageUrl if came else "",
                                 lang=subtitles.guess_lang(p.name) if kind == "subtitle" else ""))
        return sorted(out, key=lambda f: f.at, reverse=True)

    def path(self, name: str) -> Path | None:
        """A file in the folder by its name — never a path out of it."""
        if clean_name(name) != name:
            return None
        p = self.folder / name
        return p if p.is_file() and kind_of(name) else None

    def arrival(self, name: str) -> Arrival | None:
        return self._arrivals().get(name)

    def take(self, src: Path, filename: str, file_url: str = "", page_url: str = "") -> LooseFile:
        """Move a finished download in, under the name it came with — a name
        already there gets ` (2)`, ` (3)`… rather than replacing it."""
        name = clean_name(filename or src.name)
        self.folder.mkdir(parents=True, exist_ok=True)
        stem, ext = Path(name).stem, Path(name).suffix
        final, n = self.folder / name, 1
        while final.exists():
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
