"""The armed-download lifecycle (design/state-model.md §9).

The human arms ONE next download for a specific chapter; when the download
STARTS in the embedded browser the arm is consumed and the chapter binding is
claimed immediately — so the user can arm the next chapter and download in
parallel. The shell reports progress while the file streams, then hands the
finished temp file over for ingest. Unarmed downloads are rejected at start.
"""
from __future__ import annotations

import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from ..config_store import config_transaction, load_config
from ..library import media
from ..library.models import TitleOut
from ..library.service import Library

router = APIRouter(prefix="/api/downloads")

ARM_TTL_SECONDS = 15 * 60
KEEP_ITEMS = 20  # recent downloads shown in the panel

# The armed-download slot and the items map are app-global mutable state hit
# from the threadpool — consume-the-arm must be atomic or two started downloads
# both claim the same chapter.
_DL_LOCK = threading.Lock()


class ArmIn(BaseModel):
    titleId: str
    num: str
    lang: str = ""
    group: str = ""


class Resume(BaseModel):
    """What it takes to pick a transfer up where it stopped.

    The shell hands these over when a download is interrupted; Electron needs
    every one of them to re-open the same byte range on the same file. Without
    the validators the server would happily answer from byte 0 into a file that
    already holds half the episode."""
    path: str = ""            # the partial file, still on disk
    urlChain: list[str] = []  # the redirects it went through, in order
    offset: int = 0           # bytes already on disk
    total: int = 0
    eTag: str = ""
    lastModified: str = ""


class DownloadItem(BaseModel):
    id: str
    titleId: str
    num: str
    lang: str = ""
    group: str = ""
    filename: str = ""
    fileUrl: str = ""
    pageUrl: str = ""
    received: int = 0
    total: int = 0
    # downloading | done | failed | interrupted (stopped, and resumable)
    state: str = "downloading"
    error: str = ""
    resume: Resume | None = None


class DownloadsOut(BaseModel):
    armed: ArmIn | None = None
    items: list[DownloadItem] = []


def _lib(request: Request) -> Library:
    return request.app.state.library


# Unfinished transfers OUTLIVE the app: a download interrupted by a quit is
# remembered on disk, with everything needed to resume it, so the next launch
# can offer to carry on instead of silently having lost the bytes.
_INTERRUPTED = "interrupted_downloads"


def _load_interrupted() -> list[DownloadItem]:
    rows = load_config().get(_INTERRUPTED) or []
    out: list[DownloadItem] = []
    for row in rows if isinstance(rows, list) else []:
        try:
            out.append(DownloadItem.model_validate(row))
        except ValueError:
            continue  # a record from an older shape is not worth a crash
    return out


def _save_interrupted(items: list[DownloadItem]) -> None:
    with config_transaction() as cfg:
        cfg[_INTERRUPTED] = [i.model_dump() for i in items[:KEEP_ITEMS]]


def _items(request: Request) -> dict[str, DownloadItem]:
    with _DL_LOCK:
        if not hasattr(request.app.state, "downloads"):
            # what a previous run left unfinished is part of the list from the
            # first request, not something the UI has to ask for separately
            request.app.state.downloads = {i.id: i for i in _load_interrupted()}
        return request.app.state.downloads


def forget_downloads(app) -> None:
    """Drop the armed slot and the item list. A library switch MUST call this:
    an arm names a title id, and the same id can exist in the vault we just
    moved to — the file would land in the wrong library's chapter."""
    with _DL_LOCK:
        app.state.armed_download = None
        app.state.downloads = {}


def _armed(request: Request) -> ArmIn | None:
    """The live arm, expiring a stale one. EVERY reader and writer of the slot
    holds _DL_LOCK: the panel polls this once a second, so an expiry racing a
    fresh arm would clear the arm the user JUST made — and the next download
    would be refused as unarmed with nothing on screen to explain it."""
    with _DL_LOCK:
        state = getattr(request.app.state, "armed_download", None)
        if state is None:
            return None
        if time.monotonic() - state["at"] > ARM_TTL_SECONDS:
            # clear only what we just judged stale, never a newer arm
            if getattr(request.app.state, "armed_download", None) is state:
                request.app.state.armed_download = None
            return None
        return state["arm"]


@router.post("/arm", response_model=DownloadsOut)
def arm(request: Request, body: ArmIn) -> DownloadsOut:
    if _lib(request).get(body.titleId) is None:
        raise HTTPException(status_code=404, detail="title not found")
    if not body.num.strip():
        raise HTTPException(status_code=422, detail="a chapter number is required")
    with _DL_LOCK:
        request.app.state.armed_download = {"arm": body, "at": time.monotonic()}
    return state_out(request)


@router.get("", response_model=DownloadsOut)
def state_out(request: Request) -> DownloadsOut:
    items = list(_items(request).values())
    items.reverse()  # newest first
    return DownloadsOut(armed=_armed(request), items=items)


@router.delete("/arm", status_code=204)
def disarm(request: Request) -> Response:
    with _DL_LOCK:
        request.app.state.armed_download = None
    return Response(status_code=204)


class InterruptIn(BaseModel):
    reason: str = ""
    resume: Resume | None = None


