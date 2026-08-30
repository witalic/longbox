"""ComicInfo.xml — the one metadata format every comic reader already speaks.

The vault is the source of truth and `title.json` is its shape, but that shape
is ours alone: a collection whose metadata lives only there is readable by
exactly one program. A ComicInfo.xml INSIDE each archive makes every chapter
self-describing — Komga, Kavita and LANraragi read the library as it stands,
and it outlives longbox.

Deliberately a MIRROR, never a second source of truth. Writing happens where
the archive is being rewritten anyway (ingest, page edits, an explicit refresh);
a metadata edit does not silently rewrite gigabytes to keep a copy in sync.

Only the fields with an honest counterpart are mapped. Inventing a
correspondence — `Genre` for our tags, say — would make a round trip lose the
distinction, which is worse than leaving the element out.
"""
from __future__ import annotations

import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from . import media
from .models import ChapterRow, TitleDoc

NAME = "ComicInfo.xml"

# Our status vocabulary is not ComicInfo's, and ComicInfo has no field for it;
# what it does have is a per-issue "the series ended here" flag, which is not
# the same statement. Left out on purpose.
_LIST_SEP = ", "


def _text(el: ET.Element | None) -> str:
    return (el.text or "").strip() if el is not None else ""


def _num(label: str) -> str:
    """The numeric part of a free-form label, which is all `Number` can hold.
    A label with no number ("Extra") stays out rather than arriving as junk."""
    m = re.match(r"\s*(\d+(?:[.,]\d+)?)", label)
    return m.group(1).replace(",", ".") if m else ""


def build(doc: TitleDoc, ch: ChapterRow) -> bytes:
    """The XML for one chapter of one title."""
    m = doc.meta
    root = ET.Element("ComicInfo", {
        "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
        "xmlns:xsd": "http://www.w3.org/2001/XMLSchema",
    })

    def put(tag: str, value: str) -> None:
        if value:
            ET.SubElement(root, tag).text = value

    put("Series", m.title)
    put("Number", _num(ch.num))
    put("Title", ch.title)
    put("Summary", m.desc)
    put("Year", _num(m.year))
    put("Writer", _LIST_SEP.join(m.authors))
    put("Penciller", _LIST_SEP.join(m.artists))
    put("Publisher", _LIST_SEP.join(m.studio))
    put("Genre", _LIST_SEP.join(m.genres))
    put("Tags", _LIST_SEP.join(m.tags))
    put("Characters", _LIST_SEP.join(m.characters))
    put("AlternateSeries", m.alt)
    put("LanguageISO", ch.lang.lower())
    put("ScanInformation", ch.group)
    put("Web", ch.url or m.source.url)
    if m.flags.adult:
        put("AgeRating", "Adults Only 18+")
    ET.indent(root, space="  ")
    return b'<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(root, encoding="utf-8")


def parse(data: bytes) -> dict:
    """What an incoming ComicInfo.xml says, in OUR field names.

    Everything absent is simply missing from the result — a caller must be able
    to tell "the file said nothing" from "the file said empty", or a blank
    element would erase a value the library already holds."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return {}
    if root.tag != "ComicInfo":
        return {}
    out: dict = {}

    def take(tag: str, key: str, as_list: bool = False) -> None:
        value = _text(root.find(tag))
        if not value:
            return
        out[key] = ([v.strip() for v in value.split(",") if v.strip()] if as_list else value)

    take("Series", "title")
    take("Number", "num")
    take("Title", "chapterTitle")
    take("Summary", "desc")
    take("Year", "year")
    take("AlternateSeries", "alt")
    take("LanguageISO", "lang")
    take("ScanInformation", "group")
    take("Web", "url")
    take("Genre", "genres", as_list=True)
    take("Tags", "tags", as_list=True)
    take("Characters", "characters", as_list=True)
    take("Publisher", "studio", as_list=True)
    take("Penciller", "artists", as_list=True)
    # Writer covers several credits in the wild; the first one carrying names wins
    for tag in ("Writer", "Author", "Creator"):
        take(tag, "authors", as_list=True)
        if out.get("authors"):
            break
    return out


def read_from(path: Path) -> dict:
    """The ComicInfo an archive carries, if any. Never raises: a chapter that
    cannot be opened is the revision pass's business, not this module's."""
    if not path.is_file() or not zipfile.is_zipfile(path):
        return {}
    try:
        with zipfile.ZipFile(path) as z:
            name = next((n for n in z.namelist() if n.rsplit("/", 1)[-1].lower()
                         == NAME.lower()), "")
            return parse(z.read(name)) if name else {}
    except (zipfile.BadZipFile, OSError, RuntimeError, KeyError):
        return {}


def write_into(path: Path, data: bytes) -> bool:
    """Put (or replace) the ComicInfo of a stored archive.

    A zip entry cannot be replaced in place, so this is a full rewrite — the
    same atomic `tmp → rename` every page edit takes. Returns False when the
    archive already carries exactly this, so a refresh over a whole vault only
    touches what actually changed."""
    if not path.is_file() or not zipfile.is_zipfile(path):
        return False
    try:
        with zipfile.ZipFile(path) as z:
            existing = [n for n in z.namelist()
                        if n.rsplit("/", 1)[-1].lower() == NAME.lower()]
            if existing and len(existing) == 1 and z.read(existing[0]) == data:
                return False
            keep = [(i.filename, z.read(i.filename)) for i in z.infolist()
                    if not i.filename.endswith("/") and i.filename not in existing]
    except (zipfile.BadZipFile, OSError, RuntimeError) as e:
        raise media.UnsupportedArchiveError(f"{path.name} cannot be rewritten: {e}") from e
    tmp = media.tmp_path(path)
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED) as dst:
        for name, blob in keep:
            dst.writestr(name, blob)
        dst.writestr(NAME, data)
    media.replace_atomically(tmp, path)
    return True
