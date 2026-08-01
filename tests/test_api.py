from fastapi.testclient import TestClient

from evoeventmem.api.app import app

client = TestClient(app)


def test_health() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_write_and_search_preserve_legacy_response_shape() -> None:
    superseded_id = "11111111-1111-1111-1111-111111111111"
    payload = {
        "user_id": "api-test-user",
        "kind": "event",
        "content": "The coding agent fixed a dependency conflict.",
        "entities": ["coding agent", "dependency"],
        "evidence": [{"source_type": "test", "source_id": "api-1"}],
        "supersedes": superseded_id,
    }
    created = client.post("/v1/memories", json=payload)
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
        params={"user_id": "api-test-user", "q": "dependency conflict"},
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
        json={
            **base_payload,
            "memory_id": first_id,
            "tenant_id": "api-tenant-1",
            "evidence": [{"source_type": "test", "source_id": "tenant-1:1"}],
        },
    )
    second = client.post(
        "/v1/memories",
        json={
            **base_payload,
            "memory_id": second_id,
            "tenant_id": "api-tenant-2",
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
    first = client.post("/v1/memories", json=first_payload)
    colliding = client.post(
        "/v1/memories",
        json={
            **first_payload,
            "tenant_id": "api-collision-tenant-2",
            "content": "Attempted replacement API memory.",
            "evidence": [{"source_type": "test", "source_id": "replacement:1"}],
        },
    )
    retry = client.post("/v1/memories", json=first_payload)

    assert first.status_code == 200
    assert colliding.status_code == 409
    assert colliding.json() == {"detail": "memory_id_collision"}
    assert retry.status_code == 200
    assert retry.json() == first.json()


def test_search_strictly_isolates_named_and_unscoped_tenants() -> None:
    user_id = "api-search-tenant-user"
    expected_ids = {
        "tenant-a": "44444444-4444-4444-4444-444444444441",
        "tenant-b": "44444444-4444-4444-4444-444444444442",
        None: "44444444-4444-4444-4444-444444444443",
    }
    for tenant_id, memory_id in expected_ids.items():
        label = tenant_id or "legacy"
        response = client.post(
            "/v1/memories",
            json={
                "memory_id": memory_id,
                "tenant_id": tenant_id,
                "user_id": user_id,
                "content": f"Shared API search marker {label}",
                "evidence": [{"source_type": "test", "source_id": f"{label}:1"}],
            },
        )
        assert response.status_code == 200

    tenant_a = client.get(
        "/v1/memories/search",
        params={"user_id": user_id, "q": "shared search marker", "tenant_id": "tenant-a"},
    )
    tenant_b = client.get(
        "/v1/memories/search",
        params={"user_id": user_id, "q": "shared search marker", "tenant_id": "tenant-b"},
    )
    unscoped = client.get(
        "/v1/memories/search",
        params={"user_id": user_id, "q": "shared search marker"},
    )

    assert tenant_a.status_code == 200
    assert tenant_b.status_code == 200
    assert unscoped.status_code == 200
    assert [hit["memory"]["memory_id"] for hit in tenant_a.json()] == [
        expected_ids["tenant-a"]
    ]
    assert [hit["memory"]["memory_id"] for hit in tenant_b.json()] == [
        expected_ids["tenant-b"]
    ]
    assert [hit["memory"]["memory_id"] for hit in unscoped.json()] == [
        expected_ids[None]
    ]
    assert set(unscoped.json()[0]["memory"]) == set(tenant_a.json()[0]["memory"])
