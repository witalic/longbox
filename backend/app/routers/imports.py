"""Importing a folder the owner already has (design/folder-import.md §10).

The scan is held in memory by id: the preview re-plans from it on every change
of a rule, so a keystroke never walks the disk again. Running takes the one
vault-pass slot, and reports through the same status the other passes do."""
from __future__ import annotations

import os
import threading
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..config_store import config_transaction, load_config
from ..library import folder_import as fi
from ..library.service import Library
from ..passes import _pass

router = APIRouter(prefix="/api/import")

KEEP_SCANS = 4   # trees held for the preview; an old one is scanned again on demand
KEEP_RUNS = 10   # reports kept per machine — they name this machine's paths
_RUNS = "import_runs"
_SCANS_LOCK = threading.Lock()


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
    with _pass(f"importing {s.root.name}", "import") as (progress, stop):
        report = fi.run(_lib(request), s, body.profile, body.overrides, progress=progress, stop=stop)
    with config_transaction() as cfg:
        cfg[_RUNS] = [report.model_dump(), *(cfg.get(_RUNS) or [])][:KEEP_RUNS]
    if body.profile.files == "move":
        _scans(request).pop(body.scanId, None)  # the tree it describes is gone
    return report


@router.get("/runs", response_model=list[fi.Report])
def runs() -> list[fi.Report]:
    out = []
    for raw in load_config().get(_RUNS) or []:
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
