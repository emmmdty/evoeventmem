from fastapi.testclient import TestClient

from evoeventmem.api.app import create_app

TENANT_HEADER = "X-Tenant-Id"
USER_HEADER = "X-User-Id"


def _headers(*, tenant: str = "api-tenant", user: str = "api-user") -> dict[str, str]:
    return {TENANT_HEADER: tenant, USER_HEADER: user}


def _write_payload(**overrides: object) -> dict:
    return {
        "tenant_id": "api-tenant",
        "user_id": "api-user",
        "kind": "event",
        "content": "The coding agent fixed a dependency conflict.",
        "entities": ["coding agent", "dependency"],
        "evidence": [{"source_type": "test", "source_id": "api-1"}],
        **overrides,
    }


client = TestClient(create_app())


def test_health() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_write_and_search_preserve_legacy_response_shape() -> None:
    superseded_id = "11111111-1111-1111-1111-111111111111"
    created = client.post(
        "/v1/memories",
        headers=_headers(),
        json=_write_payload(supersedes=[superseded_id]),
    )
    assert created.status_code == 200

    created_memory = created.json()
    assert created_memory["kind"] == "event"
    assert created_memory["entities"] == ["coding agent", "dependency"]
    assert created_memory["evidence"] == [
        {
            "source_type": "test",
            "source_id": "api-1",
            "locator": None,
            "quote": None,
        }
    ]
    assert created_memory["supersedes"] == superseded_id
    assert "memory_kind" not in created_memory
    assert "evidence_refs" not in created_memory

    response = client.get(
        "/v1/memories/search",
        headers=_headers(),
        params={"q": "dependency conflict"},
    )
    assert response.status_code == 200

    searched_memory = response.json()[0]["memory"]
    assert searched_memory["kind"] == "event"
    assert searched_memory["entities"] == ["coding agent", "dependency"]
    assert searched_memory["evidence"] == [
        {
            "source_type": "test",
            "source_id": "api-1",
            "locator": None,
            "quote": None,
        }
    ]
    assert searched_memory["supersedes"] == superseded_id
    assert "memory_kind" not in searched_memory
    assert "evidence_refs" not in searched_memory


def test_write_same_content_for_different_tenants_does_not_leak_response() -> None:
    first_id = "22222222-2222-2222-2222-222222222221"
    second_id = "22222222-2222-2222-2222-222222222222"
    base_payload = {
        "user_id": "api-shared-tenant-user",
        "content": "A tenant-scoped identical memory.",
    }
    first = client.post(
        "/v1/memories",
        headers=_headers(tenant="api-tenant-1", user="api-shared-tenant-user"),
        json={
            **base_payload,
            "memory_id": first_id,
            "tenant_id": "api-tenant-1",
            "user_id": "api-shared-tenant-user",
            "evidence": [{"source_type": "test", "source_id": "tenant-1:1"}],
        },
    )
    second = client.post(
        "/v1/memories",
        headers=_headers(tenant="api-tenant-2", user="api-shared-tenant-user"),
        json={
            **base_payload,
            "memory_id": second_id,
            "tenant_id": "api-tenant-2",
            "user_id": "api-shared-tenant-user",
            "evidence": [{"source_type": "test", "source_id": "tenant-2:1"}],
        },
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["memory_id"] == first_id
    assert second.json()["memory_id"] == second_id
    assert second.json()["evidence"][0]["source_id"] == "tenant-2:1"
    assert set(second.json()) == set(first.json())


def test_write_memory_id_collision_returns_conflict_without_overwrite() -> None:
    memory_id = "33333333-3333-3333-3333-333333333333"
    first_payload = {
        "memory_id": memory_id,
        "tenant_id": "api-collision-tenant-1",
        "user_id": "api-collision-user",
        "content": "Original API memory.",
        "evidence": [{"source_type": "test", "source_id": "original:1"}],
    }
    first = client.post(
        "/v1/memories",
        headers=_headers(tenant="api-collision-tenant-1", user="api-collision-user"),
        json=first_payload,
    )
    colliding = client.post(
        "/v1/memories",
        headers=_headers(tenant="api-collision-tenant-1", user="api-collision-user"),
        json={
            **first_payload,
            "content": "Attempted replacement API memory.",
            "evidence": [{"source_type": "test", "source_id": "replacement:1"}],
        },
    )
    retry = client.post(
        "/v1/memories",
        headers=_headers(tenant="api-collision-tenant-1", user="api-collision-user"),
        json=first_payload,
    )

    assert first.status_code == 200
    assert colliding.status_code == 409
    assert colliding.json() == {"detail": "memory_id_collision"}
    assert retry.status_code == 200
    assert retry.json() == first.json()


def test_search_isolates_tenants_and_rejects_missing_scope() -> None:
    user_id = "api-search-tenant-user"
    expected_ids = {
        "tenant-a": "44444444-4444-4444-4444-444444444441",
        "tenant-b": "44444444-4444-4444-4444-444444444442",
    }
    for tenant_id, memory_id in expected_ids.items():
        response = client.post(
            "/v1/memories",
            headers=_headers(tenant=tenant_id, user=user_id),
            json={
                "memory_id": memory_id,
                "tenant_id": tenant_id,
                "user_id": user_id,
                "content": f"Shared API search marker {tenant_id}",
                "evidence": [{"source_type": "test", "source_id": f"{tenant_id}:1"}],
            },
        )
        assert response.status_code == 200

    tenant_a = client.get(
        "/v1/memories/search",
        headers=_headers(tenant="tenant-a", user=user_id),
        params={"q": "shared search marker"},
    )
    tenant_b = client.get(
        "/v1/memories/search",
        headers=_headers(tenant="tenant-b", user=user_id),
        params={"q": "shared search marker"},
    )
    missing_headers = client.get(
        "/v1/memories/search",
        params={"q": "shared search marker"},
    )

    assert tenant_a.status_code == 200
    assert tenant_b.status_code == 200
    assert missing_headers.status_code == 422
    assert [hit["memory"]["memory_id"] for hit in tenant_a.json()] == [
        expected_ids["tenant-a"]
    ]
    assert [hit["memory"]["memory_id"] for hit in tenant_b.json()] == [
        expected_ids["tenant-b"]
    ]


def test_cross_tenant_explain_returns_not_found_without_leak() -> None:
    user_id = "api-isolation-user"
    created = client.post(
        "/v1/memories",
        headers=_headers(tenant="tenant-a", user=user_id),
        json={
            "tenant_id": "tenant-a",
            "user_id": user_id,
            "content": "Isolation probe memory.",
            "evidence": [{"source_type": "test", "source_id": "isolation:1"}],
        },
    )
    assert created.status_code == 200
    memory_id = created.json()["memory_id"]

    same_scope = client.get(
        f"/v1/memories/{memory_id}/explain",
        headers=_headers(tenant="tenant-a", user=user_id),
    )
    wrong_tenant = client.get(
        f"/v1/memories/{memory_id}/explain",
        headers=_headers(tenant="tenant-b", user=user_id),
    )
    wrong_user = client.get(
        f"/v1/memories/{memory_id}/explain",
        headers=_headers(tenant="tenant-a", user="other-user"),
    )

    assert same_scope.status_code == 200
    assert wrong_tenant.status_code == 404
    assert wrong_user.status_code == 404
    assert wrong_tenant.json() == wrong_user.json()
