from fastapi.testclient import TestClient

from evoeventmem.api.app import app

client = TestClient(app)


def test_health() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_write_and_search() -> None:
    payload = {
        "user_id": "api-test-user",
        "kind": "event",
        "content": "The coding agent fixed a dependency conflict.",
        "entities": ["coding agent", "dependency"],
        "evidence": [{"source_type": "test", "source_id": "api-1"}],
    }
    created = client.post("/v1/memories", json=payload)
    assert created.status_code == 200

    response = client.get(
        "/v1/memories/search",
        params={"user_id": "api-test-user", "q": "dependency conflict"},
    )
    assert response.status_code == 200
    assert response.json()[0]["memory"]["content"] == payload["content"]
