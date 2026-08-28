"""Tests for rate limiting middleware."""

from __future__ import annotations

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from evoeventmem.api.ratelimit import setup_rate_limiting


@pytest.fixture()
def app_with_ratelimit() -> FastAPI:
    """Create a FastAPI app with rate limiting for testing."""
    app = FastAPI()
    limiter = setup_rate_limiting(app, write_limit="2/minute", read_limit="3/minute")

    @app.get("/test/read")
    @limiter.limit("3/minute")
    async def test_read(request: Request) -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/test/write")
    @limiter.limit("2/minute")
    async def test_write(request: Request) -> dict[str, str]:
        return {"status": "ok"}

    return app


@pytest.fixture()
def client(app_with_ratelimit: FastAPI) -> TestClient:
    """Create a test client."""
    return TestClient(app_with_ratelimit)


def test_rate_limit_not_exceeded(client: TestClient) -> None:
    """Test that requests within rate limit succeed."""
    response = client.get("/test/read", headers={"X-Tenant-Id": "tenant1", "X-User-Id": "user1"})
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_rate_limit_exceeded_returns_429(client: TestClient) -> None:
    """Test that requests exceeding rate limit return 429."""
    # Exceed the read limit (3/minute)
    for _ in range(4):
        response = client.get(
            "/test/read", headers={"X-Tenant-Id": "tenant1", "X-User-Id": "user1"}
        )
    
    assert response.status_code == 429
    assert "rate limit" in response.text.lower() or "too many requests" in response.text.lower()


def test_rate_limit_per_tenant(client: TestClient) -> None:
    """Test that rate limits are applied per tenant."""
    # Tenant 1 hits the limit
    for _ in range(3):
        client.get("/test/read", headers={"X-Tenant-Id": "tenant1", "X-User-Id": "user1"})
    
    # Tenant 1 is now rate limited
    response_tenant1 = client.get(
        "/test/read", headers={"X-Tenant-Id": "tenant1", "X-User-Id": "user1"}
    )
    assert response_tenant1.status_code == 429
    
    # Tenant 2 should still be able to make requests
    response_tenant2 = client.get(
        "/test/read", headers={"X-Tenant-Id": "tenant2", "X-User-Id": "user2"}
    )
    assert response_tenant2.status_code == 200


def test_write_rate_limit_separate_from_read(client: TestClient) -> None:
    """Test that write and read rate limits are independent."""
    # Exhaust read limit
    for _ in range(3):
        client.get("/test/read", headers={"X-Tenant-Id": "tenant1", "X-User-Id": "user1"})
    
    # Write should still work
    response = client.post("/test/write", headers={"X-Tenant-Id": "tenant1", "X-User-Id": "user1"})
    assert response.status_code == 200
