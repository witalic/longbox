"""Runtime configuration.

The vault the sidecar serves is NAMED, never assumed: the shell passes the one
it opened as LONGBOX_LIBRARY_PATH; the sidecar run on its own (`run.py
--backend`) falls back to the vault the config says was opened last. With
neither there is no vault, and nothing is created to stand in for one.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from .config_store import load_config


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LONGBOX_", env_file=".env", extra="ignore")

    # Per-launch shared secret guarding /api. None => guard is a pass-through (dev/tests).
    auth_token: str | None = None
    # The vault to serve (the shell / env / tests).
    library_path: Path | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()


def resolve_library_path() -> Path | None:
    named = get_settings().library_path
    if named is not None:
        return named
    saved = load_config().get("library_path")
    return Path(saved) if saved else None
