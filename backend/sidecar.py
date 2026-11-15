#!/usr/bin/env python
"""Entry point for the FROZEN sidecar (PyInstaller); dev runs uvicorn directly.

A packaged build ships this as a plain executable the Electron shell spawns
exactly like it spawns `python -m uvicorn` in development — same environment
variables, same loopback-only server, same /health handshake.
"""
from __future__ import annotations

import multiprocessing
import os

import uvicorn

from app.main import app


def main() -> None:
    port = int(os.environ.get("LONGBOX_PORT", "8787"))
    # host is loopback, always: the vault is never served to the network
    uvicorn.run(app, host="127.0.0.1", port=port, access_log=False, log_level="warning")


if __name__ == "__main__":
    # a frozen build on Windows re-executes itself for any child process
    multiprocessing.freeze_support()
    main()
