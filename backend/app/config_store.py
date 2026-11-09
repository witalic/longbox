"""What THIS computer keeps: where the vaults are, and nothing else
(design/vault-home.md). A small JSON file in the app's own folder:

    {"libraries": [...], "library_path": "<the vault opened last>"}

Everything the app keeps besides that lives in the vault it serves. The shell
writes the file when a vault is opened or created; the sidecar reads it, forgets
a vault on request, and hands what an older build left here to the vault.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import uuid
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
    return d  # made by the first write (save_config), never by a read


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
    # (it holds every registered library path); the scratch name is this
    # write's own — the shell writes the same file
    with _CONFIG_LOCK:
        path = config_path()
        # the directory can be missing on a first run — or after someone cleared
        # it while the app was up; a write must not depend on who made it
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{uuid.uuid4().hex[:8]}.tmp")
        tmp.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        os.replace(tmp, path)


class UnreadableConfig(RuntimeError):
    """config.json is there but cannot be read: it is the only record of where
    the vaults are, so nothing is written over it."""


@contextmanager
def config_transaction():
    """Load → mutate → save as ONE serialized step; concurrent writers can't
    clobber each other's fields. A config that exists but cannot be read is
    refused, never replaced by what this write alone knows."""
    with _CONFIG_LOCK:
        p = config_path()
        try:
            cfg = json.loads(p.read_text(encoding="utf-8"))
        except FileNotFoundError:
            cfg = {}
        except (ValueError, OSError) as e:
            raise UnreadableConfig(f"{p} cannot be read — fix or move it aside") from e
        if not isinstance(cfg, dict):
            raise UnreadableConfig(f"{p} cannot be read — fix or move it aside")
        yield cfg
        save_config(cfg)
