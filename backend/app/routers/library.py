"""Library endpoints: queries, draft commits (meta layers), the write-through
user layer, and cover bytes. See design/state-model.md §12 for the surface.
"""
from __future__ import annotations

import base64
import binascii
import re
import tempfile
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, File, HTTPException, Query, Request, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from ..config_store import config_transaction, load_config
from ..library import fields, media
from ..library.models import (
    Bookmark, BrowseGroup, CustomFieldDef, DraftIn, FacetCounts, Source, TitleOut, UserPatch,
)
from ..library.media import MediaInUseError
from ..library.service import Library
from ..scraper.covers import fetch_cover

router = APIRouter(prefix="/api")

# derived from the ONE map in media.py (which stores the files), so a new format
# cannot land in a router and miss the module that actually writes it
_CT_BY_EXT = {ext.lstrip("."): ct for ext, ct in media.CT_BY_EXT.items()}
_EXT_BY_CT: dict[str, str] = {}
for _ext, _ct in media.CT_BY_EXT.items():
    _EXT_BY_CT.setdefault(_ct, _ext.lstrip("."))  # first spelling wins: jpeg -> jpg
_EXT_BY_CT["image/jpg"] = "jpg"  # some sites serve this non-standard type
_MAX_COVER_BYTES = 8 * 1024 * 1024
_MAX_PAGE_BYTES = 24 * 1024 * 1024  # a single manga page; webtoon strips get large
# The client posts a page view's images in small batches; a request far beyond
# that is either a bug or an attempt to make the sidecar buffer gigabytes.
_MAX_PAGES_PER_CAPTURE = 32


def _lib(request: Request) -> Library:
    return request.app.state.library


def _by_field(pairs: list[str]) -> dict[str, tuple[str, ...]]:
    """`field:value` strings into {field id: (values, …)}.

    The FIRST colon separates: a field id never contains one, a tag well might.
    An id no longer in the registry is dropped rather than refused — a filter
    saved before a custom field was deleted must not 400 the whole library."""
    out: dict[str, list[str]] = {}
    for pair in pairs:
        fid, sep, value = pair.partition(":")
        if sep and value and fid in fields.by_id():
            out.setdefault(fid, []).append(value)
    return {k: tuple(v) for k, v in out.items()}


def _selection(search: str | None, progress: str | None, fav: bool,
               min_rating: int | None, f: list[str], nf: list[str]) -> dict:
    return {
        "search": search, "progress": progress, "fav": fav or None, "min_rating": min_rating,
        "include": _by_field(f), "exclude": _by_field(nf),
    }


class FieldOut(BaseModel):
    """A metadata field as the UI needs to know it. Served so that a field the
    app gains — or the user defines — is rendered without a frontend change."""
    id: str
    label: str
    type: str
    control: str
    builtin: bool
    required: bool
    editable: bool
    facet: bool
    placeholder: str
    vocab: str
    group: str  # which block of the editor draws it


@router.get("/fields", response_model=list[FieldOut])
def list_fields() -> list[FieldOut]:
    return [FieldOut(id=f.id, label=f.label, type=f.type, control=f.control,
                     builtin=f.builtin, required=f.required, editable=f.editable,
                     facet=f.facet, placeholder=f.placeholder, vocab=f.vocab or f.id,
                     group=f.group)
            for f in fields.registry()]


class FieldIn(BaseModel):
    """A field definition as the user writes it. `id` comes from the path."""
    label: str
    type: str = "text"
    facet: bool = True
    multiline: bool = False
    placeholder: str = ""


_FIELD_ID = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
# boolean is deferred with its tri-state filter (design/metadata-model.md §8)
_FIELD_TYPES = ("text", "number", "list", "date")


def _save_fields(lib: Library, defs: list[CustomFieldDef]) -> None:
    lib.vault.save_custom_fields(defs)
    fields.set_custom(defs)


