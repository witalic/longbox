"""FastAPI application: the loopback guard, the library API, /health, and (when a
built frontend exists) the static UI served at /app/. Single origin, no CORS.
"""
from __future__ import annotations

import hashlib
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .library.service import Library
from .routers import downloads as downloads_router
from .routers import library as library_router
from .routers import recipes as recipes_router
from .routers import settings as settings_router
from .scraper.recipes import RecipeStore
from .security import ApiGuard
from .settings import get_settings, resolve_library_path

log = logging.getLogger("longbox")

_REPO_ROOT = Path(__file__).resolve().parents[2]
_FRONTEND_DIST = _REPO_ROOT / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    provided: Library | None = getattr(app.state, "provided_library", None)
    owns = provided is None
    if provided is not None:
        lib = provided
    else:
        root = resolve_library_path()
        lib = Library(root)  # scans the vault on disk and builds the index
        log.info("library ready at %s (%d titles)", root, lib.count())
    app.state.library = lib
    app.state.recipes = RecipeStore()
    try:
        yield
    finally:
        if owns:
            lib.close()


def create_app(library: Library | None = None) -> FastAPI:
    app = FastAPI(title="longbox", lifespan=lifespan)
    app.state.provided_library = library
    app.add_middleware(ApiGuard)
    app.include_router(library_router.router)
    app.include_router(settings_router.router)
    app.include_router(recipes_router.router)
    app.include_router(downloads_router.router)

    @app.get("/health")
    def health() -> dict:
        # Open (pre-guard). Echoes sha256(token) so the shell can confirm it reached
        # its own sidecar without exposing the secret.
        token = get_settings().auth_token
        return {
            "status": "ok",
            "sha256": hashlib.sha256(token.encode()).hexdigest() if token else None,
            "titles": app.state.library.count(),
        }

    if _FRONTEND_DIST.is_dir():
        app.mount("/app", StaticFiles(directory=_FRONTEND_DIST, html=True), name="app")

    return app


app = create_app()
