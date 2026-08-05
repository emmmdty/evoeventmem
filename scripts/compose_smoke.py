"""Docker Compose smoke test for the EvoEventMem API.

Runs inside the compose ``smoke`` service and exercises the full HTTP
surface against a Postgres-backed API container. Uses only the standard
library so the smoke image needs no extra packages.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

BASE_URL = "http://api:8000"


def _parse(content: bytes) -> object:
    if not content:
        return None
    try:
        return json.loads(content)
    except (ValueError, UnicodeDecodeError):
        return content.decode("utf-8", errors="replace")


def request(method: str, path: str, payload: object | None = None) -> tuple[int, object]:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(BASE_URL + path, data=body, method=method)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status, _parse(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, _parse(exc.read())


def fail(step: str, detail: object) -> None:
    print(f"compose smoke FAILED at step {step}: {detail!r}")
    sys.exit(1)


def main() -> None:
    status, body = request("GET", "/readiness")
    if status != 200 or body.get("store") != "postgres":
        fail("readiness", (status, body))

    payload = {
        "tenant_id": "compose-tenant",
        "user_id": "compose-user",
        "content": "the compose smoke test switched the registry to npmmirror",
        "evidence": [{"source_type": "smoke", "source_id": "compose:1"}],
    }
    status, body = request("POST", "/v1/memories", payload)
    if status != 200:
        fail("write", (status, body))
    memory_id = body["memory_id"]

    status, body = request(
        "GET",
        "/v1/memories/search?user_id=compose-user&q=npmmirror&tenant_id=compose-tenant",
    )
    if status != 200 or not body or body[0]["memory"]["memory_id"] != memory_id:
        fail("search", (status, body))

    status, body = request("GET", f"/v1/memories/{memory_id}/explain?user_id=compose-user")
    if status != 200 or body["memory"]["memory_id"] != memory_id:
        fail("explain", (status, body))

    status, body = request(
        "POST",
        f"/v1/memories/{memory_id}/feedback?user_id=compose-user",
        {"outcome": "useful", "rating": 1.0},
    )
    if status != 200 or not body["metadata"]["feedback_events"]:
        fail("feedback", (status, body))

    status, body = request("POST", f"/v1/memories/{memory_id}/forget?user_id=compose-user")
    if status != 200 or "forgotten_at" not in body["metadata"]:
        fail("forget", (status, body))

    status, body = request("GET", "/metrics")
    if status != 200 or "evoeventmem_http_requests_total" not in body:
        fail("metrics", (status, body))

    print("compose smoke ok")


if __name__ == "__main__":
    main()
