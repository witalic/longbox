"""Whether a launch may trust the index (CLAUDE.md: a launch reads the index, not
the vault).

A serving session is marked open in `.longbox/session.json` and marked closed —
with the index file as it was left — only when it settled: closed through
`POST /api/close`, its check finished, nothing cut off. Anything else makes the
next launch verify the whole vault, and the log says which it was: on a network
vault that verification is the minute of reading a launch is asked about.

A launch after a settled session still looks for titles added or removed by
hand — but not by listing every shelf. A folder's time moves when an entry is
added to it, removed or renamed, so the session takes each shelf's time as it
ends (`shelves`, kept in the index) and the next launch lists only a shelf whose
time moved. On a network vault that listing is the whole cost of a quiet launch
— 2,600 title folders took eight seconds on a cold NAS, and the share answered
nothing else meanwhile, the window's first requests included.
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING

from .vault import APP_DIR, Vault, not_a_shelf, type_dir_name

if TYPE_CHECKING:
    from .index import LibraryIndex

log = logging.getLogger("longbox.library")

# A folder time this close to the moment it is read may still move within the
# same tick of a coarse clock (a whole second on some shares): not taken, so the
# next launch lists that shelf.
_SETTLING_NS = 2_000_000_000

DOC = f"{APP_DIR}/session.json"
SCHEMA = 1


def stamp_of(path: Path) -> str:
    try:
        st = path.stat()
    except OSError:
        return ""
    return f"{st.st_mtime_ns}.{st.st_size}"


def begin(vault: Vault, found: str) -> tuple[bool, str]:
    """Mark this session open. Says whether it is marked, and why the whole
    vault has to be verified — "" when the index can be trusted. `found`: the
    index file as this session found it, before anything opened it."""
    try:
        last = vault.read_doc(DOC)
        with vault.doc_transaction(DOC, schema=SCHEMA) as doc:
            doc["open"] = True
            doc.pop("index", None)
    except Exception as exc:  # noqa: BLE001 — a marker that cannot be read or written: verify
        return False, f"the session record could not be read or written ({exc})"
    if not last:
        return True, "there is no record of the last session"
    if last.get("open") is not False:
        return True, "the last session did not close"
    if not found:
        return True, "the index file was not there"
    if last.get("index") != found:
        return True, f"the index file changed after the last session (left {last.get('index')}, found {found})"
    return True, ""


def shelves(root: Path) -> dict[str, int]:
    """Each shelf's folder time — asked of the folder itself: NTFS updates the
    copy a listing of the root carries lazily."""
    now, out = time.time_ns(), {}
    with os.scandir(root) as entries:
        for e in entries:
            if e.is_dir() and not not_a_shelf(e.name):
                at = os.stat(e.path).st_mtime_ns
                if now - at > _SETTLING_NS:
                    out[e.name] = at
    return out


def since(index: LibraryIndex) -> tuple[dict[str, int], dict[str, str]]:
    """What the last settled session left of the shelves, for `Vault.title_dirs`:
    each one's folder time, and the shelf each indexed title is on."""
    unchanged = index.shelf_stamps()
    return unchanged, ({tid: type_dir_name(t) for tid, t in index.types().items()} if unchanged else {})


def leave(index: LibraryIndex, root: Path, settled: bool) -> None:
    """Keep each shelf's folder time in the index as this session leaves it —
    none for one that did not settle: the launch after it checks every shelf."""
    try:
        index.set_shelf_stamps(shelves(root) if settled else {})
    except Exception as exc:  # noqa: BLE001 — without them the next launch lists every shelf
        log.warning("the shelves' times were not kept: %s", exc)


def end(vault: Vault) -> None:
    """Mark the session closed, with the index file as it leaves it."""
    with vault.doc_transaction(DOC, schema=SCHEMA) as doc:
        doc["open"] = False
        doc["index"] = stamp_of(vault.root / "index.db")