@router.put("/fields/{field_id}", response_model=list[FieldOut])
def put_field(field_id: str, body: FieldIn, request: Request) -> list[FieldOut]:
    """Define a field, or change one. Renaming the LABEL is free; the id is the
    key every stored value hangs on, so it is fixed at creation."""
    if not _FIELD_ID.match(field_id):
        raise HTTPException(400, "a field id is lowercase letters, digits and _")
    if field_id in {f.id for f in fields.BUILTIN}:
        raise HTTPException(409, f"{field_id} is a built-in field")
    if body.type not in _FIELD_TYPES:
        raise HTTPException(400, f"type must be one of {', '.join(_FIELD_TYPES)}")
    if not body.label.strip():
        raise HTTPException(400, "a field needs a label")
    lib = _lib(request)
    defs = [d for d in lib.vault.custom_fields() if d.id != field_id]
    defs.append(CustomFieldDef(id=field_id, label=body.label.strip(), type=body.type,
                               facet=body.facet, multiline=body.multiline,
                               placeholder=body.placeholder))
    _save_fields(lib, defs)
    return list_fields()


@router.delete("/fields/{field_id}", response_model=list[FieldOut])
def delete_field(field_id: str, request: Request) -> list[FieldOut]:
    """Stop offering a field. The VALUES stay in the vault — deleting a field is
    not a licence to shred what the user typed, and re-adding it brings them back."""
    lib = _lib(request)
    defs = lib.vault.custom_fields()
    if not any(d.id == field_id for d in defs):
        raise HTTPException(404, "no such custom field")
    _save_fields(lib, [d for d in defs if d.id != field_id])
    return list_fields()


@router.get("/library", response_model=list[TitleOut])
def list_library(
    request: Request,
    search: str | None = None,
    progress: str | None = None,
    fav: bool = False,
    min_rating: int | None = None,
    sort: str = "updated",
    f: list[str] = Query(default=[], description="include, as field:value"),
    nf: list[str] = Query(default=[], description="exclude, as field:value"),
) -> list[TitleOut]:
    sel = _selection(search, progress, fav, min_rating, f, nf)
    return _lib(request).query(sort=sort, **sel)


@router.get("/library/count")
def library_count(request: Request) -> dict[str, int]:
    """How many titles the library holds, unfiltered — the header's denominator.
    Counting rows beats listing them just to read a length."""
    return {"total": _lib(request).count()}


@router.get("/library/facets", response_model=FacetCounts)
def library_facets(
    request: Request,
    search: str | None = None,
    progress: str | None = None,
    fav: bool = False,
    min_rating: int | None = None,
    f: list[str] = Query(default=[], description="include, as field:value"),
    nf: list[str] = Query(default=[], description="exclude, as field:value"),
) -> FacetCounts:
    """Linked facet counts for the CURRENT selection (no params = the full vocab)."""
    sel = _selection(search, progress, fav, min_rating, f, nf)
    return _lib(request).facets(sel)


@router.get("/titles/{title_id}", response_model=TitleOut)
def get_title(request: Request, title_id: str) -> TitleOut:
    title = _lib(request).get(title_id)
    if title is None:
        raise HTTPException(status_code=404, detail="title not found")
    return title


@router.post("/titles", response_model=TitleOut, status_code=201)
def create_title(request: Request, draft: DraftIn) -> TitleOut:
    """Commit a NEW draft into the vault."""
    if not draft.meta.title.strip():
        raise HTTPException(status_code=422, detail="a title is required")
    return _lib(request).create(draft)


@router.put("/titles/{title_id}", response_model=TitleOut)
def commit_title(request: Request, title_id: str, draft: DraftIn) -> TitleOut:
    """Commit a draft into an existing title (meta layers only; the user layer
    is untouchable from this path by construction)."""
    updated = _lib(request).commit(title_id, draft)
    if updated is None:
        raise HTTPException(status_code=404, detail="title not found")
    return updated


