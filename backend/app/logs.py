"""The app's log: one file in the vault (`.longbox/longbox.log`), written by the
sidecar for itself, the shell and the window — whichever way it was started.

A slow start, or a disk busy for a minute after one, is read off the log, never
guessed: every step of a launch, every job that runs in the background (when it
started, how long it took, what it did, or why it stopped), why a launch checked
the whole vault, why a close left the session open, and any request that took
long. Lines are events, never a stream: the log lives on the vault's drive, and
writing it must not become the next thing that keeps that drive busy.

A launch is a run: it opens with a `----- run <id> -----` line, and every line
of it — the shell's, the sidecar's, the window's — carries that id, so one run
is one search. The shell makes the id and hands it over with the lines it said
before the sidecar existed (`LONGBOX_RUN`, `LONGBOX_LOG_FIRST`), which are
written first. Three files of a megabyte, so a run that said a lot does not
push the one before it out.
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import time
from collections.abc import Iterator
from contextlib import contextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path

from pydantic import BaseModel

log = logging.getLogger("longbox")

FILE = "longbox.log"
MAX_BYTES = 1_000_000
KEEP = 2  # older files beside it
# a request taking longer than this is worth a line of its own
SLOW_S = 1.0
_LEVELS = {"info": logging.INFO, "warning": logging.WARNING, "error": logging.ERROR}
_handler: RotatingFileHandler | None = None


def to_file(app_dir: Path) -> None:
    """Write the log into the vault's own folder — once per process: a process
    serves one vault, and opening another is a restart into it. The run opens
    with its line, and the shell's first lines follow it."""
    global _handler
    if _handler is not None:
        return
    # the shell's id for this run; a sidecar started on its own makes one
    run = os.environ.get("LONGBOX_RUN") or secrets.token_hex(2)
    try:
        handler = RotatingFileHandler(app_dir / FILE, maxBytes=MAX_BYTES, backupCount=KEEP, encoding="utf-8")
    except OSError:
        return  # a log that cannot be written never stops the app
    handler.setFormatter(logging.Formatter(f"%(asctime)s [{run}] %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    # uvicorn keeps its loggers to itself: a request that raised is one of its lines
    if not (logging.getLogger("uvicorn.error").propagate and logging.getLogger("uvicorn").propagate):
        logging.getLogger("uvicorn.error").addHandler(handler)
    _handler = handler
    log.info("----- run %s -----", run)
    try:
        first = [Line.model_validate(x) for x in json.loads(os.environ.get("LONGBOX_LOG_FIRST") or "[]")]
    except ValueError:
        first = []
    write("shell", first)


@contextmanager
def step(what: str, logger: logging.Logger = log) -> Iterator[list[str]]:
    """One piece of work in the log: a line as it starts, and one as it ends —
    how long it took and what it reports (the strings put in the list it
    yields), or how far it got before it failed."""
    logger.info("%s — started", what)
    said: list[str] = []
    started = time.perf_counter()
    try:
        yield said
    except BaseException as exc:
        logger.warning("%s — failed after %.1fs: %s", what, time.perf_counter() - started,
                       str(exc) or type(exc).__name__)
        raise
    logger.info("%s — done in %.1fs%s", what, time.perf_counter() - started,
                f": {'; '.join(said)}" if said else "")


class Line(BaseModel):
    at: float = 0       # when it was said, in ms since the epoch — the shell holds a launch's first lines
    level: str = "info"
    text: str


class Said(BaseModel):
    source: str = "shell"  # or "window"
    lines: list[Line] = []


def write(source: str, lines: list[Line]) -> None:
    """Lines the shell or the window said, under their own name and time."""
    logger = logging.getLogger(f"longbox.{source}")
    for line in lines[:200]:
        record = logger.makeRecord(logger.name, _LEVELS.get(line.level, logging.INFO), "", 0,
                                   line.text[:2000], None, None)
        if line.at > 0:
            record.created = line.at / 1000
            record.msecs = line.at % 1000
        logger.handle(record)
