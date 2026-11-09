"""What an older build kept in the app's folder, moved into the vault.

The app's folder on this computer keeps only where the vaults are
(design/vault-home.md); recipes, bookmarks, source groups, the homepage,
transfers a quit cut short, paused imports and import reports all live in the
vault now. What an older build left in the config moves ONCE, into the vault the
config says is being opened — the first one this build opens — and leaves the
config only once the vault holds it.

The shell moves the rest before the sidecar starts: the browser's profile, the
ad blocker, partial transfers, the old caches. The window's preferences an older
build kept in the browser's localStorage it reads out of the moved profile and
hands over in `.longbox/legacy-window.json`, which is taken in here.
"""
from __future__ import annotations

import json
import logging
import os

from .config_store import config_dir, config_path, config_transaction
from .library.service import Library
from .library.vault import APP_DIR
from .scraper.models import Recipe

log = logging.getLogger("longbox")

_SITE_KEYS = ("sources", "source_groups", "hidden_sources")
_STATE_KEYS = ("interrupted_downloads", "import_runs", "paused_imports")
_KEYS = (*_SITE_KEYS, "homepage", *_STATE_KEYS)
HANDED_OVER = f"{APP_DIR}/legacy-window.json"


def _saved_config() -> dict | None:
    """The config as saved ({} when there is none) — or None when it exists but
    cannot be read. Then nothing may be written to it: it is the only record of
    where the owner's libraries are."""
    try:
        raw = json.loads(config_path().read_bytes().decode("utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        return None
    return raw if isinstance(raw, dict) else None


def _same(a: object, b: object) -> bool:
    return isinstance(a, str) and bool(a) and \
        os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(str(b)))


def _paused_here(rows: object, lib: Library) -> list[dict]:
    """An older build kept every library's paused imports together, each naming
    its library. Only this vault's come along; another vault's was a shortcut —
    running that import again skips what it stored."""
    out = []
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, dict) and _same(row.get("library"), lib.root):
            out.append({k: v for k, v in row.items() if k != "library"})
    return out


def _transfers_here(rows: object, lib: Library) -> list:
    """The shell moved the transfers' partial files into the vault before this
    ran; a record still naming the old folder is pointed at where the file is."""
    old = os.path.normcase(os.path.abspath(config_dir() / "partial-downloads"))
    new = lib.vault.root / APP_DIR / "partial-downloads"
    out = []
    for row in rows if isinstance(rows, list) else []:
        resume = row.get("resume") if isinstance(row, dict) else None
        at = resume.get("path") if isinstance(resume, dict) else None
        if isinstance(at, str) and at and os.path.normcase(os.path.dirname(os.path.abspath(at))) == old:
            row = {**row, "resume": {**resume, "path": str(new / os.path.basename(at))}}
        out.append(row)
    return out


def _take_window_prefs(lib: Library) -> bool:
    """The window's preferences the shell handed over; the vault's own win."""
    path = lib.vault.root / HANDED_OVER
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return False
    except (OSError, ValueError):
        log.warning("left %s in place: it cannot be read", path)
        return False
    prefs = {k: v for k, v in raw.items() if isinstance(k, str) and isinstance(v, str)} if isinstance(raw, dict) else {}
    if prefs:
        lib.settings.adopt(window=prefs)
    path.unlink(missing_ok=True)
    return True


def move_site_data_into(lib: Library) -> bool:
    """Returns whether anything moved. Never raises: a move that fails leaves the
    config as it was, and the next start tries again. Only a sidecar the shell
    started moves anything: run on its own it reads the OS's default config
    folder, which may be an installed build's."""
    if not os.environ.get("LONGBOX_CONFIG_DIR"):
        return False
    try:
        took = _take_window_prefs(lib)
        cfg = _saved_config()
        if cfg is None:
            log.warning("the app config cannot be read; what it holds stays where it is")
            return took
        if not _same(cfg.get("library_path"), lib.root):
            return took  # not the vault the config is opening (a test, another vault)
        recipe_dir = config_dir() / "recipes"
        moved, recipes = [], []
        for p in sorted(recipe_dir.glob("*.json")) if recipe_dir.is_dir() else []:
            try:
                recipes.append(Recipe.model_validate_json(p.read_bytes()))
                moved.append(p)
            except (OSError, ValueError):
                log.warning("left %s in place: not a recipe this build can read", p)
        keys = [k for k in _KEYS if k in cfg]
        if not moved and not keys:
            return took

        if recipes:
            lib.recipes.adopt(recipes)
        if any(k in cfg for k in _SITE_KEYS):
            prefs = cfg.get("sources")
            lib.source_prefs.adopt(prefs if isinstance(prefs, dict) else {},
                                   cfg.get("source_groups") or [], cfg.get("hidden_sources") or [])
        if isinstance(cfg.get("homepage"), str):
            lib.settings.adopt(homepage=cfg["homepage"])
        here = {"paused_imports": _paused_here, "interrupted_downloads": _transfers_here}
        for key in _STATE_KEYS:
            rows = here[key](cfg.get(key), lib) if key in here else cfg.get(key)
            if isinstance(rows, list) and rows:
                with lib.state.edit(key) as kept:
                    if not kept:  # the vault's own record wins
                        kept[:] = rows
        # only now that the vault holds them may the config forget them
        with config_transaction() as live:
            for k in _KEYS:
                live.pop(k, None)
        for p in moved:
            p.unlink(missing_ok=True)
        try:
            recipe_dir.rmdir()
        except OSError:
            pass  # absent, or holding what could not be moved — either way, leave it
        log.info("moved %d recipe(s) and %s from the app config into %s",
                 len(recipes), ", ".join(keys) or "nothing else", lib.root)
        return True
    except Exception:  # noqa: BLE001 — a failed move must never keep the app from starting
        log.exception("moving the app config's data into the vault failed; the config keeps it")
        return False
