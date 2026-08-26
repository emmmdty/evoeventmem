from __future__ import annotations

import io
import json
import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from evoeventmem.api.app import create_app
from evoeventmem.infra.config import Settings
from evoeventmem.infra.logging import StructuredLogFormatter

_ROOT = Path(__file__).resolve().parents[2]

_UNREACHABLE_DSN = "postgresql://user:swordfish@127.0.0.1:1/evoeventmem"

TENANT_HEADER = "X-Tenant-Id"
USER_HEADER = "X-User-Id"
SESSION_HEADER = "X-Session-Id"


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


def _write_payload(*, content: str = "the project moved to npmmirror", **overrides: object) -> dict:
    return {
        "tenant_id": "api-tenant",
        "user_id": "api-user",
        "content": content,
        "evidence": [{"source_type": "test", "source_id": "api-test:1"}],
        **overrides,
    }


def test_readiness_reports_memory_store_without_lifespan() -> None:
    client = TestClient(create_app())
    response = client.get("/readiness")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"
    assert response.json()["store"] == "memory"


def test_health_and_metrics_endpoints() -> None:
    client = TestClient(create_app())
    assert client.get("/health").json() == {"status": "ok"}
    metrics_response = client.get("/metrics")
    assert metrics_response.status_code == 200
    assert "evoeventmem_http_requests_total" in metrics_response.text
    assert metrics_response.headers["content-type"].startswith("text/plain")


def test_explain_feedback_forget_lifecycle() -> None:
    client = TestClient(create_app())
    written = client.post("/v1/memories", headers=_headers(), json=_write_payload())
    assert written.status_code == 200
    memory_id = written.json()["memory_id"]

    explained = client.get(f"/v1/memories/{memory_id}/explain", headers=_headers())
    assert explained.status_code == 200
    assert explained.json()["memory"]["memory_id"] == memory_id
    assert explained.json()["related"] == []

    feedback = client.post(
        f"/v1/memories/{memory_id}/feedback",
        headers=_headers(),
        json={"outcome": "useful", "rating": 0.9},
    )
    assert feedback.status_code == 200
    assert feedback.json()["metadata"]["feedback_events"][0]["outcome"] == "useful"

    forgotten = client.post(f"/v1/memories/{memory_id}/forget", headers=_headers())
    assert forgotten.status_code == 200
    assert forgotten.json()["metadata"]["forgotten_at"] is not None

    explained_again = client.get(f"/v1/memories/{memory_id}/explain", headers=_headers())
    assert explained_again.status_code == 200
    assert explained_again.json()["memory"]["memory_id"] == memory_id


def test_forget_removes_memory_from_search() -> None:
    client = TestClient(create_app())
    written = client.post("/v1/memories", headers=_headers(), json=_write_payload())
    memory_id = written.json()["memory_id"]

    before = client.get(
        "/v1/memories/search",
        headers=_headers(),
        params={"q": "npmmirror"},
    )
    assert [hit["memory"]["memory_id"] for hit in before.json()] == [memory_id]

    client.post(f"/v1/memories/{memory_id}/forget", headers=_headers())

    after = client.get(
        "/v1/memories/search",
        headers=_headers(),
        params={"q": "npmmirror"},
    )
    assert after.json() == []


def test_explain_related_returns_linked_memories() -> None:
    client = TestClient(create_app())
    base = client.post(
        "/v1/memories",
        headers=_headers(),
        json=_write_payload(
            content="base durable memory",
            evidence=[{"source_type": "test", "source_id": "base:1"}],
        ),
    ).json()
    superseding = client.post(
        "/v1/memories",
        headers=_headers(),
        json=_write_payload(
            content="superseding memory",
            supersedes=[base["memory_id"]],
            evidence=[{"source_type": "test", "source_id": "super:1"}],
        ),
    ).json()

    explained = client.get(
        f"/v1/memories/{superseding['memory_id']}/explain", headers=_headers()
    )

    assert explained.status_code == 200
    related_ids = [item["memory_id"] for item in explained.json()["related"]]
    assert related_ids == [base["memory_id"]]


