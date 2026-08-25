"""Per-domain recipe storage. Recipes live in the app config dir (not the library —
they are app-level knowledge about sites), one JSON file per domain, versioned.
"""
from __future__ import annotations

import os
import re
import threading
from pathlib import Path

from ..config_store import config_dir
from .models import Recipe

_SAFE = re.compile(r"[^a-z0-9.-]+")


def _safe_domain(domain: str) -> str:
    return _SAFE.sub("_", domain.strip().lower()).strip("._-") or "unknown"


class RecipeStore:
    def __init__(self, root: Path | None = None) -> None:
        self.dir = (root or config_dir()) / "recipes"
        self.dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()  # save() is read-bump-write

    def _path(self, domain: str) -> Path:
        return self.dir / f"{_safe_domain(domain)}.json"

    def get(self, domain: str) -> Recipe | None:
        p = self._path(domain)
        if not p.is_file():
            return None
        recipe = Recipe.model_validate_json(p.read_text(encoding="utf-8"))
        # A pre-candidate (prototype-era) file parses but can extract nothing —
        # treat it as unlearned rather than showing a recipe that silently no-ops.
        usable = (recipe.chapters is not None or bool(recipe.hidden)
                  or any(f.candidates for f in recipe.fields.values()))
        return recipe if usable else None

    def list_domains(self) -> list[str]:
        return sorted(Recipe.model_validate_json(p.read_text(encoding="utf-8")).domain
                      for p in self.dir.glob("*.json"))

    def save(self, recipe: Recipe) -> Recipe:
        """Persist a recipe, bumping the version past any existing one for that domain."""
        with self._lock:
            existing = self.get(recipe.domain)
            recipe.version = (existing.version + 1) if existing else max(recipe.version, 1)
            path = self._path(recipe.domain)
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(recipe.model_dump_json(indent=2), encoding="utf-8")
            os.replace(tmp, path)  # a crash mid-write must not corrupt the recipe
            return recipe

    def delete(self, domain: str) -> bool:
        """Forget everything learned about a domain."""
        p = self._path(domain)
        if not p.is_file():
            return False
        p.unlink()
        return True
