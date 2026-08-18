"""FastAPI application: the loopback guard, the library API, /health, and (when a
built frontend exists) the static UI served at /app/. Single origin, no CORS.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import sys
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
# A frozen sidecar carries the built UI inside its own bundle; a source checkout
# serves whatever the last `npm run build` produced.
_BUNDLE = getattr(sys, "_MEIPASS", None)
_FRONTEND_DIST = Path(_BUNDLE) / "frontend_dist" if _BUNDLE else _REPO_ROOT / "frontend" / "dist"


def _quiet_client_resets(loop: asyncio.AbstractEventLoop) -> None:
    """Windows proactor noise: when the shell window closes/reloads, Chromium
    drops its keep-alive sockets mid-flight and the transport's own close
    callback raises WinError 10054. It is not an application error — swallow
    exactly that; everything else goes to the default handler."""
    def handler(loop: asyncio.AbstractEventLoop, context: dict) -> None:
        exc = context.get("exception")
        if isinstance(exc, ConnectionResetError) and getattr(exc, "winerror", None) == 10054:
            return
        loop.default_exception_handler(context)
    loop.set_exception_handler(handler)


@asynccontextmanager
async def lifespan(app: FastAPI):
    _quiet_client_resets(asyncio.get_running_loop())
    provided: Library | None = getattr(app.state, "provided_library", None)
    owns = provided is None
    if provided is not None:
        lib = provided
    else:
        root = resolve_library_path()
        # Serve from the index immediately; the vault is verified right after, so
        # a big (or networked) library never stands between launch and a window.
        lib = Library(root, defer_sync=True)
        log.info("library ready at %s (%d titles indexed)", root, lib.count())
        lib.sync_in_background()
    app.state.library = lib
    app.state.recipes = RecipeStore()
    try:
        yield
    finally:
        # close whatever library is live NOW — a library switch replaces it, and
        # closing the original would leave the active one's SQLite handle open
        if owns:
            current = getattr(app.state, "library", lib)
            current.close()
            if current is not lib:
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
