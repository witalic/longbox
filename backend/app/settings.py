"""Runtime configuration.

The library path is resolved in priority order: an explicit LONGBOX_LIBRARY_PATH
(env/tests override) → the saved config file → a default under the OS config dir.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from .config_store import default_library_path, load_config


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LONGBOX_", env_file=".env", extra="ignore")

    # Per-launch shared secret guarding /api. None => guard is a pass-through (dev/tests).
    auth_token: str | None = None
    # Explicit library override (env / tests). When unset, config/default is used.
    library_path: Path | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()


def resolve_library_path() -> Path:
    override = get_settings().library_path
    if override is not None:
        return override
    saved = load_config().get("library_path")
    if saved:
        return Path(saved)
    return default_library_path()
