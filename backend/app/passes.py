"""The one vault pass at a time.

Every long operation over the library — checking it, converting archives,
rewriting metadata, sweeping leftovers, retyping a field — takes this slot. One
at a time because they all walk the same vault and on a network share two of
them only get in each other's way; one MACHINE because a row that grew its own
progress is how a settings page stops looking like one page.

It lives here rather than in a router so that operations belonging to other
routers report and stop exactly the same way.
"""
from __future__ import annotations

import threading
from contextlib import contextmanager

from fastapi import HTTPException

# The vault passes — one at a time, whichever it is: they all walk the whole
# library, and on a network share two of them only get in each other's way.
#
# Progress is read by the UI from a PARALLEL request while the (sync,
# threadpool-run) pass is still working, the same way a rebuild reports itself.
# `stop` is the event the pass polls; the service methods decide where it is
# safe to put a pass down, and every one of them reports whether it was.
_LOCK = threading.Lock()
# `key` is the row that started it. The client keeps NO state of its own about
# a running pass: leaving the page (or reloading it) must not lose the progress
# of something the server is still doing.
PASS = {"running": False, "op": "", "key": "", "done": 0, "total": 0}
_PASS_STOP = threading.Event()


@contextmanager
def _pass(op: str, key: str = ""):
    """Claim the one pass slot, or refuse. Yields the progress callback and the
    stop event the pass should poll."""
    if not _LOCK.acquire(blocking=False):
        raise HTTPException(status_code=409,
                            detail=f"{PASS['op'] or 'a vault pass'} is already running")
    _PASS_STOP.clear()
    PASS.update(running=True, op=op, key=key, done=0, total=0)
    try:
        yield (lambda done, total: PASS.update(done=done, total=total)), _PASS_STOP
    finally:
        PASS.update(running=False, op="", key="")
        _LOCK.release()




def stop_event() -> threading.Event:
    """The event a running pass polls — set by the stop endpoint."""
    return _PASS_STOP