def test_scoping_isolates_explain_feedback_forget() -> None:
    client = TestClient(create_app())
    written = client.post(
        "/v1/memories",
        headers=_headers(tenant="tenant-a", user="user-a"),
        json=_write_payload(
            tenant_id="tenant-a",
            user_id="user-a",
            content="tenant-a memory",
            evidence=[{"source_type": "test", "source_id": "a:1"}],
        ),
    )
    memory_id = written.json()["memory_id"]

    wrong_user = client.get(
        f"/v1/memories/{memory_id}/explain", headers=_headers(user="user-b")
    )
    wrong_tenant = client.get(
        f"/v1/memories/{memory_id}/explain", headers=_headers(tenant="tenant-b")
    )
    missing = client.get(
        "/v1/memories/00000000-0000-0000-0000-000000000000/explain",
        headers=_headers(),
    )

    assert wrong_user.status_code == 404
    assert wrong_tenant.status_code == 404
    assert missing.status_code == 404

    assert (
        client.post(
            f"/v1/memories/{memory_id}/feedback",
            headers=_headers(user="user-b"),
            json={"outcome": "useful"},
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/v1/memories/{memory_id}/forget",
            headers=_headers(user="user-b"),
        ).status_code
        == 404
    )


def test_feedback_validates_outcome_and_rating() -> None:
    client = TestClient(create_app())
    memory_id = client.post(
        "/v1/memories", headers=_headers(), json=_write_payload()
    ).json()["memory_id"]

    empty_outcome = client.post(
        f"/v1/memories/{memory_id}/feedback",
        headers=_headers(),
        json={"outcome": ""},
    )
    bad_rating = client.post(
        f"/v1/memories/{memory_id}/feedback",
        headers=_headers(),
        json={"outcome": "useful", "rating": 1.5},
    )

    assert empty_outcome.status_code == 422
    assert bad_rating.status_code == 422


def test_request_id_is_echoed_and_generated() -> None:
    client = TestClient(create_app())
    first = client.get("/health", headers={"X-Request-ID": "req-custom"})
    second = client.get("/health")
    third = client.get("/health")

    assert first.headers["x-request-id"] == "req-custom"
    assert second.headers["x-request-id"] != third.headers["x-request-id"]


def test_postgres_unavailable_fails_closed_by_default() -> None:
    settings = Settings(
        store="postgres",
        database_url=_UNREACHABLE_DSN,
        db_connect_timeout=1.0,
        db_operation_timeout=5.0,
    )
    app = create_app(settings)
    with TestClient(app) as client:
        readiness = client.get("/readiness")
        write = client.post("/v1/memories", headers=_headers(), json=_write_payload())
        search = client.get(
            "/v1/memories/search", headers=_headers(), params={"q": "npmmirror"}
        )
        explain = client.get(
            "/v1/memories/00000000-0000-0000-0000-000000000000/explain",
            headers=_headers(),
        )
        metrics = client.get("/metrics")

    assert readiness.status_code == 503
    assert readiness.json()["detail"]["reason"] == "store_unavailable"
    assert write.status_code == 503
    assert write.json()["detail"] == "store_unavailable"
    assert search.status_code == 503
    assert explain.status_code == 503
    assert "evoeventmem_store_fallback_total 1" not in metrics.text


