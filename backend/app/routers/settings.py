"""Settings endpoints: the registered libraries (switchable on the fly), the
browser homepage, and index maintenance."""
from __future__ import annotations

import json
import sys
import threading
from contextlib import contextmanager
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..config_store import config_transaction, load_config
from ..passes import PASS, _pass, stop_event
from ..library.service import Library
from .downloads import forget_downloads

router = APIRouter(prefix="/api")


DEFAULT_HOMEPAGE = "https://www.google.com"



# ONE source of app identity: app-meta.json (name, version, updated).
#
# Two homes, because the sidecar has two shapes: a source checkout keeps it at
# the repo root, and a FROZEN build carries it inside the bundle — where
# `parents[3]` lands in a temp directory that holds nothing. Without the second
# path every packaged build reported itself as 0.0.0.
def _meta_paths() -> tuple[Path, ...]:
    here = Path(__file__).resolve()
    bundle = getattr(sys, "_MEIPASS", "")
    return tuple(p / "app-meta.json" for p in (
        *( (Path(bundle),) if bundle else () ),
        here.parents[3],
    ))


def app_meta() -> dict:
    for path in _meta_paths():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
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

    old = _lib(request)
    if path.resolve() == Path(old.root).resolve():
        return _out(request)  # already there — a second Library over one vault
                              # would mean two lock sets guarding the same files

    # Open the new location FIRST: if it can't be indexed, the switch fails with
    # the old library still live. Writing the config before that would point the
    # next launch at a folder the app cannot open — with no UI left to fix it.
    try:
        # the index is enough to answer with; the disk is verified behind us
        new = Library(path, defer_sync=True)
    except Exception as exc:  # noqa: BLE001 — an unreadable vault must not strand the app
        raise HTTPException(status_code=400, detail=f"cannot open that library: {exc}")
    new.sync_in_background()

    with config_transaction() as cfg:
        cfg["library_path"] = str(path)
        libs = [x for x in (cfg.get("libraries") or []) if isinstance(x, str) and x.strip()]
        # switching must REGISTER both sides: the library we're leaving (which may
        # predate the list entirely) and the one we're entering — nothing vanishes
        previous = str(old.root)
        if previous not in libs:
            libs.insert(0, previous)
        if str(path) not in libs:
            libs.append(str(path))
        cfg["libraries"] = libs

    forget_downloads(request.app)  # an arm belongs to the vault it was made in
    request.app.state.library = new
    # the old one closes on a delay: requests already holding it mid-query must
    # not hit a closed SQLite connection
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


@router.get("/settings/pass/status")
def pass_status() -> dict:
    """How the running vault pass is going. Nothing running answers the same
    shape with `running: false`, so the UI polls one endpoint for all of them."""
    return dict(PASS)


@router.post("/settings/pass/stop")
def stop_pass() -> dict:
    """Put the running pass down at the next title. It reports what it covered
    before that — a partial answer that says it is partial."""
    stop_event().set()
    return dict(PASS)


@router.get("/settings/library/status")
def library_status(request: Request) -> dict:
    """How the live library's verification against disk is going. A first-ever
    open fills the index here (progress worth showing); later opens find nothing
    and finish before anyone looks."""
    lib = _lib(request)
    return {"path": str(lib.root), **lib.sync_state}


class NormalizeOut(BaseModel):
    converted: int


@router.post("/settings/normalize-archives", response_model=NormalizeOut)
def normalize_archives(request: Request) -> NormalizeOut:
    """Re-run the archive sweep by hand: convert anything in the vault that is
    not a plain zip (retrying archives that failed before — e.g. after
    installing an unrar backend). Normally this runs once per vault."""
    with _pass("converting archives", "convert") as (progress, stop):
        converted = _lib(request).normalize_archives(force=True, progress=progress, stop=stop)
    if converted < 0:
        raise HTTPException(status_code=409, detail="a conversion pass is already running")
    return NormalizeOut(converted=converted)


class BrokenRow(BaseModel):
    titleId: str
    title: str
    what: str
    count: int
    num: str = ""
    lang: str = ""
    group: str = ""


class Broken(BaseModel):
    total: int
    titles: int
    rows: list[BrokenRow]


class LeftoverRow(BaseModel):
    titleId: str
    title: str
    name: str
    bytes: int


class Leftovers(BaseModel):
    files: int
    bytes: int
    titles: int
    # every affected title, uncapped — this is the scope the sweep works in
    titleIds: list[str] = []
    rows: list[LeftoverRow]


class DupCopy(BaseModel):
    titleId: str
    title: str
    num: str = ""
    lang: str = ""
    group: str = ""
    detail: str = ""
    size: int = 0


class DupGroup(BaseModel):
    sha256: str
    size: int
    wasted: int
    copies: list[DupCopy]