@router.patch("/titles/{title_id}/user", response_model=TitleOut)
def patch_user(request: Request, title_id: str, patch: UserPatch) -> TitleOut:
    """Instant write-through: favorite, rating, per-chapter read state."""
    title = _lib(request).patch_user(title_id, patch)
    if title is None:
        raise HTTPException(status_code=404, detail="title not found")
    return title


@router.delete("/titles/{title_id}", status_code=204)
def delete_title(request: Request, title_id: str) -> Response:
    if not _lib(request).delete(title_id):
        raise HTTPException(status_code=404, detail="title not found")
    return Response(status_code=204)


# ---- covers ----

class CoverIn(BaseModel):
    """Cover bytes captured in the page's context (the mandatory path — it sees
    exactly what the user sees, with the page's cookies), or a URL for the
    server-side fallback fetch when no page context is available."""
    data: str = ""         # base64 image bytes (page-context capture)
    contentType: str = ""  # e.g. "image/jpeg" (with `data`)
    sourceUrl: str = ""    # where the bytes came from (provenance)
    url: str = ""          # fallback: fetch this URL server-side
    referer: str = ""      # fallback: send this page's origin as Referer


@router.get("/titles/{title_id}/cover")
def get_cover(request: Request, title_id: str, w: int = 0):
    """The cover; `w` > 0 serves a cached high-quality downscale (the URL is
    versioned by mtime, so long client caching is safe)."""
    width = min(max(w, 0), 1024) or None
    if width:
        got = _lib(request).cover_thumb(title_id, width)
        if got is None:
            raise HTTPException(status_code=404, detail="no cover")
        data, ct = got
        return Response(content=data, media_type=ct, headers={"Cache-Control": "max-age=86400"})
    path = _lib(request).vault.cover_path(title_id)
    if path is None:
        raise HTTPException(status_code=404, detail="no cover")
    ext = path.suffix.lstrip(".").lower()
    return FileResponse(path, media_type=_CT_BY_EXT.get(ext, "application/octet-stream"))


@router.post("/titles/{title_id}/cover", response_model=TitleOut)
def set_cover(request: Request, title_id: str, body: CoverIn) -> TitleOut:
    if body.data:
        ext = _EXT_BY_CT.get(body.contentType.split(";")[0].strip().lower())
        if ext is None:
            raise HTTPException(status_code=422, detail="unsupported cover content type")
        try:
            data = base64.b64decode(body.data, validate=True)
        except (binascii.Error, ValueError):
            raise HTTPException(status_code=422, detail="invalid base64 cover data")
        if not data or len(data) > _MAX_COVER_BYTES:
            raise HTTPException(status_code=422, detail="cover is empty or too large")
        source = body.sourceUrl
    elif body.url:
        got = fetch_cover(body.url, referer=body.referer)
        if got is None:
            raise HTTPException(status_code=502, detail="could not fetch the cover")
        data, ext = got
        source = body.url
    else:
        raise HTTPException(status_code=422, detail="either data or url is required")
    title = _lib(request).set_cover(title_id, data, ext, source)
    if title is None:
        raise HTTPException(status_code=404, detail="title not found")
    return title


@router.delete("/titles/{title_id}/cover", response_model=TitleOut)
def delete_cover(request: Request, title_id: str) -> TitleOut:
    title = _lib(request).delete_cover(title_id)
    if title is None:
        raise HTTPException(status_code=404, detail="title not found")
    return title


# ---- chapter media: pages inside the downloaded archive ----

