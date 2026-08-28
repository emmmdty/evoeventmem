from __future__ import annotations

import logging
import os
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from jose import JWTError, jwt
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from evoeventmem.infra.config import Settings

logger = logging.getLogger("evoeventmem")

EXEMPT_PATHS: frozenset[str] = frozenset(
    {"/health", "/readiness", "/metrics", "/docs", "/openapi.json"}
)

_STATIC_TOKENS_KEY = "auth_valid_tokens"


class AuthMiddleware(BaseHTTPMiddleware):
    """Bearer token verification middleware.

    Supports two modes controlled by ``EEM_AUTH_MODE``:

    **static** (default): Reads valid tokens from ``EEM_API_KEYS``
    (comma-separated).  When the variable is unset or empty the middleware
    is a no-op, allowing unauthenticated development/testing usage.

    **oauth2**: Fetches signing keys from a JWKS endpoint and verifies
    JWTs per RS256 algorithm, issuer, audience, and expiry claims.
    """

    def __init__(self, app: object) -> None:  # noqa: ANN401
        super().__init__(app)  # type: ignore[arg-type]
        raw = os.environ.get("EEM_API_KEYS", "")
        self._valid_tokens: frozenset[str] = (
            frozenset(k.strip() for k in raw.split(",") if k.strip())
            if raw.strip()
            else frozenset()
        )
        self._jwks_cache: dict[str, Any] | None = None

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if request.url.path in EXEMPT_PATHS:
            return await call_next(request)

        settings: Settings = request.app.state.settings

        if settings.auth_mode == "oauth2":
            return await self._dispatch_oauth2(request, call_next, settings)
        return await self._dispatch_static(request, call_next)

    async def _dispatch_static(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if not self._valid_tokens:
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

    async def _dispatch_oauth2(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
        settings: Settings,
    ) -> Response:
        auth_header = request.headers.get("authorization", "")
        if not auth_header.startswith("Bearer "):
            return JSONResponse(
                status_code=401, content={"detail": "missing bearer token"}
            )

        token = auth_header[len("Bearer ") :]
        try:
            claims = await self._verify_jwt(token, settings)
        except _AuthError as exc:
            return JSONResponse(
                status_code=401, content={"detail": str(exc)}
            )

        request.state.auth_claims = claims
        return await call_next(request)

    async def _get_jwks(self, url: str) -> dict[str, Any]:
        if self._jwks_cache is not None:
            return self._jwks_cache
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, timeout=10.0)
            resp.raise_for_status()
            self._jwks_cache = resp.json()
        return self._jwks_cache

    async def _verify_jwt(
        self, token: str, settings: Settings
    ) -> dict[str, Any]:
        assert settings.oauth2_jwks_url is not None
        jwks = await self._get_jwks(settings.oauth2_jwks_url)
        try:
            unverified_header = jwt.get_unverified_header(token)
        except JWTError as exc:
            raise _AuthError("malformed token") from exc

        kid = unverified_header.get("kid")
        key = None
        for jwk in jwks.get("keys", []):
            if jwk.get("kid") == kid:
                key = jwk
                break
        if key is None:
            raise _AuthError("no matching signing key")

        algorithms = unverified_header.get("alg")
        if algorithms != "RS256":
            raise _AuthError(f"unsupported algorithm: {algorithms}")

        options: dict[str, bool] = {
            "verify_iss": settings.oauth2_issuer is not None,
            "verify_aud": settings.oauth2_audience is not None,
        }

        try:
            claims = jwt.decode(
                token,
                key,
                algorithms=["RS256"],
                issuer=settings.oauth2_issuer,
                audience=settings.oauth2_audience,
                options=options,
            )
        except JWTError as exc:
            raise _AuthError("invalid token") from exc

        result: dict[str, Any] = claims
        return result


class _AuthError(Exception):
    pass
