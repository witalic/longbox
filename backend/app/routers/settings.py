"""Settings endpoints: the registered libraries (switchable on the fly), the
browser homepage, and index maintenance."""
from __future__ import annotations

import threading
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..config_store import config_transaction, load_config
from ..library.service import Library

router = APIRouter(prefix="/api")


DEFAULT_HOMEPAGE = "https://www.google.com"

# ONE source of app identity: <repo>/app-meta.json (name, version, updated).
_META_PATH = Path(__file__).resolve().parents[3] / "app-meta.json"


def app_meta() -> dict:
    try:
        import json
        return json.loads(_META_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"name": "longbox", "version": "0.0.0", "updated": "", "description": ""}


class SettingsOut(BaseModel):
    library_path: str
    title_count: int
    homepage: str
    libraries: list[str]
    app: dict = {}


class LibraryPathIn(BaseModel):
    path: str


class HomepageIn(BaseModel):
    homepage: str


def _lib(request: Request) -> Library:
    return request.app.state.library


def _norm(p: str) -> str:
    return str(Path(p).expanduser())


def _libraries(cfg: dict, current: str) -> list[str]:
    """The registered library list, with the active one always present (first)."""
    libs = [x for x in (cfg.get("libraries") or []) if isinstance(x, str) and x.strip()]
    if current not in libs:
        libs.insert(0, current)
    return libs


def _out(request: Request, cfg: dict | None = None) -> SettingsOut:
    cfg = cfg if cfg is not None else load_config()
    lib = _lib(request)
    current = str(lib.root)
    return SettingsOut(
        library_path=current, title_count=lib.count(),
        homepage=cfg.get("homepage") or DEFAULT_HOMEPAGE,
        libraries=_libraries(cfg, current),
        app=app_meta(),
    )


@router.get("/settings", response_model=SettingsOut)
def get_settings_ep(request: Request) -> SettingsOut:
    return _out(request)


@router.put("/settings/homepage", response_model=SettingsOut)
def set_homepage(request: Request, body: HomepageIn) -> SettingsOut:
    with config_transaction() as cfg:
        cfg["homepage"] = body.homepage.strip() or DEFAULT_HOMEPAGE
    return _out(request, cfg)


@router.put("/settings/library-path", response_model=SettingsOut)
def set_library_path(request: Request, body: LibraryPathIn) -> SettingsOut:
    """Switch the ACTIVE library (registering the path). Existing content at the
    target is simply indexed — switching never touches files."""
    path = Path(_norm(body.path))
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"cannot use that path: {exc}")

    with config_transaction() as cfg:
        cfg["library_path"] = str(path)
        libs = [x for x in (cfg.get("libraries") or []) if isinstance(x, str) and x.strip()]
        # switching must REGISTER both sides: the library we're leaving (which may
        # predate the list entirely) and the one we're entering — nothing vanishes
        previous = str(_lib(request).root)
        if previous not in libs:
            libs.insert(0, previous)
        if str(path) not in libs:
            libs.append(str(path))
        cfg["libraries"] = libs

    # Swap the live library to the new location (indexes whatever vault is there).
    # The old one closes on a delay: requests already holding it mid-query must
    # not hit a closed SQLite connection.
    old = _lib(request)
    new = Library(path)
    request.app.state.library = new
    threading.Timer(5.0, old.close).start()
    return _out(request, cfg)


@router.delete("/settings/libraries", response_model=SettingsOut)
def remove_library(request: Request, body: LibraryPathIn) -> SettingsOut:
    """Forget a registered library path — the folder and its files stay on disk."""
    target = _norm(body.path)
    if target == str(_lib(request).root):
        raise HTTPException(status_code=409, detail="switch to another library first")
    with config_transaction() as cfg:
        cfg["libraries"] = [x for x in _libraries(cfg, str(_lib(request).root)) if _norm(x) != target]
    return _out(request, cfg)


# Rebuild progress — read by the UI from a parallel request while the (sync,
# threadpool-run) rebuild request is still streaming through the files.
REBUILD = {"running": False, "done": 0, "total": 0}
_REBUILD_LOCK = threading.Lock()  # claim-the-run must be atomic
_NORMALIZE_LOCK = threading.Lock()


@router.get("/settings/rebuild/status")
def rebuild_status() -> dict:
    return dict(REBUILD)


class NormalizeOut(BaseModel):
    converted: int


@router.post("/settings/normalize-archives", response_model=NormalizeOut)
def normalize_archives(request: Request) -> NormalizeOut:
    """Re-run the archive sweep by hand: convert anything in the vault that is
    not a plain zip (retrying archives that failed before — e.g. after
    installing an unrar backend). Normally this runs once per vault."""
    if not _NORMALIZE_LOCK.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="a conversion pass is already running")
    try:
        return NormalizeOut(converted=_lib(request).normalize_archives(force=True))
    finally:
        _NORMALIZE_LOCK.release()


@router.post("/settings/rebuild", response_model=SettingsOut)
def rebuild_index(request: Request) -> SettingsOut:
    """Rebuild the SQLite index purely from the files on disk (reads every
    title, writes nothing)."""
    if not _REBUILD_LOCK.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="a rebuild is already running")
    try:
        REBUILD.update(running=True, done=0, total=0)
        _lib(request).rescan(lambda done, total: REBUILD.update(done=done, total=total))
    finally:
        REBUILD["running"] = False
        _REBUILD_LOCK.release()
    return _out(request)
