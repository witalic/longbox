"""Importing a folder the owner already has (design/folder-import.md §10).

The scan is held in memory by id: the preview re-plans from it on every change
of a rule, so a keystroke never walks the disk again. Running reads the folder
once more — what is stored is what is on the disk NOW, not what was there when
it was chosen — takes the one vault-pass slot, and reports through the same
status the other passes do."""
from __future__ import annotations

import os
import threading
import uuid
from collections.abc import Callable
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from ..library import folder_import as fi
from ..library.service import Library
from ..passes import PASS, _pass, stop_event

router = APIRouter(prefix="/api/import")

KEEP_SCANS = 4   # trees held for the preview; an old one is scanned again on demand
KEEP_RUNS = 10   # reports kept in the vault they went into
KEEP_PAUSED = 10
_RUNS = "import_runs"
_PAUSED = "paused_imports"
_SCANS_LOCK = threading.Lock()
# Set with the pass's stop event by Pause: the run stops between two entries as
# a stopped one does, and is then remembered to be carried on.
_PAUSE = threading.Event()


def _lib(request: Request) -> Library:
    return request.app.state.library


def _scans(request: Request) -> dict[str, fi.Scan]:
    with _SCANS_LOCK:
        if not hasattr(request.app.state, "import_scans"):
            request.app.state.import_scans = {}
        return request.app.state.import_scans


def _inside(a: Path, b: Path) -> bool:
    a, b = os.path.normcase(str(a)), os.path.normcase(str(b))
    return a == b or a.startswith(b.rstrip("\\/") + os.sep)


class ScanIn(BaseModel):
    # A path from OUR shell's folder picker; the API is loopback-only and guarded
    # by the per-launch token, so the path is trusted input here.
    root: str


class ScanOut(BaseModel):
    scanId: str
    root: str
    levels: list[dict]
    counts: dict[str, int]
    skipped: list[str]


@router.post("/scan", response_model=ScanOut)
def scan(request: Request, body: ScanIn) -> ScanOut:
    root = Path(body.root).expanduser()
    if not root.is_dir():
        raise HTTPException(status_code=422, detail="that folder does not exist")
    vault = _lib(request).root.resolve()
    if _inside(root.resolve(), vault):
        raise HTTPException(status_code=422, detail="that folder is inside the library itself")
    try:
        s = fi.scan(root, skip=vault)
    except OSError as e:
        raise HTTPException(status_code=422, detail=f"the folder cannot be read: {e.strerror or e}")
    scans = _scans(request)
    with _SCANS_LOCK:
        sid = uuid.uuid4().hex[:12]
        scans[sid] = s
        while len(scans) > KEEP_SCANS:
            scans.pop(next(iter(scans)))
    return ScanOut(scanId=sid, **fi.describe(s))


class PlanIn(BaseModel):
    scanId: str
    profile: fi.Profile
    overrides: fi.Overrides = fi.Overrides()


def _scan_of(request: Request, scan_id: str) -> fi.Scan:
    s = _scans(request).get(scan_id)
    if s is None:
        raise HTTPException(status_code=404, detail="that scan is gone — choose the folder again")
    return s


@router.post("/plan", response_model=fi.Plan)
def plan(request: Request, body: PlanIn) -> fi.Plan:
    try:
        return fi.plan(_lib(request), _scan_of(request, body.scanId), body.profile, body.overrides)
    except fi.InvalidProfile as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


class PausedImport(BaseModel):
    """An import put down to be carried on — later, or after a restart. Kept in
    the vault it was going into. Carrying it on reads the folder again and runs
    the same plan, so what is already stored is skipped, never stored twice."""
    id: str
    root: str
    profile: fi.Profile
    overrides: fi.Overrides = fi.Overrides()
    at: str = ""
    # how far it had got — a run carried on counts on from here
    done: int = 0
    total: int = 0
    doneBytes: int = 0
    totalBytes: int = 0


def _same_path(a: str, b: str) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def _read_paused(raws: list) -> list[PausedImport]:
    out = []
    for raw in raws:
        try:
            out.append(PausedImport.model_validate(raw))
        except ValueError:
            continue  # a record from an older shape is not worth a failed import
    return out


def _keep_paused(lib: Library, change: Callable[[list[PausedImport]], list[PausedImport]]) -> None:
    with lib.state.edit(_PAUSED) as rows:
        rows[:] = [p.model_dump() for p in change(_read_paused(rows))][:KEEP_PAUSED]


def _paused(request: Request) -> list[PausedImport]:
    return _read_paused(_lib(request).state.rows(_PAUSED))