@router.post("/{download_id}/interrupted", response_model=DownloadItem)
def interrupted(request: Request, download_id: str, body: InterruptIn) -> DownloadItem:
    """A transfer stopped with bytes on disk — quitting mid-download, a dropped
    connection. Remembered ACROSS RUNS: the record carries the chapter it was
    bound to and the offset it reached, so resuming is a continuation and not a
    fresh guess."""
    item = _item_or_404(request, download_id)
    item.state = "interrupted"
    item.error = body.reason or "stopped"
    item.resume = body.resume
    _save_interrupted([i for i in _items(request).values() if i.state == "interrupted"])
    return item


@router.post("/{download_id}/rearm", response_model=DownloadsOut)
def rearm(request: Request, download_id: str) -> DownloadsOut:
    """Arm the chapter an interrupted download was bound to, so the transfer the
    shell is about to restart claims the same entry it was claiming before."""
    item = _item_or_404(request, download_id)
    if _lib(request).get(item.titleId) is None:
        raise HTTPException(status_code=404, detail="that title is gone")
    with _DL_LOCK:
        request.app.state.armed_download = {
            "arm": ArmIn(titleId=item.titleId, num=item.num, lang=item.lang, group=item.group),
            "at": time.monotonic(),
        }
    return state_out(request)


@router.delete("/{download_id}", status_code=204)
def forget(request: Request, download_id: str) -> Response:
    """Drop a download from the list. Stopping one is the shell's job (it owns
    the transfer); this removes the record afterwards — and clears a failed one
    the human has read. Declared AFTER /arm: a wildcard swallows it otherwise."""
    dropped = _items(request).pop(download_id, None)
    if dropped is not None and dropped.state == "interrupted":
        _save_interrupted([i for i in _items(request).values() if i.state == "interrupted"])
    return Response(status_code=204)


class StartIn(BaseModel):
    filename: str = ""
    fileUrl: str = ""
    pageUrl: str = ""


@router.post("/start", response_model=DownloadItem)
def start(request: Request, body: StartIn) -> DownloadItem:
    """A download began in the browser: consume the arm and CLAIM the chapter
    binding right now — the next chapter can be armed in parallel."""
    with _DL_LOCK:  # observe + consume the arm as one step
        state = getattr(request.app.state, "armed_download", None)
        fresh = state is not None and time.monotonic() - state["at"] <= ARM_TTL_SECONDS
        if not fresh:
            raise HTTPException(status_code=409, detail="no download is armed")
        armed = state["arm"]
        request.app.state.armed_download = None
    item = DownloadItem(
        id=uuid.uuid4().hex[:12], titleId=armed.titleId, num=armed.num,
        lang=armed.lang, group=armed.group,
        filename=body.filename, fileUrl=body.fileUrl, pageUrl=body.pageUrl,
    )
    items = _items(request)
    items[item.id] = item
    while len(items) > KEEP_ITEMS:  # prune the oldest finished entries
        for key, it in list(items.items()):
            if it.state != "downloading":
                items.pop(key)
                break
        else:
            break
    return item


def _item_or_404(request: Request, download_id: str) -> DownloadItem:
    item = _items(request).get(download_id)
    if item is None:
        raise HTTPException(status_code=404, detail="unknown download")
    return item


class ProgressIn(BaseModel):
    received: int = 0
    total: int = 0


@router.post("/{download_id}/progress", response_model=DownloadItem)
def progress(request: Request, download_id: str, body: ProgressIn) -> DownloadItem:
    item = _item_or_404(request, download_id)
    item.received = body.received
    item.total = body.total
    return item


class CompleteIn(BaseModel):
    # A local temp path written by OUR shell process; the API is loopback-only
    # and guarded by the per-launch token, so the path is trusted input here.
    path: str


@router.post("/{download_id}/complete", response_model=TitleOut)
def complete(request: Request, download_id: str, body: CompleteIn) -> TitleOut:
    item = _item_or_404(request, download_id)
    src = Path(body.path)
    if not src.is_file():
        item.state = "failed"
        item.error = "downloaded file not found"
        raise HTTPException(status_code=400, detail=item.error)
    size = src.stat().st_size
    # pages/size are stamped by the vault at ingest, from the stored zip
    sidecar = {
        "fileUrl": item.fileUrl,
        "pageUrl": item.pageUrl,
        "filename": item.filename or src.name,
        "downloadedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    try:
        result = _lib(request).attach_chapter_media(
            item.titleId, num=item.num, lang=item.lang, group=item.group,
            src=src, sidecar=sidecar,
        )
    except media.UnsupportedArchiveError as exc:
        item.state = "failed"
        item.error = str(exc)
        raise HTTPException(status_code=422, detail=item.error)
    except media.MediaInUseError as exc:
        # re-downloading the episode that is playing: the old file is still held
        item.state = "failed"
        item.error = (f"“{exc}” is open right now — close the player on this "
                      f"entry and download it again")
        raise HTTPException(status_code=409, detail=item.error)
    if result is None:
        item.state = "failed"
        item.error = "title no longer exists"
        raise HTTPException(status_code=404, detail=item.error)
    item.state = "done"
    item.received = size
    item.total = size
    return result


class FailedIn(BaseModel):
    reason: str = ""


@router.post("/{download_id}/failed", response_model=DownloadItem)
def failed(request: Request, download_id: str, body: FailedIn) -> DownloadItem:
    item = _item_or_404(request, download_id)
    item.state = "failed"
    item.error = body.reason or "interrupted"
    return item
