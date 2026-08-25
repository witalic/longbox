"""App configuration persisted OUTSIDE the library (so it survives changing the
library path). A small JSON file in the OS per-user config dir.
"""
from __future__ import annotations

import json
import os
import sys
import threading
from contextlib import contextmanager
from pathlib import Path

# Endpoints run on FastAPI's threadpool, so two config writers can race; every
# read-modify-write must go through config_transaction().
_CONFIG_LOCK = threading.RLock()


def config_dir() -> Path:
    # The shell passes its stable per-app dir (Electron userData) so config lives in
    # one unambiguous place across launches; fall back to the OS config dir otherwise.
    override = os.environ.get("LONGBOX_CONFIG_DIR")
    if override:
        d = Path(override)
    elif sys.platform == "win32":
        d = Path(os.environ.get("APPDATA") or Path.home() / "AppData/Roaming") / "longbox"
    elif sys.platform == "darwin":
        d = Path.home() / "Library" / "Application Support" / "longbox"
    else:
        d = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "longbox"
    d.mkdir(parents=True, exist_ok=True)
    return d


def config_path() -> Path:
    return config_dir() / "config.json"


def load_config() -> dict:
    p = config_path()
    if p.is_file():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_config(cfg: dict) -> None:
    # atomic tmp → replace: a crash mid-write must never truncate the config
    # (it holds every registered library path)
    with _CONFIG_LOCK:
        path = config_path()
        # the directory can be missing on a first run — or after someone cleared
        # it while the app was up; a write must not depend on who made it
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        os.replace(tmp, path)


@contextmanager
def config_transaction():
    """Load → mutate → save as ONE serialized step; concurrent writers can't
    clobber each other's fields."""
    with _CONFIG_LOCK:
        cfg = load_config()
        yield cfg
        save_config(cfg)


def default_library_path() -> Path:
    return config_dir() / "library"