@router.post("/titles/{title_id}/chapters/import", response_model=TitleOut)
async def import_chapter_archive(
    request: Request, title_id: str, num: str = "", lang: str = "", group: str = "",
    filename: str = "", url: str = "", chapter_id: str = "",
) -> TitleOut:
    """Attach an ALREADY-downloaded archive from disk: the raw file bytes come
    in the body, the chapter identity in the query — either an exact
    `chapter_id` (attach/replace on an existing row) or num/lang/group (matched,
    created when missing). `url` records the chapter's source link on the row."""
    if not num.strip() and not chapter_id:
        raise HTTPException(status_code=422, detail="a chapter number (or chapter_id) is required")
    tmp = tempfile.NamedTemporaryFile(prefix="longbox-import-", suffix=Path(filename).suffix or ".zip", delete=False)
    try:
        # streamed to disk, never buffered: a 2 GB archive must not become 2 GB
        # of process memory just to be written out again
        size = 0
        async for chunk in request.stream():
            size += len(chunk)
            tmp.write(chunk)
        tmp.close()
        if not size:
            raise HTTPException(status_code=422, detail="an archive file is required")
        src = Path(tmp.name)
        # pages/size are stamped by the vault at ingest, from the stored zip
        sidecar = {
            "fileUrl": "", "pageUrl": "", "filename": filename or src.name,
            "importedFrom": "local",
            "downloadedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        try:
            # repacking an archive is seconds of CPU and disk under a title lock:
            # on the event loop it would stall every other request in the app
            result = await run_in_threadpool(
                _lib(request).attach_chapter_media,
                title_id, num=num, lang=lang, group=group, src=src, sidecar=sidecar,
                url=url, chapter_id=chapter_id)
        except media.UnsupportedArchiveError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        if result is None:
            raise HTTPException(status_code=404, detail="title or chapter not found")
        return result
    finally:
        Path(tmp.name).unlink(missing_ok=True)  # no-op when ingest moved it

# ---- page capture: sources that serve pages, not archives ----

class KnownPagesIn(BaseModel):
    keys: list[str] = Field(default_factory=list, max_length=2000)


class CapturedImage(BaseModel):
    key: str                # the image's own name — the dedup key
    url: str = ""           # where it came from (provenance only)
    data: str               # base64 bytes, fetched in the page's own context
    contentType: str = ""


class CapturePagesIn(BaseModel):
    pageUrl: str = ""       # the reader page these images were taken from
    images: list[CapturedImage] = []


@router.post("/titles/{title_id}/chapters/{chapter_id}/pages/known", response_model=list[str])
def known_chapter_pages(request: Request, title_id: str, chapter_id: str, body: KnownPagesIn) -> list[str]:
    """Of `keys`, which this chapter ALREADY holds — the client fetches only the
    rest, so flipping back through a chapter downloads nothing twice."""
    stored = set(_lib(request).stored_page_keys(title_id, chapter_id))
    return [k for k in body.keys if k in stored]


@router.post("/titles/{title_id}/chapters/{chapter_id}/pages/capture", response_model=TitleOut)
def capture_chapter_pages(request: Request, title_id: str, chapter_id: str, body: CapturePagesIn) -> TitleOut:
    """Append page images captured from a reader page into the ARMED chapter
    row. Images whose key is already stored are skipped."""
    if len(body.images) > _MAX_PAGES_PER_CAPTURE:
        raise HTTPException(status_code=413, detail="too many images in one capture")
    images: list[tuple[bytes, str, str]] = []
    for img in body.images:
        try:
            data = base64.b64decode(img.data, validate=True)
        except (binascii.Error, ValueError):
            continue
        if not data or len(data) > _MAX_PAGE_BYTES or not img.key:
            continue
        # the BYTES decide the format: a CDN's content-type (and the name it
        # serves an image under) is routinely wrong, and a mislabelled page
        # would be unreadable in any external viewer
        ext = media.sniff_ext(data) \
            or _EXT_BY_CT.get(img.contentType.split(";")[0].strip().lower()) \
            or (Path(urlsplit(img.url or img.key).path).suffix.lstrip(".").lower())
        if f".{ext}" not in media.IMAGE_EXTS:
            continue  # not an image at all — never store it as a page
        images.append((data, f".{ext}", img.key))
    try:
        result = _lib(request).capture_chapter_pages(
            title_id, chapter_id, page_url=body.pageUrl, images=images)
    except media.UnsupportedArchiveError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    if result is None:
        raise HTTPException(status_code=404, detail="title or chapter not found")
    return result[0]


class PagesDeleteIn(BaseModel):
    indices: list[int]


class PagesMoveIn(BaseModel):
    to: str            # target chapter id (must exist; archive created on demand)
    indices: list[int]


@router.post("/titles/{title_id}/chapters/{chapter_id}/pages/add", response_model=TitleOut)
async def add_chapter_pages(
    request: Request, title_id: str, chapter_id: str, files: list[UploadFile] = File(...),
) -> TitleOut:
    """Append loose images (multi-select or a whole folder) to an entry's
    archive — created on first add. Non-image files are skipped silently, so a
    folder upload with stray junk still works."""
    payload: list[tuple[bytes, str]] = []
    for f in files:
        ext = Path(f.filename or "").suffix.lower()
        if ext not in media.IMAGE_EXTS:
            continue
        data = await f.read()
        if data:
            payload.append((data, ext))
    if not payload:
        raise HTTPException(status_code=422, detail="no image files in the upload")
    try:
        # a folder import decodes and rewrites the whole archive — off the loop
        result = await run_in_threadpool(
            _lib(request).add_chapter_pages, title_id, chapter_id, payload)
    except media.UnsupportedArchiveError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    if result is None:
        raise HTTPException(status_code=404, detail="title or chapter not found")
    return result


@router.post("/titles/{title_id}/chapters/{chapter_id}/pages/move", response_model=TitleOut)
def move_chapter_pages(request: Request, title_id: str, chapter_id: str, body: PagesMoveIn) -> TitleOut:
    """Move the selected pages into another entry of the SAME title."""
    try:
        result = _lib(request).move_chapter_pages(title_id, chapter_id, body.to, body.indices)
    except media.UnsupportedArchiveError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    if result is None:
        raise HTTPException(status_code=404, detail="title, source or target chapter not found")
    return result


class PagesOrderIn(BaseModel):
    order: list[int]  # a full permutation of the current page indices


@router.post("/titles/{title_id}/chapters/{chapter_id}/pages/reorder", response_model=TitleOut)
def reorder_chapter_pages(request: Request, title_id: str, chapter_id: str, body: PagesOrderIn) -> TitleOut:
    """Manually rearrange the pages of one entry (the archive is rewritten)."""
    try:
        result = _lib(request).reorder_chapter_pages(title_id, chapter_id, body.order)
    except media.UnsupportedArchiveError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if result is None:
        raise HTTPException(status_code=404, detail="no downloaded media for that chapter")
    return result


@router.get("/titles/{title_id}/chapters/{chapter_id}/pages")
def chapter_pages(request: Request, title_id: str, chapter_id: str) -> dict:
    pages = _lib(request).chapter_pages(title_id, chapter_id)
    if pages is None:
        raise HTTPException(status_code=404, detail="no downloaded media for that chapter")
    return {"count": len(pages)}


# A player does not ask for a range it intends to read whole. Chromium asks for
# `bytes=N-` — this byte to the end of the file — reads a hundred kilobytes, drops
# the connection, and asks again a hundred kilobytes further on. Taking that ask
# literally means committing to hundreds of MB the player will abandon: a new TCP
# connection and a fresh open() on the vault disk per fragment, with every byte
# already read thrown away. A window it will read to the end costs the same bytes
# and none of the churn. Measured on a 4K episode over a network vault: 0.29x
# playback with 83 buffer fragments and 69 dropped frames, against 0.97x with 8
# fragments and none.
VIDEO_WINDOW = 8 * 1024 * 1024


def _open_ended_start(range_header: str, size: int) -> int | None:
    """The first byte of a single `bytes=N-` ask, or None for anything else: an
    explicit `bytes=N-M` and a suffix `bytes=-N` are answered exactly as asked."""
    if not range_header.startswith("bytes="):
        return None
    spec = range_header[6:].strip()
    if "," in spec:
        return None
    first, sep, last = spec.partition("-")
    if not sep or last or not first.isdigit():
        return None
    start = int(first)
    return start if start < size else None


def _window(path: Path, start: int, length: int) -> Iterator[bytes]:
    with path.open("rb") as f:
        f.seek(start)
        left = length
        while left > 0:
            chunk = f.read(min(256 * 1024, left))
            if not chunk:
                return
            left -= len(chunk)
            yield chunk


@router.get("/titles/{title_id}/chapters/{chapter_id}/video")
def chapter_video(request: Request, title_id: str, chapter_id: str) -> Response:
    """The episode file itself — never copied or re-containered on the way out.
    An open-ended ask gets a window; anything else FileResponse answers, so an
    explicit range still comes back as a 206 and seeking works untouched."""
    path = _lib(request).chapter_video_path(title_id, chapter_id)
    if path is None:
        raise HTTPException(status_code=404, detail="no video for this chapter")
    content_type = media.VIDEO_CT_BY_EXT.get(path.suffix.lower(), "application/octet-stream")
    start = _open_ended_start(request.headers.get("range", ""), size := path.stat().st_size)
    if start is not None:
        end = min(start + VIDEO_WINDOW, size) - 1
        return StreamingResponse(
            _window(path, start, end - start + 1),
            status_code=206,
            media_type=content_type,
            headers={
                "Content-Range": f"bytes {start}-{end}/{size}",
                "Content-Length": str(end - start + 1),
                "Cache-Control": "no-store",
                "Accept-Ranges": "bytes",
            })
    return FileResponse(
        path,
        media_type=content_type,
        # An episode is streamed by range from a disk the app already owns, so
        # persisting it buys nothing and costs everything: one 600 MB file does
        # not fit a store that holds a few hundred MB in total, so it evicts
        # every cover and page preview and then churns for the rest of playback
        # — on the same disk the stream is reading through. This drops only the
        # PERSISTENCE; the player's in-memory buffer, and seeking inside it, are
        # untouched.
        headers={"Cache-Control": "no-store", "Accept-Ranges": "bytes"})


class PlaybackIn(BaseModel):
    duration: float = 0.0


@router.post("/titles/{title_id}/chapters/{chapter_id}/video/meta", response_model=TitleOut)
def chapter_video_meta(request: Request, title_id: str, chapter_id: str,
                       body: PlaybackIn) -> TitleOut:
    """What only a player can tell us: how long the episode actually is. Read
    once, on first play, and kept in the sidecar so the list can show it without
    opening the file (and without an ffprobe the app does not ship yet)."""
    title = _lib(request).set_video_duration(title_id, chapter_id, body.duration)
    if title is None:
        raise HTTPException(status_code=404, detail="chapter not found")
    return title


@router.get("/titles/{title_id}/chapters/{chapter_id}/frames/{kind}")
def chapter_frames(request: Request, title_id: str, chapter_id: str, kind: str,
                   w: int = 0) -> Response:
    """A stored still for an episode — `poster` or `sheet`.

    404 until the window has cut one: an episode with no stills yet is a normal
    state, not an error."""
    got = _lib(request).chapter_frames(title_id, chapter_id, kind,
                                       min(max(w, 0), 1600) or None)
    if got is None:
        raise HTTPException(status_code=404, detail="no frames")
    data, content_type = got
    return Response(content=data, media_type=content_type,
                    headers={"Cache-Control": "max-age=86400"})


@router.put("/titles/{title_id}/chapters/{chapter_id}/frames/{kind}", response_model=TitleOut)
async def put_chapter_frames(request: Request, title_id: str, chapter_id: str,
                             kind: str, grid: str = "") -> TitleOut:
    """Stills the WINDOW decoded, stored in the vault so they are decoded once."""
    data = await request.body()
    if len(data) > 8 * 1024 * 1024:
        raise HTTPException(413, "frames too large")
    lib = _lib(request)
    if not await run_in_threadpool(lib.save_chapter_frames, title_id, chapter_id, kind,
                                   data, grid[:8]):
        raise HTTPException(400, "not a JPEG, no such chapter, or unknown kind")
    out = lib.get(title_id)
    if out is None:
        raise HTTPException(404, "no such title")
    return out


@router.get("/titles/{title_id}/chapters/{chapter_id}/pages/{index}")
def chapter_page(request: Request, title_id: str, chapter_id: str, index: int,
                 w: int = 0, cap: float = 0) -> Response:
    """A page image; `w` > 0 serves a cached downscaled JPEG preview, and
    `cap` > 0 additionally crops very tall pages to width×cap from the top."""
    width = min(max(w, 0), 1024) or None
    ratio = min(max(cap, 0.0), 4.0) or None
    got = _lib(request).chapter_page(title_id, chapter_id, index, width, ratio)
    if got is None:
        raise HTTPException(status_code=404, detail="page not found")
    data, content_type = got
    return Response(content=data, media_type=content_type,
                    headers={"Cache-Control": "max-age=86400"})


@router.post("/titles/{title_id}/chapters/{chapter_id}/pages/delete", response_model=TitleOut)
def delete_chapter_pages(request: Request, title_id: str, chapter_id: str, body: PagesDeleteIn) -> TitleOut:
    """Rewrite the archive without the given page indices (atomic)."""
    result = _lib(request).delete_chapter_pages(title_id, chapter_id, body.indices)
    if result is None:
        raise HTTPException(status_code=404, detail="no downloaded media for that chapter")
    return result


def _held(name: str) -> HTTPException:
    """Windows refuses to unlink an open file, and the holder is almost always
    the player on the very entry being deleted — so the answer says which file
    and what to do about it, instead of a 500 out of `os.unlink`."""
    return HTTPException(409, f"“{name}” is open right now — close the player or the "
                              f"reader on this entry, then delete it")


@router.delete("/titles/{title_id}/chapters/{chapter_id}/media", response_model=TitleOut)
def delete_chapter_media(request: Request, title_id: str, chapter_id: str) -> TitleOut:
    """Remove the downloaded archive; the chapter row stays."""
    try:
        result = _lib(request).delete_chapter_media(title_id, chapter_id)
    except MediaInUseError as e:
        raise _held(str(e))
    if result is None:
        raise HTTPException(status_code=404, detail="title not found")
    return result


@router.delete("/titles/{title_id}/chapters/{chapter_id}", response_model=TitleOut)
def delete_chapter_row(request: Request, title_id: str, chapter_id: str) -> TitleOut:
    """Remove the chapter row AND its downloaded media."""
    try:
        result = _lib(request).delete_chapter_row(title_id, chapter_id)
    except MediaInUseError as e:
        raise _held(str(e))
    if result is None:
        raise HTTPException(status_code=404, detail="chapter not found")
    return result


# ---- derived collections ----

@router.post("/authors/{author_id}/favorite", response_model=list[BrowseGroup])
def set_author_favorite(request: Request, author_id: str, value: bool = True) -> list[Author]:
    """Write-through: mark an author as favorite (persisted in the vault)."""
    if not _lib(request).set_author_favorite(author_id, value):
        raise HTTPException(status_code=404, detail="author not found")
    return _lib(request).browse("authors")


@router.delete("/sources/{domain}")
def delete_source(request: Request, domain: str) -> dict:
    """Remove a source from the Sources list and forget its learned recipe.
    Titles KEEP their source links — those are removed per title, explicitly.
    Capturing from the domain again (saving a recipe) brings it back."""
    with config_transaction() as cfg:
        hidden = set(cfg.get("hidden_sources", []))
        hidden.add(domain)
        cfg["hidden_sources"] = sorted(hidden)
    recipe_deleted = request.app.state.recipes.delete(domain)
    return {"hidden": True, "recipeDeleted": recipe_deleted}


def _source_prefs(cfg: dict) -> dict:
    """The user's own layer over the sources: group and saved links, per domain.

    It lives in the app config, not in a recipe: a recipe is what longbox LEARNED
    about a site, while a bookmark is where the user likes to start on it."""
    prefs = cfg.get("sources")
    return prefs if isinstance(prefs, dict) else {}


@router.get("/browse/{field_id}", response_model=list[BrowseGroup])
def browse_by(
    field_id: str,
    request: Request,
    search: str | None = None,
    progress: str | None = None,
    fav: bool = False,
    min_rating: int | None = None,
    f: list[str] = Query(default=[]),
    nf: list[str] = Query(default=[]),
) -> list[BrowseGroup]:
    if field_id not in fields.by_id():
        raise HTTPException(404, "no such field")
    sel = _selection(search, progress, fav, min_rating, f, nf)
    narrowed = any((search, progress, fav, min_rating, f, nf))
    return _lib(request).browse(field_id, sel if narrowed else None)


@router.get("/sources", response_model=list[Source])
def list_sources(request: Request) -> list[Source]:
    recipes = request.app.state.recipes
    cfg = load_config()
    hidden = set(cfg.get("hidden_sources", []))
    prefs = _source_prefs(cfg)
    sources = [s for s in _lib(request).sources() if s.domain not in hidden]
    for s in sources:  # join the saved per-domain recipe onto each source
        recipe = recipes.get(s.domain)
        if recipe is not None:
            s.hasRecipe = True
            s.recipeVer = recipe.version
            s.fields = list(recipe.fields.keys())
        mine = prefs.get(s.domain) or {}
        s.group = str(mine.get("group") or "")
        s.bookmarks = [Bookmark.model_validate(b) for b in (mine.get("bookmarks") or [])
                       if isinstance(b, dict) and b.get("url")]
    return sources


class SourcePrefsIn(BaseModel):
    """Partial: send what changes. Bookmarks arrive as the WHOLE list — an index
    into a list somebody else may have reordered is not a stable address."""
    group: str | None = None
    bookmarks: list[Bookmark] | None = None


@router.put("/sources/{domain}", response_model=list[Source])
def put_source_prefs(domain: str, body: SourcePrefsIn, request: Request) -> list[Source]:
    with config_transaction() as cfg:
        prefs = dict(_source_prefs(cfg))
        mine = dict(prefs.get(domain) or {})
        if body.group is not None:
            mine["group"] = body.group.strip()
        if body.bookmarks is not None:
            mine["bookmarks"] = [b.model_dump() for b in body.bookmarks if b.url.strip()]
        # an entry that says nothing is not worth keeping in the config
        if mine.get("group") or mine.get("bookmarks"):
            prefs[domain] = mine
        else:
            prefs.pop(domain, None)
        cfg["sources"] = prefs
    return list_sources(request)


@router.get("/source-groups", response_model=list[str])
def list_source_groups() -> list[str]:
    groups = load_config().get("source_groups")
    return [g for g in groups if isinstance(g, str) and g.strip()] if isinstance(groups, list) else []


class SourceGroupsIn(BaseModel):
    groups: list[str]


@router.put("/source-groups", response_model=list[str])
def put_source_groups(body: SourceGroupsIn) -> list[str]:
    """The ordered group list. Renaming one here does NOT re-tag its sources —
    the caller sends the moves it wants, so a rename can never orphan a domain
    behind the app's back."""
    seen: list[str] = []
    for g in body.groups:
        name = g.strip()
        if name and name not in seen:
            seen.append(name)
    with config_transaction() as cfg:
        cfg["source_groups"] = seen
    return seen
