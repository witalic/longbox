"""Library endpoints: queries, draft commits (meta layers), the write-through
user layer, and cover bytes. See design/state-model.md §12 for the surface.
"""
from __future__ import annotations

import base64
import binascii
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..config_store import config_transaction, load_config
from ..library import media
from ..library.models import Author, DraftIn, Facets, Source, TitleOut, UserPatch
from ..library.service import Library
from ..scraper.covers import fetch_cover

router = APIRouter(prefix="/api")

_CT_BY_EXT = {
    "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
    "webp": "image/webp", "gif": "image/gif", "avif": "image/avif",
}
_EXT_BY_CT = {
    "image/jpeg": "jpg", "image/jpg": "jpg", "image/png": "png",
    "image/webp": "webp", "image/gif": "gif", "image/avif": "avif",
}
_MAX_COVER_BYTES = 8 * 1024 * 1024


def _lib(request: Request) -> Library:
    return request.app.state.library


def _selection(
    search: str | None,
    progress: str | None,
    fav: bool,
    min_rating: int | None,
    types: list[str], types_not: list[str],
    statuses: list[str], statuses_not: list[str],
    genres: list[str], genres_not: list[str],
    tags: list[str], tags_not: list[str],
    languages: list[str], languages_not: list[str],
    flags: list[str], flags_not: list[str],
    authors: list[str], authors_not: list[str],
    characters: list[str], characters_not: list[str],
) -> dict:
    return {
        "search": search, "progress": progress, "fav": fav or None, "min_rating": min_rating,
        "types": tuple(types), "types_not": tuple(types_not),
        "statuses": tuple(statuses), "statuses_not": tuple(statuses_not),
        "genres": tuple(genres), "genres_not": tuple(genres_not),
        "tags": tuple(tags), "tags_not": tuple(tags_not),
        "languages": tuple(languages), "languages_not": tuple(languages_not),
        "flags": tuple(flags), "flags_not": tuple(flags_not),
        "authors": tuple(authors), "authors_not": tuple(authors_not),
        "characters": tuple(characters), "characters_not": tuple(characters_not),
    }


@router.get("/library", response_model=list[TitleOut])
def list_library(
    request: Request,
    search: str | None = None,
    progress: str | None = None,
    fav: bool = False,
    min_rating: int | None = None,
    sort: str = "updated",
    types: list[str] = Query(default=[]),
    types_not: list[str] = Query(default=[]),
    statuses: list[str] = Query(default=[]),
    statuses_not: list[str] = Query(default=[]),
    genres: list[str] = Query(default=[]),
    genres_not: list[str] = Query(default=[]),
    tags: list[str] = Query(default=[]),
    tags_not: list[str] = Query(default=[]),
    languages: list[str] = Query(default=[]),
    languages_not: list[str] = Query(default=[]),
    flags: list[str] = Query(default=[]),
    flags_not: list[str] = Query(default=[]),
    authors: list[str] = Query(default=[]),
    authors_not: list[str] = Query(default=[]),
    characters: list[str] = Query(default=[]),
    characters_not: list[str] = Query(default=[]),
) -> list[TitleOut]:
    sel = _selection(search, progress, fav, min_rating, types, types_not,
                     statuses, statuses_not, genres, genres_not, tags, tags_not,
                     languages, languages_not, flags, flags_not, authors, authors_not,
                     characters, characters_not)
    return _lib(request).query(sort=sort, **sel)


@router.get("/library/facets", response_model=Facets)
def library_facets(
    request: Request,
    search: str | None = None,
    progress: str | None = None,
    fav: bool = False,
    min_rating: int | None = None,
    types: list[str] = Query(default=[]),
    types_not: list[str] = Query(default=[]),
    statuses: list[str] = Query(default=[]),
    statuses_not: list[str] = Query(default=[]),
    genres: list[str] = Query(default=[]),
    genres_not: list[str] = Query(default=[]),
    tags: list[str] = Query(default=[]),
    tags_not: list[str] = Query(default=[]),
    languages: list[str] = Query(default=[]),
    languages_not: list[str] = Query(default=[]),
    flags: list[str] = Query(default=[]),
    flags_not: list[str] = Query(default=[]),
    authors: list[str] = Query(default=[]),
    authors_not: list[str] = Query(default=[]),
    characters: list[str] = Query(default=[]),
    characters_not: list[str] = Query(default=[]),
) -> Facets:
    """Linked facet counts for the CURRENT selection (no params = the full vocab)."""
    sel = _selection(search, progress, fav, min_rating, types, types_not,
                     statuses, statuses_not, genres, genres_not, tags, tags_not,
                     languages, languages_not, flags, flags_not, authors, authors_not,
                     characters, characters_not)
    return Facets(**_lib(request).facets(sel))


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
    data = await request.body()
    if not data:
        raise HTTPException(status_code=422, detail="an archive file is required")
    tmp = tempfile.NamedTemporaryFile(prefix="longbox-import-", suffix=Path(filename).suffix or ".zip", delete=False)
    try:
        tmp.write(data)
        tmp.close()
        src = Path(tmp.name)
        # pages/size are stamped by the vault at ingest, from the stored zip
        sidecar = {
            "fileUrl": "", "pageUrl": "", "filename": filename or src.name,
            "importedFrom": "local",
            "downloadedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        try:
            result = _lib(request).attach_chapter_media(
                title_id, num=num, lang=lang, group=group, src=src, sidecar=sidecar,
                url=url, chapter_id=chapter_id)
        except media.UnsupportedArchiveError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        if result is None:
            raise HTTPException(status_code=404, detail="title or chapter not found")
        return result
    finally:
        Path(tmp.name).unlink(missing_ok=True)  # no-op when ingest moved it

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
        result = _lib(request).add_chapter_pages(title_id, chapter_id, payload)
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


@router.delete("/titles/{title_id}/chapters/{chapter_id}/media", response_model=TitleOut)
def delete_chapter_media(request: Request, title_id: str, chapter_id: str) -> TitleOut:
    """Remove the downloaded archive; the chapter row stays."""
    result = _lib(request).delete_chapter_media(title_id, chapter_id)
    if result is None:
        raise HTTPException(status_code=404, detail="title not found")
    return result


@router.delete("/titles/{title_id}/chapters/{chapter_id}", response_model=TitleOut)
def delete_chapter_row(request: Request, title_id: str, chapter_id: str) -> TitleOut:
    """Remove the chapter row AND its downloaded media."""
    result = _lib(request).delete_chapter_row(title_id, chapter_id)
    if result is None:
        raise HTTPException(status_code=404, detail="chapter not found")
    return result


# ---- derived collections ----

@router.get("/authors", response_model=list[Author])
def list_authors(request: Request) -> list[Author]:
    return _lib(request).authors()


@router.post("/authors/{author_id}/favorite", response_model=list[Author])
def set_author_favorite(request: Request, author_id: str, value: bool = True) -> list[Author]:
    """Write-through: mark an author as favorite (persisted in the vault)."""
    if not _lib(request).set_author_favorite(author_id, value):
        raise HTTPException(status_code=404, detail="author not found")
    return _lib(request).authors()


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


@router.get("/sources", response_model=list[Source])
def list_sources(request: Request) -> list[Source]:
    recipes = request.app.state.recipes
    hidden = set(load_config().get("hidden_sources", []))
    sources = [s for s in _lib(request).sources() if s.domain not in hidden]
    for s in sources:  # join the saved per-domain recipe onto each source
        recipe = recipes.get(s.domain)
        if recipe is not None:
            s.hasRecipe = True
            s.recipeVer = recipe.version
            s.fields = list(recipe.fields.keys())
    return sources
