"""Value pages (design/value-pages.md): a page for a person, a tag, a character
— what the library knows about one value, and what the owner wrote about it.

A value is passed as a query parameter, never in the path: values hold slashes
(`AC/DC`) and anything else a person can type.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from ..library import media
from ..library import pages as P
from ..library.rules import InvalidRule
from ..library.service import Library

router = APIRouter(prefix="/api/pages")

_EXT_BY_CT: dict[str, str] = {}
for _ext, _ct in media.CT_BY_EXT.items():
    _EXT_BY_CT.setdefault(_ct, _ext.lstrip("."))
_MAX_IMAGE_BYTES = 8 * 1024 * 1024


def _lib(request: Request) -> Library:
    return request.app.state.library


def _vocab(field: str) -> str:
    vocab = P.vocab_of(field)
    if vocab is None:
        raise HTTPException(status_code=404, detail=f"{field} values have no pages")
    return vocab


@router.get("/{field}/page", response_model=P.PageOut)
def page(field: str, value: str, request: Request) -> P.PageOut:
    _vocab(field)
    out = P.view(_lib(request), field, value)
    if out is None:
        raise HTTPException(status_code=404, detail="no such value")
    return out


@router.get("/{field}/peek", response_model=P.PeekOut)
def peek(field: str, value: str, request: Request) -> P.PeekOut:
    _vocab(field)
    out = P.peek(_lib(request), field, value)
    if out is None:
        raise HTTPException(status_code=404, detail="no such value")
    return out


@router.put("/{field}/page", response_model=P.PageOut)
def save(field: str, value: str, body: P.PageIn, request: Request) -> P.PageOut:
    _vocab(field)
    lib = _lib(request)
    try:
        P.save(lib, field, value, body)
    except (ValueError, InvalidRule) as e:
        raise HTTPException(status_code=422, detail=str(e))
    return P.view(lib, field, value)


@router.delete("/{field}/page")
def delete(field: str, value: str, request: Request) -> dict:
    _vocab(field)
    try:
        gone = P.delete(_lib(request), field, value)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    if not gone:
        raise HTTPException(status_code=404, detail="no page was written for this value")
    return {"deleted": True}


@router.get("/{field}", response_model=list[P.Stored])
def stored(field: str, request: Request) -> list[P.Stored]:
    """Every page written in the field's vocabulary, with whether any title
    still holds its value — the listings' marks and "pages without titles"."""
    _vocab(field)
    return P.stored(_lib(request), field)


# ---- the image ----

@router.get("/{field}/image")
def image(field: str, value: str, request: Request, v: str = "", w: int = 0) -> Response:
    """The page's image. `v` is its file name — unique per image, so the URL
    can be cached for good; `w` serves a cached downscale."""
    vocab = _vocab(field)
    name = v
    if not name:
        page = _lib(request).pages.get(vocab, value)
        name = page.image if page else ""
    got = _lib(request).page_image(vocab, name, min(max(w, 0), 1024))
    if got is None:
        raise HTTPException(status_code=404, detail="no image")
    data, ct = got
    return Response(content=data, media_type=ct, headers={"Cache-Control": "max-age=31536000, immutable"})


@router.put("/{field}/image", response_model=P.PageOut)
async def put_image(field: str, value: str, request: Request) -> P.PageOut:
    """A dropped or pasted image: the raw bytes, typed by Content-Type."""
    _vocab(field)
    ext = _EXT_BY_CT.get((request.headers.get("content-type") or "").split(";")[0].strip().lower())
    if ext is None:
        raise HTTPException(status_code=422, detail="not an image a page can show")
    data = await request.body()
    if not data or len(data) > _MAX_IMAGE_BYTES:
        raise HTTPException(status_code=422, detail="the image is empty or too large")
    if not media.sniff_ext(data):
        raise HTTPException(status_code=422, detail="the file's own bytes are not an image")
    lib = _lib(request)
    try:
        await run_in_threadpool(P.set_image, lib, field, value, data, ext)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return await run_in_threadpool(P.view, lib, field, value)


class FromTitle(BaseModel):
    titleId: str
    slot: int = 0


@router.post("/{field}/image/from-title", response_model=P.PageOut)
def image_from_title(field: str, value: str, body: FromTitle, request: Request) -> P.PageOut:
    """One of its works' covers as the page's face — copied, so it outlives
    the title it came from."""
    _vocab(field)
    lib = _lib(request)
    path = lib.vault.cover_path(body.titleId, max(body.slot, 0))
    if path is None:
        raise HTTPException(status_code=404, detail="that title has no such cover")
    try:
        P.set_image(lib, field, value, path.read_bytes(), path.suffix)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return P.view(lib, field, value)


@router.delete("/{field}/image", response_model=P.PageOut)
def drop_image(field: str, value: str, request: Request) -> P.PageOut:
    _vocab(field)
    lib = _lib(request)
    P.drop_image(lib, field, value)
    return P.view(lib, field, value)
