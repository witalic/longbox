"""Site knowledge an older build kept in the app config, moved into the vault.

Recipes, bookmarks and source groups used to live in the per-user config dir, so
a library carried to another machine arrived without them. They live in the
vault now (`recipes.json`, `sources.json`). What an older build left behind moves
ONCE, into the library that is open when this build first starts, and is then
removed from the config — which keeps only what belongs to THIS machine.
"""
from __future__ import annotations

import json
import logging

from .config_store import config_dir, config_path, config_transaction
from .library.service import Library
from .scraper.models import Recipe
from .settings import get_settings

log = logging.getLogger("longbox")

_KEYS = ("sources", "source_groups", "hidden_sources")


def _saved_config() -> dict | None:
    """The config as saved ({} when there is none) — or None when it exists but
    cannot be read. Then the library just opened is a fallback, not the owner's,
    and the config must not be written: it is the only record of where the
    owner's libraries are."""
    try:
        raw = json.loads(config_path().read_bytes().decode("utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        return None
    return raw if isinstance(raw, dict) else None


def move_site_data_into(lib: Library) -> bool:
    """Returns whether anything moved. Never raises: a move that fails leaves the
    config as it was, and the next start tries again."""
    try:
        if get_settings().library_path is not None:
            return False  # an override is open, not the library the config describes
        cfg = _saved_config()
        if cfg is None:
            log.warning("the app config cannot be read; recipes and sources stay where they are")
            return False
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
            return False

        if recipes:
            lib.recipes.adopt(recipes)
        if keys:
            prefs = cfg.get("sources")
            lib.source_prefs.adopt(prefs if isinstance(prefs, dict) else {},
                                   cfg.get("source_groups") or [], cfg.get("hidden_sources") or [])
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
        log.info("moved %d recipe(s) and the source layer into %s", len(recipes), lib.root)
        return True
    except Exception:  # noqa: BLE001 — a failed move must never keep the app from starting
        log.exception("moving site data into the vault failed; the app config keeps it")
        return False