def test_development_fallback_serves_degraded_only_when_explicit() -> None:
    settings = Settings(
        store="postgres",
        database_url=_UNREACHABLE_DSN,
        db_connect_timeout=1.0,
        db_operation_timeout=5.0,
        allow_development_fallback=True,
    )
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(StructuredLogFormatter())
    app = create_app(settings)
    logging.getLogger("evoeventmem").addHandler(handler)
    with TestClient(app) as client:
        readiness = client.get("/readiness")
        health = client.get("/health")
        write = client.post("/v1/memories", headers=_headers(), json=_write_payload())
        metrics = client.get("/metrics")

    assert readiness.status_code == 503
    assert readiness.json()["detail"]["status"] == "degraded"
    assert readiness.json()["detail"]["store"] == "memory-degraded"
    assert health.json() == {"status": "ok", "degraded": True}
    assert write.status_code == 200
    assert write.headers["x-evoeventmem-degraded"] == "true"
    assert "evoeventmem_store_fallback_total 1" in metrics.text
    assert "evoeventmem_degraded_responses_total" in metrics.text

    payloads = [
        json.loads(line)
        for line in stream.getvalue().strip().splitlines()
        if line.strip()
    ]
    fallback_payload = next(
        payload for payload in payloads if payload["event"] == "store.fallback"
    )
    assert fallback_payload["degraded"] is True
    assert fallback_payload["requested_store"] == "postgres"
    assert "swordfish" not in stream.getvalue()


def test_exception_responses_record_reason_and_source_metrics() -> None:
    settings = Settings(
        store="postgres",
        database_url=_UNREACHABLE_DSN,
        db_connect_timeout=1.0,
        db_operation_timeout=5.0,
    )
    app = create_app(settings)
    with TestClient(app) as client:
        client.post("/v1/memories", headers=_headers(), json=_write_payload())
        metrics = client.get("/metrics")

    assert (
        'evoeventmem_http_exceptions_total{reason="store_unavailable",source="postgres"}'
        in metrics.text
    )
    assert (
        'evoeventmem_http_requests_total{method="POST",path="/v1/memories",status="503"}'
        in metrics.text
    )


def test_metrics_use_route_templates_not_raw_uuid_paths() -> None:
    client = TestClient(create_app())
    memory_id = client.post(
        "/v1/memories", headers=_headers(), json=_write_payload()
    ).json()["memory_id"]
    client.post(
        f"/v1/memories/{memory_id}/feedback",
        headers=_headers(),
        json={"outcome": "useful"},
    )
    metrics = client.get("/metrics").text

    assert 'path="/v1/memories/{memory_id}/feedback"' in metrics
    assert 'path="/v1/memories/{memory_id}"' not in metrics
    assert str(memory_id) not in metrics


def test_middleware_logs_request_id_reason_source_and_duration() -> None:
    settings = Settings(
        store="postgres",
        database_url=_UNREACHABLE_DSN,
        db_connect_timeout=1.0,
        db_operation_timeout=5.0,
    )
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(StructuredLogFormatter())
    app = create_app(settings)
    logging.getLogger("evoeventmem").addHandler(handler)
    with TestClient(app) as client:
        write = client.post(
            "/v1/memories",
            headers={**_headers(), "X-Request-ID": "req-fail-closed"},
            json=_write_payload(),
        )
        assert write.status_code == 503

    payloads = [
        json.loads(line)
        for line in stream.getvalue().strip().splitlines()
        if line.strip()
    ]
    request_payload = next(
        payload for payload in payloads if payload["event"] == "http.request"
    )
    assert request_payload["request_id"] == "req-fail-closed"
    assert request_payload["reason"] == "store_unavailable"
    assert request_payload["source"] == "postgres"
    assert request_payload["status"] == 503
    assert request_payload["duration_ms"] > 0


def test_token_overlap_policy_marks_all_responses_degraded() -> None:
    settings = Settings(embedding_policy="token_overlap")
    app = create_app(settings)
    with TestClient(app) as client:
        readiness = client.get("/readiness")
        write = client.post("/v1/memories", headers=_headers(), json=_write_payload())
        metrics = client.get("/metrics")

    assert readiness.status_code == 200
    assert readiness.json()["status"] == "degraded"
    assert write.status_code == 200
    assert write.headers["x-evoeventmem-degraded"] == "true"
    assert "evoeventmem_degraded_responses_total" in metrics.text


