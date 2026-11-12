"""FastAPI application: the loopback guard, the library API, /health, and (when a
built frontend exists) the static UI served at /app/. Single origin, no CORS.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import sys
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .config_store import UnreadableConfig
from .legacy_config import move_site_data_into
from .library.service import Library
from .library.vault import UnreadableDocument, VaultGone, is_vault
from .passes import stop_event
from .routers import downloads as downloads_router
from .routers import imports as imports_router
from .routers import library as library_router
from .routers import metadata as metadata_router
from .routers import pages as pages_router
from .routers import recipes as recipes_router
from .routers import settings as settings_router
from .security import ApiGuard
from .settings import get_settings, resolve_library_path
from .vault_watch import VaultWatch

log = logging.getLogger("longbox")

CLOSE_PATH = "/api/close"
# How long a close waits for the writes on their way. A pass that will not stop
# by then is cut off with the process — and the session is left open for it.
CLOSE_WAIT_S = 5.0
_READS = {"GET", "HEAD", "OPTIONS"}


class _Writes:
    """The requests that may write, in flight: a close waits for them, since one
    may hold a write the index has not taken yet."""

    def __init__(self) -> None:
        self._n = 0
        self._cv = threading.Condition()

    def enter(self) -> None:
        with self._cv:
            self._n += 1

    def leave(self) -> None:
        with self._cv:
            self._n -= 1
            self._cv.notify_all()

    def drained(self, timeout: float) -> bool:
        with self._cv:
            return self._cv.wait_for(lambda: self._n == 0, timeout)

_REPO_ROOT = Path(__file__).resolve().parents[2]
# A frozen sidecar carries the built UI inside its own bundle; a source checkout
# serves whatever the last `npm run build` produced.
_BUNDLE = getattr(sys, "_MEIPASS", None)
_FRONTEND_DIST = Path(_BUNDLE) / "frontend_dist" if _BUNDLE else _REPO_ROOT / "frontend" / "dist"


class _AppFiles(StaticFiles):
    """The built UI, with the ONE cache rule it needs.

    Asset names carry a content hash, so they are safe to keep forever. The
    entry document does not — and served without a Cache-Control it falls to
    Chromium's heuristic cache, which happily pins an open window to the bundle
    it saw last. A rebuilt frontend then changes nothing on screen, which is a
    very expensive way to learn about HTTP defaults."""

    def file_response(self, *args, **kwargs):  # type: ignore[override]
        resp = super().file_response(*args, **kwargs)
        path = str(getattr(resp, "path", ""))
        immutable = "/assets/" in path.replace("\\", "/")
        resp.headers["Cache-Control"] = (
            "public, max-age=31536000, immutable" if immutable else "no-cache")
        return resp


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
    watch: VaultWatch | None = None
    if provided is not None:
        lib = provided
    else:
        root = resolve_library_path()
        # Never a vault made to stand in: the launcher creates vaults, on purpose
        # (design/vault-home.md). The shell checked this one answers before it
        # started the sidecar.
        if root is None:
            raise RuntimeError("no vault named — the app's launch window names one; "
                               "run on its own, the sidecar needs LONGBOX_LIBRARY_PATH")
        if not is_vault(root):
            raise RuntimeError(f"{root} is not a vault — longbox creates vaults from its launch window")
        # Serve from the index immediately; the vault is verified right after, so
        # a big (or networked) library never stands between launch and a window.
        lib = Library(root, defer_sync=True)
        log.info("library ready at %s (%d titles indexed)", root, lib.count())
        move_site_data_into(lib)
        lib.sync_in_background()
        watch = VaultWatch(lib)
        watch.start()
    app.state.library = lib
    app.state.vault_watch = watch
    try:
        yield
    finally:
        if watch is not None:
            watch.close()
            lib.close()


def create_app(library: Library | None = None) -> FastAPI:
    app = FastAPI(title="longbox", lifespan=lifespan)
    app.state.provided_library = library
    app.state.closing = False
    writes = _Writes()
    app.add_middleware(ApiGuard)
    app.include_router(library_router.router)
    app.include_router(settings_router.router)
    app.include_router(recipes_router.router)
    app.include_router(downloads_router.router)
    app.include_router(metadata_router.router)
    app.include_router(imports_router.router)
    app.include_router(pages_router.router)

    @app.exception_handler(UnreadableDocument)
    @app.exception_handler(UnreadableConfig)
    def unreadable(_request: Request, exc: RuntimeError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(OSError)
    def vault_io_failed(request: Request, exc: OSError) -> JSONResponse:
        """A read or write that failed may be the vault going: the watch asks at
        once instead of at its next tick."""
        watch: VaultWatch | None = getattr(request.app.state, "vault_watch", None)
        if watch is not None:
            watch.poke()
        if isinstance(exc, VaultGone):
            return JSONResponse(status_code=503, content={"detail": str(exc), "vault": "lost"})
        return JSONResponse(status_code=500, content={"detail": str(exc)})

    @app.middleware("http")
    async def vault_gate(request: Request, call_next):
        """While the vault does not answer, nothing waits on it and nothing is
        written anywhere: every API request is refused at once. So is every one
        after the app began to close."""
        if not request.url.path.startswith("/api"):
            return await call_next(request)
        writing = request.method not in _READS and request.url.path != CLOSE_PATH
        if writing:
            writes.enter()  # before the check: a close that begins now waits for this one
        try:
            if request.app.state.closing:
                return JSONResponse(status_code=503, content={"detail": "the app is closing"})
            watch: VaultWatch | None = getattr(request.app.state, "vault_watch", None)
            if watch is not None and watch.state != "ok":
                return JSONResponse(status_code=503, content={"detail": "the vault is unreachable",
                                                              "vault": watch.state})
            return await call_next(request)
        finally:
            if writing:
                writes.leave()

    @app.post(CLOSE_PATH)
    def close(request: Request) -> dict:
        """The shell's last word before it stops the sidecar, which on Windows
        is a kill that runs no shutdown: the passes stop, the writes on their
        way land, and the library closes — its session marked closed only when
        nothing was cut off (design/state-model.md)."""
        request.app.state.closing = True
        lib: Library = request.app.state.library
        stop_event().set()  # an import, a check, any pass, at its next step
        lib.closing()
        settled = writes.drained(CLOSE_WAIT_S)
        watch: VaultWatch | None = request.app.state.vault_watch
        if watch is not None:
            watch.close()
        lib.close(settled=settled)
        return {"settled": settled}

    @app.get("/health")
    def health() -> dict:
        # Open (pre-guard). Echoes sha256(token) so the shell can confirm it reached
        # its own sidecar without exposing the secret. `vault` is what the shell
        # watches: `lost` covers the window, `back` restarts the app into it.
        # Nothing here touches the vault: the shell asks every few seconds.
        token = get_settings().auth_token
        watch: VaultWatch | None = app.state.vault_watch
        return {
            "status": "ok",
            "sha256": hashlib.sha256(token.encode()).hexdigest() if token else None,
            "vault": watch.state if watch is not None else "ok",
            "since": watch.since if watch is not None else "",
        }

    if _FRONTEND_DIST.is_dir():
        app.mount("/app", _AppFiles(directory=_FRONTEND_DIST, html=True), name="app")

    return app


app = create_app()