def _run(request: Request, root: Path, profile: fi.Profile, overrides: fi.Overrides, *,
         scan_id: str = "", carried: PausedImport | None = None) -> fi.Report:
    """One run over the folder as it is now. It takes the place of an import of
    the same folder that was put down, and a run carried on from one counts on
    from where that one stopped."""
    lib = _lib(request)
    with _pass(f"importing {root.name}", "import") as (progress, stop):
        _PAUSE.clear()
        try:
            s = fi.scan(root, skip=lib.root.resolve())
        except OSError as e:
            raise HTTPException(status_code=422, detail=f"the folder cannot be read: {e.strerror or e}")
        if scan_id:
            scans = _scans(request)  # takes the scans' lock itself
            with _SCANS_LOCK:
                scans[scan_id] = s  # the next preview starts from this reading too
        _keep_paused(lib, lambda rows: [p for p in rows if not _same_path(p.root, str(root))])
        n0, b0 = (carried.done, carried.doneBytes) if carried else (0, 0)

        def counted(done: int, total: int, detail: str = "", done_bytes: int = 0, total_bytes: int = 0) -> None:
            progress(n0 + done, n0 + total, detail, b0 + done_bytes, b0 + total_bytes)

        report = fi.run(lib, s, profile, overrides, progress=counted, stop=stop)
        if report.stopped and _PAUSE.is_set():
            report.paused = True
            held = PausedImport(
                id=uuid.uuid4().hex[:12], root=str(root), profile=profile,
                overrides=overrides, at=report.at, done=PASS["done"], total=PASS["total"],
                doneBytes=PASS["doneBytes"], totalBytes=PASS["totalBytes"])
            _keep_paused(lib, lambda rows: [held, *rows])
    with lib.state.edit(_RUNS) as rows:
        rows[:] = [report.model_dump(), *rows][:KEEP_RUNS]
    return report


@router.post("/run", response_model=fi.Report)
def run(request: Request, body: PlanIn) -> fi.Report:
    """Blocks until the import ends; progress is read from the pass status in
    parallel, and the report is kept for this machine's list of last runs."""
    s = _scan_of(request, body.scanId)
    try:
        fi.check(body.profile)
        fi.check_overrides(body.overrides)
    except fi.InvalidProfile as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    report = _run(request, s.root, body.profile, body.overrides, scan_id=body.scanId)
    if body.profile.files == "move":
        _scans(request).pop(body.scanId, None)  # the tree it describes is gone
    return report


@router.post("/pause")
def pause() -> dict:
    """Put the running import down to be carried on: it stops between two
    entries, as Stop does, and is remembered with all it takes to go on."""
    if not PASS["running"] or PASS["key"] != "import":
        raise HTTPException(status_code=409, detail="no import is running")
    _PAUSE.set()
    stop_event().set()
    return dict(PASS)


@router.get("/paused", response_model=list[PausedImport])
def paused(request: Request) -> list[PausedImport]:
    return _paused(request)


@router.post("/paused/{paused_id}/resume", response_model=fi.Report)
def resume(request: Request, paused_id: str) -> fi.Report:
    held = next((p for p in _paused(request) if p.id == paused_id), None)
    if held is None:
        raise HTTPException(status_code=404, detail="that paused import is gone")
    if not Path(held.root).is_dir():
        raise HTTPException(status_code=422, detail=f"{held.root} is not there any more")
    try:
        fi.check(held.profile)
        fi.check_overrides(held.overrides)
    except fi.InvalidProfile as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return _run(request, Path(held.root), held.profile, held.overrides, carried=held)


@router.delete("/paused/{paused_id}", status_code=204)
def forget_paused(request: Request, paused_id: str) -> Response:
    _keep_paused(_lib(request), lambda rows: [p for p in rows if p.id != paused_id])
    return Response(status_code=204)


@router.get("/runs", response_model=list[fi.Report])
def runs(request: Request) -> list[fi.Report]:
    out = []
    for raw in _lib(request).state.rows(_RUNS):
        try:
            out.append(fi.Report.model_validate(raw))
        except ValueError:
            continue  # a record from an older shape is not worth a failed page
    return out


@router.get("/profiles", response_model=list[fi.Profile])
def profiles(request: Request) -> list[fi.Profile]:
    return fi.ProfileStore(_lib(request).vault).profiles()


@router.put("/profiles", response_model=list[fi.Profile])
def save_profiles(request: Request, body: list[fi.Profile]) -> list[fi.Profile]:
    try:
        return fi.ProfileStore(_lib(request).vault).save(body)
    except fi.InvalidProfile as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