class Duplicates(BaseModel):
    sets: int
    bytes: int
    groups: list[DupGroup]


class GapRow(BaseModel):
    titleId: str
    title: str
    lang: str = ""
    group: str = ""
    missing: list[int]
    what: str


class Gaps(BaseModel):
    titles: int
    rows: list[GapRow]


class HistoryEntry(BaseModel):
    op: str
    at: str
    seconds: float = 0
    outcome: str = ""
    stopped: bool = False


class CheckOut(BaseModel):
    """Three answers from one pass. `rows` are capped; the totals never are."""
    checked: int
    hashed: int
    total: int
    expected: int
    withDigest: int
    deep: bool
    stopped: bool
    systemic: bool
    at: str = ""
    broken: Broken
    leftovers: Leftovers
    duplicates: Duplicates
    gaps: Gaps


class HealthOut(BaseModel):
    """What has been done to this library, and the last report it produced."""
    history: list[HistoryEntry]
    lastCheck: CheckOut | None = None


@router.get("/settings/health", response_model=HealthOut)
def vault_health(request: Request) -> HealthOut:
    """The stored record. Read when the panel opens, so the last answer is
    there without re-earning it — and so "has this library ever been checked"
    has an answer at all."""
    return HealthOut(**_lib(request).health())


@router.post("/settings/check", response_model=CheckOut)
def check_vault(request: Request, deep: bool = False, backfill: bool = False) -> CheckOut:
    """Is anything broken, is anything wasting space, is anything missing.

    `deep` re-reads every byte to compare checksums; `backfill` records one for
    content stored before there were any — which establishes a baseline, it does
    not verify one, and is reported separately from what it checked."""
    # the name is what the UI shows beside the progress: switching the mode
    # mid-run must not change what the RUNNING pass says it is doing
    what = "full check + record" if backfill else ("full check" if deep else "quick check")
    with _pass(what, "check") as (progress, stop):
        return CheckOut(**_lib(request).check(deep=deep, backfill=backfill, name=what,
                                              progress=progress, stop=stop))


class SweptOut(BaseModel):
    deleted: int
    failed: int
    bytes: int


@router.post("/settings/leftovers", response_model=SweptOut)
def delete_leftovers(request: Request) -> SweptOut:
    """Delete the files that belong to no entry at all.

    Which titles to look in comes from the LAST CHECK, stored in the vault —
    never from the request, which carries nothing at all. Inside each one the
    leftovers are worked out again from the entries the title has, so this
    cannot be handed a path to delete."""
    lib = _lib(request)
    last = lib.health().get("lastCheck") or {}
    scope = (last.get("leftovers") or {}).get("titleIds")
    if scope is None:
        # The service can sweep the whole library, and says so — but that is a
        # lock and a directory listing per title, and doing it SILENTLY because
        # a stored report happens to predate the scope is exactly the kind of
        # cost that should never be spent without saying so.
        raise HTTPException(
            status_code=409,
            detail="this report is from an older check and does not say where the leftovers "
                   "are — run a check first")
    if not scope:
        return SweptOut(deleted=0, failed=0, bytes=0)
    with _pass("deleting leftovers", "sweep") as (progress, stop):
        return SweptOut(**lib.delete_leftovers(scope, progress=progress, stop=stop))


class MirrorOut(BaseModel):
    written: int
    stopped: bool = False


@router.post("/settings/comicinfo", response_model=MirrorOut)
def refresh_comicinfo(request: Request) -> MirrorOut:
    """Bring every archive's ComicInfo.xml in line with the library. Ingest and
    page edits keep it current for free; a metadata edit does not, because
    rewriting archives to carry a changed tag is not a quiet cost."""
    with _pass("updating metadata", "mirror") as (progress, stop):
        return MirrorOut(**_lib(request).refresh_comicinfo(progress=progress, stop=stop))


class Gap(BaseModel):
    titleId: str
    title: str
    lang: str = ""
    group: str = ""
    missing: list[int]


@router.get("/settings/gaps", response_model=list[Gap])
def numbering_gaps(request: Request, title_id: str) -> list[Gap]:
    """What one title is missing — the quiet line on its own page. Index-only
    and immediate, so it is outside the pass slot: it must not be refused
    because a check happens to be running."""
    return [Gap(**g) for g in _lib(request).gaps(title_id)]


@router.post("/settings/rebuild", response_model=SettingsOut)
def rebuild_index(request: Request) -> SettingsOut:
    """Rebuild the SQLite index purely from the files on disk (reads every
    title, writes nothing)."""
    with _pass("rebuilding the index", "rebuild") as (progress, stop):
        _lib(request).rescan(progress, stop)
    return _out(request)
