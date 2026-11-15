"""Whether a launch may trust the index (CLAUDE.md: a launch reads the index, not
the vault).

A serving session is marked open in `.longbox/session.json` and marked closed —
with the index file as it was left — only when it settled: closed through
`POST /api/close`, its check finished, nothing cut off. Anything else makes the
next launch verify the whole vault, and the log says which it was: on a network
vault that verification is the minute of reading a launch is asked about.
"""
from __future__ import annotations

from pathlib import Path

from .vault import APP_DIR, Vault

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


def end(vault: Vault) -> None:
    """Mark the session closed, with the index file as it leaves it."""
    with vault.doc_transaction(DOC, schema=SCHEMA) as doc:
        doc["open"] = False
        doc["index"] = stamp_of(vault.root / "index.db")
