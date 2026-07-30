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
