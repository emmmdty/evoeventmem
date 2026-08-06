from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from evoeventmem.api.app import create_app
from evoeventmem.core.ports import RequestScope, ScopeMismatch

TENANT_HEADER = "X-Tenant-Id"
USER_HEADER = "X-User-Id"
SESSION_HEADER = "X-Session-Id"


def _write_payload(**overrides: object) -> dict:
    return {
        "tenant_id": "api-tenant",
        "user_id": "api-user",
        "content": "the registry switched to npmmirror",
        "evidence": [{"source_type": "test", "source_id": "scope:1"}],
        **overrides,
    }


def _headers(
    *,
    tenant: str = "api-tenant",
    user: str = "api-user",
    session: str | None = None,
) -> dict[str, str]:
    headers = {TENANT_HEADER: tenant, USER_HEADER: user}
    if session is not None:
        headers[SESSION_HEADER] = session
    return headers


def test_scope_requires_nonempty_tenant() -> None:
    with pytest.raises(ValueError):
        RequestScope(tenant_id="", user_id="user-1")
    with pytest.raises(ValueError):
        RequestScope(tenant_id="   ", user_id="user-1")
    with pytest.raises(ValueError):
        RequestScope(user_id="user-1")


def test_scope_requires_nonempty_user() -> None:
    with pytest.raises(ValueError):
        RequestScope(tenant_id="tenant-1", user_id="")
    with pytest.raises(ValueError):
        RequestScope(tenant_id="tenant-1", user_id="   ")
    with pytest.raises(ValueError):
        RequestScope(tenant_id="tenant-1")


def test_scope_accepts_optional_session_narrowing() -> None:
    assert RequestScope(tenant_id="t", user_id="u").session_id is None
    assert RequestScope(tenant_id="t", user_id="u", session_id="s").session_id == "s"


def test_scope_canonical_serialization_is_stable() -> None:
    scope = RequestScope(tenant_id="tenant-1", user_id="user-1", session_id="session-9")
    assert scope.canonical_key() == "tenant-1|user-1|session-9"


def test_scope_canonical_serialization_omits_missing_session() -> None:
    scope = RequestScope(tenant_id="tenant-1", user_id="user-1")
    assert scope.canonical_key() == "tenant-1|user-1"


def test_scope_canonical_serialization_is_deterministic() -> None:
    first = RequestScope(tenant_id="t", user_id="u", session_id="s").canonical_key()
    second = RequestScope(tenant_id="t", user_id="u", session_id="s").canonical_key()
    assert first == second


def test_scope_matches_when_identities_agree() -> None:
    scope = RequestScope(tenant_id="t", user_id="u", session_id="s")
    assert scope.mismatch(tenant_id="t", user_id="u", session_id="s") is None


def test_scope_mismatch_reports_user_difference() -> None:
    scope = RequestScope(tenant_id="t", user_id="u", session_id="s")
    mismatch = scope.mismatch(tenant_id="t", user_id="other", session_id="s")
    assert isinstance(mismatch, ScopeMismatch)
    assert mismatch.field == "user_id"
    assert mismatch.scope_value == "u"
    assert mismatch.body_value == "other"


def test_scope_mismatch_reports_tenant_difference() -> None:
    scope = RequestScope(tenant_id="t", user_id="u")
    mismatch = scope.mismatch(tenant_id="other", user_id="u")
    assert isinstance(mismatch, ScopeMismatch)
    assert mismatch.field == "tenant_id"


def test_scope_mismatch_reports_session_difference() -> None:
    scope = RequestScope(tenant_id="t", user_id="u", session_id="s")
    mismatch = scope.mismatch(tenant_id="t", user_id="u", session_id="other")
    assert isinstance(mismatch, ScopeMismatch)
    assert mismatch.field == "session_id"


# ---------------------------------------------------------------------------
# D6: concrete header-based request scope on the HTTP surface.
# ---------------------------------------------------------------------------


def test_missing_tenant_header_fails() -> None:
    client = TestClient(create_app())
    response = client.post(
        "/v1/memories",
        headers={USER_HEADER: "api-user"},
        json=_write_payload(),
    )
    assert response.status_code == 422


def test_missing_user_header_fails() -> None:
    client = TestClient(create_app())
    response = client.post(
        "/v1/memories",
        headers={TENANT_HEADER: "api-tenant"},
        json=_write_payload(),
    )
    assert response.status_code == 422


def test_missing_scope_headers_fail_on_write_and_lookup() -> None:
    client = TestClient(create_app())
    assert client.post("/v1/memories", json=_write_payload()).status_code == 422
    assert client.get("/v1/memories/search", params={"q": "npmmirror"}).status_code == 422
    assert (
        client.get(
            "/v1/memories/00000000-0000-0000-0000-000000000000/explain"
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/v1/memories/00000000-0000-0000-0000-000000000000/feedback",
            json={"outcome": "useful"},
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/v1/memories/00000000-0000-0000-0000-000000000000/forget"
        ).status_code
        == 422
    )


def test_body_identity_mismatch_with_headers_fails() -> None:
    client = TestClient(create_app())
    response = client.post(
        "/v1/memories",
        headers=_headers(tenant="tenant-a", user="user-a"),
        json=_write_payload(tenant_id="tenant-b", user_id="user-a"),
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "scope_mismatch"


def test_session_header_narrows_lookup_scope() -> None:
    client = TestClient(create_app())
    written = client.post(
        "/v1/memories",
        headers=_headers(session="session-1"),
        json=_write_payload(session_id="session-1"),
    )
    assert written.status_code == 200
    memory_id = written.json()["memory_id"]

    in_session = client.get(
        f"/v1/memories/{memory_id}/explain", headers=_headers(session="session-1")
    )
    out_of_session = client.get(
        f"/v1/memories/{memory_id}/explain", headers=_headers(session="session-2")
    )
    assert in_session.status_code == 200
    assert out_of_session.status_code == 404


def test_scoped_search_requires_headers_and_isolates_sessions() -> None:
    client = TestClient(create_app())
    session_one = client.post(
        "/v1/memories",
        headers=_headers(session="session-1"),
        json=_write_payload(session_id="session-1", content="npmmirror session one"),
    )
    assert session_one.status_code == 200
    client.post(
        "/v1/memories",
        headers=_headers(session="session-2"),
        json=_write_payload(session_id="session-2", content="npmmirror session two"),
    )

    hits_one = client.get(
        "/v1/memories/search",
        headers=_headers(session="session-1"),
        params={"q": "npmmirror"},
    )
    hits_two = client.get(
        "/v1/memories/search",
        headers=_headers(session="session-2"),
        params={"q": "npmmirror"},
    )
    assert hits_one.status_code == 200
    assert hits_two.status_code == 200
    assert hits_one.json()[0]["memory"]["session_id"] == "session-1"
    assert hits_two.json()[0]["memory"]["session_id"] == "session-2"