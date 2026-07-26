"""Loopback guard for the /api surface.

Mirrors the shell contract: a per-launch secret delivered as the `lb_auth` cookie
(local mode) or an `Authorization: Bearer` token (remote mode). Constant-time
compare, and non-loopback Host is rejected in local mode (anti DNS-rebinding).
When no token is configured the guard passes everything through (dev/tests).
"""
from __future__ import annotations

import hmac

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from .settings import get_settings

_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}


def _host_is_loopback(request: Request) -> bool:
    host = request.headers.get("host", "")
    hostname = host.rsplit(":", 1)[0] if host else ""
    return hostname in _LOOPBACK_HOSTS


class ApiGuard(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if not request.url.path.startswith("/api"):
            return await call_next(request)

        token = get_settings().auth_token
        if token is None:  # dev / tests — pass-through
            return await call_next(request)

        if not _host_is_loopback(request):
            return JSONResponse({"detail": "forbidden host"}, status_code=403)

        supplied = request.cookies.get("lb_auth")
        if supplied is None:
            auth = request.headers.get("authorization", "")
            if auth.startswith("Bearer "):
                supplied = auth[7:]

        if supplied is None or not hmac.compare_digest(supplied, token):
            return JSONResponse({"detail": "unauthorized"}, status_code=401)

        return await call_next(request)
