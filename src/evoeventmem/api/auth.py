from __future__ import annotations

import os
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response


class AuthMiddleware(BaseHTTPMiddleware):
    """Bearer token verification middleware.

    Reads valid tokens from the ``EEM_API_KEYS`` environment variable
    (comma-separated).  When the variable is unset or empty the middleware
    is a no-op, allowing unauthenticated development/testing usage.

    Exempt paths (``/health``, ``/readiness``, ``/metrics``, ``/docs``,
    ``/openapi.json``) are always allowed through without a token.
    """

    EXEMPT_PATHS: frozenset[str] = frozenset(
        {"/health", "/readiness", "/metrics", "/docs", "/openapi.json"}
    )

    def __init__(self, app: object) -> None:  # noqa: ANN401 – BaseHTTPMiddleware sig
        super().__init__(app)  # type: ignore[arg-type]
        raw = os.environ.get("EEM_API_KEYS", "")
        self._valid_tokens: frozenset[str] = (
            frozenset(k.strip() for k in raw.split(",") if k.strip())
            if raw.strip()
            else frozenset()
        )

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if not self._valid_tokens:
            return await call_next(request)

        if request.url.path in self.EXEMPT_PATHS:
            return await call_next(request)

        auth_header = request.headers.get("authorization", "")
        if not auth_header.startswith("Bearer "):
            return JSONResponse(
                status_code=401, content={"detail": "missing bearer token"}
            )

        token = auth_header[len("Bearer ") :]
        if token not in self._valid_tokens:
            return JSONResponse(
                status_code=403, content={"detail": "invalid bearer token"}
            )

        return await call_next(request)
