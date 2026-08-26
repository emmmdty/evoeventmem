from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

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
