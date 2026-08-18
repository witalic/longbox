#!/usr/bin/env python
"""Entry point for the FROZEN sidecar (PyInstaller); dev runs uvicorn directly.

A packaged build ships this as a plain executable the Electron shell spawns
exactly like it spawns `python -m uvicorn` in development — same environment
variables, same loopback-only server, same /health handshake.
"""
from __future__ import annotations

import logging
import multiprocessing
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

import uvicorn

from app.main import app


def _log_to_file() -> None:
    """A packaged build has no console, so a slow or failed start is invisible —
    to the user AND to anyone trying to report it. The log lands next to the
    app's config, which is the one directory that always exists."""
    cfg = os.environ.get("LONGBOX_CONFIG_DIR")
    if not cfg:
        return
    try:
        handler = RotatingFileHandler(Path(cfg) / "longbox.log", maxBytes=512_000,
                                      backupCount=1, encoding="utf-8")
    except OSError:
        return  # a log we cannot write must never stop the app from running
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def main() -> None:
    _log_to_file()
    port = int(os.environ.get("LONGBOX_PORT", "8787"))
    # host is loopback, always: the vault is never served to the network
    uvicorn.run(app, host="127.0.0.1", port=port, access_log=False, log_level="warning")


if __name__ == "__main__":
    # a frozen build on Windows re-executes itself for any child process
    multiprocessing.freeze_support()
    main()
