from __future__ import annotations

import os
import time
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient
from jose import jwt

from evoeventmem.api.app import create_app

_TENANT_HEADER = "X-Tenant-Id"
_USER_HEADER = "X-User-Id"
_API_KEY = "test-secret-key-123"


def _headers(*, token: str = _API_KEY, tenant: str = "t", user: str = "u") -> dict[str, str]:
    return {
        _TENANT_HEADER: tenant,
        _USER_HEADER: user,
        "Authorization": f"Bearer {token}",
    }


def _write_payload() -> dict:
    return {
        "tenant_id": "t",
        "user_id": "u",
        "content": "test memory",
        "evidence": [{"source_type": "test", "source_id": "auth:1"}],
    }


@pytest.fixture(autouse=True)
def _set_api_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EEM_API_KEYS", _API_KEY)


def test_401_when_no_authorization_header() -> None:
    client = TestClient(create_app())
    response = client.post(
        "/v1/memories",
        headers={_TENANT_HEADER: "t", _USER_HEADER: "u"},
        json=_write_payload(),
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "missing bearer token"


def test_403_when_invalid_token() -> None:
    client = TestClient(create_app())
    response = client.post(
        "/v1/memories",
        headers=_headers(token="wrong-token"),
        json=_write_payload(),
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "invalid bearer token"


def test_200_when_valid_token() -> None:
    client = TestClient(create_app())
    response = client.post(
        "/v1/memories",
        headers=_headers(),
        json=_write_payload(),
    )
    assert response.status_code == 200
    assert response.json()["content"] == "test memory"


def test_health_works_without_auth() -> None:
    client = TestClient(create_app())
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_readiness_works_without_auth() -> None:
    client = TestClient(create_app())
    response = client.get("/readiness")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"


def test_metrics_works_without_auth() -> None:
    client = TestClient(create_app())
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "evoeventmem_http_requests_total" in response.text


def test_search_requires_auth() -> None:
    client = TestClient(create_app())
    response = client.get(
        "/v1/memories/search",
        headers={_TENANT_HEADER: "t", _USER_HEADER: "u"},
        params={"q": "test"},
    )
    assert response.status_code == 401


def test_explain_requires_auth() -> None:
    client = TestClient(create_app())
    response = client.get(
        "/v1/memories/00000000-0000-0000-0000-000000000000/explain",
        headers={_TENANT_HEADER: "t", _USER_HEADER: "u"},
    )
    assert response.status_code == 401


def test_forget_requires_auth() -> None:
    client = TestClient(create_app())
    response = client.post(
        "/v1/memories/00000000-0000-0000-0000-000000000000/forget",
        headers={_TENANT_HEADER: "t", _USER_HEADER: "u"},
    )
    assert response.status_code == 401


def test_feedback_requires_auth() -> None:
    client = TestClient(create_app())
    response = client.post(
        "/v1/memories/00000000-0000-0000-0000-000000000000/feedback",
        headers={_TENANT_HEADER: "t", _USER_HEADER: "u"},
        json={"outcome": "useful"},
    )
    assert response.status_code == 401


def test_multiple_valid_keys() -> None:
    os.environ["EEM_API_KEYS"] = "key-a,key-b,key-c"
    try:
        client = TestClient(create_app())
        resp_a = client.post(
            "/v1/memories",
            headers=_headers(token="key-a"),
            json=_write_payload(),
        )
        resp_b = client.post(
            "/v1/memories",
            headers=_headers(token="key-b"),
            json=_write_payload(),
        )
        assert resp_a.status_code == 200
        assert resp_b.status_code == 200
    finally:
        os.environ["EEM_API_KEYS"] = _API_KEY


# ---------------------------------------------------------------------------
# OAuth2/OIDC JWT verification tests
# ---------------------------------------------------------------------------

_JWKS_URL = "https://auth.example.com/.well-known/jwks.json"
_ISSUER = "https://auth.example.com"
_AUDIENCE = "evoeventmem-api"


def _make_rsa_key_pair() -> tuple[object, str]:
    from cryptography.hazmat.primitives.asymmetric import rsa

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()
    from jose.utils import long_to_base64

    numbers = public_key.public_numbers()
    jwk = {
        "kty": "RSA",
        "kid": "test-key-1",
        "use": "sig",
        "alg": "RS256",
        "n": long_to_base64(numbers.n).decode(),
        "e": long_to_base64(numbers.e).decode(),
    }
    return private_key, jwk


_PRIVATE_KEY, _TEST_JWK = _make_rsa_key_pair()
_JWKS = {"keys": [_TEST_JWK]}


def _make_jwt(
    *,
    sub: str = "user-1",
    iss: str = _ISSUER,
    aud: str = _AUDIENCE,
    exp: int | None = None,
) -> str:
    from cryptography.hazmat.primitives import serialization

    payload: dict[str, object] = {"sub": sub, "iss": iss, "aud": aud}
    if exp is not None:
        payload["exp"] = exp
    else:
        payload["exp"] = int(time.time()) + 3600
    private_pem = _PRIVATE_KEY.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return jwt.encode(
        payload, private_pem, algorithm="RS256", headers={"kid": "test-key-1"}
    )


def _oauth2_settings_env() -> dict[str, str]:
    return {
        "EEM_AUTH_MODE": "oauth2",
        "EEM_OAUTH2_JWKS_URL": _JWKS_URL,
        "EEM_OAUTH2_ISSUER": _ISSUER,
        "EEM_OAUTH2_AUDIENCE": _AUDIENCE,
    }


class _FakeAsyncClient:
    def __init__(self, *, jwks: dict | None = None) -> None:
        self._jwks = jwks

    async def __aenter__(self) -> _FakeAsyncClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        pass

    async def get(self, url: str, **kwargs: object) -> _FakeResponse:
        if self._jwks is None:
            raise httpx.ConnectError("not reachable")
        return _FakeResponse(self._jwks)


class _FakeResponse:
    def __init__(self, jwks: dict) -> None:
        self._jwks = jwks

    def json(self) -> dict:
        return self._jwks

    def raise_for_status(self) -> None:
        pass


def _patch_jwks(jwks: dict | None = None) -> object:
    fake = _FakeAsyncClient(jwks=jwks)
    return patch(
        "evoeventmem.api.auth.httpx.AsyncClient",
        return_value=fake,
    )


def test_oauth2_rejects_missing_bearer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = _oauth2_settings_env()
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("EEM_API_KEYS", raising=False)

    client = TestClient(create_app())
    response = client.post(
        "/v1/memories",
        headers={_TENANT_HEADER: "t", _USER_HEADER: "u"},
        json=_write_payload(),
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "missing bearer token"


def test_oauth2_rejects_invalid_jwt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = _oauth2_settings_env()
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("EEM_API_KEYS", raising=False)

    with _patch_jwks(_JWKS):
        client = TestClient(create_app())
        response = client.post(
            "/v1/memories",
            headers=_headers(token="not-a-jwt"),
            json=_write_payload(),
        )
    assert response.status_code == 401
    assert response.json()["detail"] == "malformed token"


def test_oauth2_accepts_valid_jwt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = _oauth2_settings_env()
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("EEM_API_KEYS", raising=False)

    token = _make_jwt()

    with _patch_jwks(_JWKS):
        client = TestClient(create_app())
        response = client.post(
            "/v1/memories",
            headers=_headers(token=token),
            json=_write_payload(),
        )
    assert response.status_code == 200
    assert response.json()["content"] == "test memory"


def test_oauth2_rejects_expired_jwt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = _oauth2_settings_env()
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("EEM_API_KEYS", raising=False)

    token = _make_jwt(exp=int(time.time()) - 3600)

    with _patch_jwks(_JWKS):
        client = TestClient(create_app())
        response = client.post(
            "/v1/memories",
            headers=_headers(token=token),
            json=_write_payload(),
        )
    assert response.status_code == 401


def test_oauth2_rejects_wrong_issuer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = _oauth2_settings_env()
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("EEM_API_KEYS", raising=False)

    token = _make_jwt(iss="https://wrong-issuer.example.com")

    with _patch_jwks(_JWKS):
        client = TestClient(create_app())
        response = client.post(
            "/v1/memories",
            headers=_headers(token=token),
            json=_write_payload(),
        )
    assert response.status_code == 401


def test_oauth2_rejects_wrong_audience(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = _oauth2_settings_env()
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("EEM_API_KEYS", raising=False)

    token = _make_jwt(aud="wrong-audience")

    with _patch_jwks(_JWKS):
        client = TestClient(create_app())
        response = client.post(
            "/v1/memories",
            headers=_headers(token=token),
            json=_write_payload(),
        )
    assert response.status_code == 401


def test_oauth2_health_works_without_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = _oauth2_settings_env()
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("EEM_API_KEYS", raising=False)

    client = TestClient(create_app())
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