def test_openapi_schema_matches_committed_artifact() -> None:
    committed = json.loads((_ROOT / "api/openapi.json").read_text(encoding="utf-8"))
    generated = create_app().openapi()
    assert generated == committed


def test_openapi_schema_exposes_expected_paths() -> None:
    paths = create_app().openapi()["paths"]
    for expected in (
        "/health",
        "/readiness",
        "/metrics",
        "/v1/memories",
        "/v1/memories/search",
        "/v1/memories/{memory_id}/explain",
        "/v1/memories/{memory_id}/feedback",
        "/v1/memories/{memory_id}/forget",
    ):
        assert expected in paths


def test_openapi_schema_declares_scope_headers() -> None:
    schema = create_app().openapi()
    write_parameters = schema["paths"]["/v1/memories"]["post"]["parameters"]
    header_names = {parameter["name"] for parameter in write_parameters}
    assert "X-Tenant-Id" in header_names
    assert "X-User-Id" in header_names
    assert "X-Session-Id" in header_names


def test_structured_log_lines_contain_request_id() -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(StructuredLogFormatter())
    app = create_app()
    logging.getLogger("evoeventmem").addHandler(handler)
    client = TestClient(app)
    write_response = client.post("/v1/memories", headers=_headers(), json=_write_payload())

    payloads = [
        json.loads(line)
        for line in stream.getvalue().strip().splitlines()
        if line.strip()
    ]
    written = next(payload for payload in payloads if payload["event"] == "memory.written")
    assert written["request_id"] == write_response.headers["x-request-id"]


def test_logs_never_contain_memory_content() -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(StructuredLogFormatter())
    app = create_app()
    logging.getLogger("evoeventmem").addHandler(handler)
    client = TestClient(app)
    client.post(
        "/v1/memories",
        headers=_headers(),
        json=_write_payload(content="secret project codename npmmirror-42"),
    )

    assert "secret project codename npmmirror-42" not in stream.getvalue()
    assert "npmmirror-42" not in stream.getvalue()


# ---------------------------------------------------------------------------
# T2-A6: existing endpoints work when auth is enabled (mock auth)
# ---------------------------------------------------------------------------

_AUTH_TOKEN = "test-api-key-t2a6"


def _auth_headers(
    *,
    tenant: str = "api-tenant",
    user: str = "api-user",
    session: str | None = None,
) -> dict[str, str]:
    h = _headers(tenant=tenant, user=user, session=session)
    h["Authorization"] = f"Bearer {_AUTH_TOKEN}"
    return h


def test_existing_endpoints_work_with_auth_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T2-A6: all CRUD endpoints return 200 when EEM_API_KEYS is set and
    requests carry a valid Bearer token."""
    monkeypatch.setenv("EEM_API_KEYS", _AUTH_TOKEN)
    client = TestClient(create_app())

    # write
    write_resp = client.post(
        "/v1/memories", headers=_auth_headers(), json=_write_payload()
    )
    assert write_resp.status_code == 200
    memory_id = write_resp.json()["memory_id"]

    # search
    search_resp = client.get(
        "/v1/memories/search",
        headers=_auth_headers(),
        params={"q": "npmmirror"},
    )
    assert search_resp.status_code == 200

    # explain
    explain_resp = client.get(
        f"/v1/memories/{memory_id}/explain",
        headers=_auth_headers(),
    )
    assert explain_resp.status_code == 200

    # feedback
    feedback_resp = client.post(
        f"/v1/memories/{memory_id}/feedback",
        headers=_auth_headers(),
        json={"outcome": "useful"},
    )
    assert feedback_resp.status_code == 200

    # forget
    forget_resp = client.post(
        f"/v1/memories/{memory_id}/forget",
        headers=_auth_headers(),
    )
    assert forget_resp.status_code == 200
