"""Docker Compose smoke test for the EvoEventMem API.

Host-callable modes:

  python scripts/compose_smoke.py \
    --base-url http://127.0.0.1:8000 \
    --phase seed \
    --state-file artifacts/smoke/compose-state.json
  # Lead restarts the API externally between seed and verify.
  python scripts/compose_smoke.py \
    --base-url http://127.0.0.1:8000 \
    --phase verify \
    --state-file artifacts/smoke/compose-state.json

One-shot mode (Compose ``smoke`` profile, no state file) writes and verifies in
a single run and does not prove restart persistence:

  python scripts/compose_smoke.py --base-url http://api:8000

Checks: migration/readiness, scoped write, pgvector search, explain, feedback,
forget, metrics, persistence after restart (verify phase), and a negative
cross-scope request. Uses only the standard library so the smoke image needs no
extra packages. No Docker socket is required or mounted.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_BASE_URL = "http://api:8000"

SCOPE_HEADERS = {
    "X-Tenant-Id": "compose-tenant",
    "X-User-Id": "compose-user",
    "X-Session-Id": "compose-session",
}

WRITE_PAYLOAD = {
    "tenant_id": "compose-tenant",
    "user_id": "compose-user",
    "session_id": "compose-session",
    "content": "the compose smoke test switched the registry to npmmirror",
    "evidence": [{"source_type": "smoke", "source_id": "compose:1"}],
}

EXPECTED_EMBEDDING = {"provider": "deterministic", "model_id": "test-model", "dimension": 4}


def _parse(content: bytes) -> object:
    if not content:
        return None
    try:
        return json.loads(content)
    except (ValueError, UnicodeDecodeError):
        return content.decode("utf-8", errors="replace")


def request(
    base_url: str, method: str, path: str, payload: object | None = None
) -> tuple[int, object, dict[str, str]]:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(base_url + path, data=body, method=method)
    for name, value in SCOPE_HEADERS.items():
        req.add_header(name, value)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status, _parse(response.read()), dict(response.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, _parse(exc.read()), dict(exc.headers)


def fail(step: str, detail: object) -> None:
    print(f"compose smoke FAILED at step {step}: {detail!r}", flush=True)
    sys.exit(1)


def check_readiness(base_url: str) -> dict[str, Any]:
    status, body, _ = request(base_url, "GET", "/readiness")
    if status != 200 or not isinstance(body, dict) or body.get("store") != "postgres":
        fail("readiness/migration", (status, body))
    embedding = body.get("embedding")
    if embedding != EXPECTED_EMBEDDING:
        fail("readiness embedding identity", embedding)
    return embedding


def write_memory(base_url: str, payload: dict[str, Any]) -> dict[str, Any]:
    status, body, _ = request(base_url, "POST", "/v1/memories", payload)
    if status != 200 or not isinstance(body, dict):
        fail("write", (status, body))
    return body


def check_pgvector_search(base_url: str, memory_id: str) -> None:
    status, body, _ = request(
        base_url, "GET", "/v1/memories/search?q=npmmirror&limit=10"
    )
    if status != 200 or not isinstance(body, list) or not body:
        fail("pgvector search", (status, body))
    first = body[0]
    if first["memory"]["memory_id"] != memory_id:
        fail("pgvector search top hit", (memory_id, first))
    if first.get("reason") != "pgvector cosine":
        fail("pgvector search source", first.get("reason"))


def check_explain(base_url: str, memory_id: str) -> None:
    status, body, _ = request(base_url, "GET", f"/v1/memories/{memory_id}/explain")
    if status != 200 or not isinstance(body, dict) or body["memory"]["memory_id"] != memory_id:
        fail("explain", (status, body))


def check_feedback(base_url: str, memory_id: str) -> None:
    status, body, _ = request(
        base_url,
        "POST",
        f"/v1/memories/{memory_id}/feedback",
        {"outcome": "useful", "rating": 1.0},
    )
    if status != 200 or not isinstance(body, dict) or not body["metadata"]["feedback_events"]:
        fail("feedback", (status, body))


def check_negative_cross_scope(base_url: str, memory_id: str) -> None:
    req = urllib.request.Request(base_url + f"/v1/memories/{memory_id}/explain")
    req.add_header("X-Tenant-Id", "compose-tenant")
    req.add_header("X-User-Id", "compose-other-user")
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            status = response.status
    except urllib.error.HTTPError as exc:
        status = exc.code
    if status != 404:
        fail("negative cross-scope", status)


def check_metrics(base_url: str) -> None:
    status, body, _ = request(base_url, "GET", "/metrics")
    text = body if isinstance(body, str) else str(body)
    if status != 200 or "evoeventmem_http_requests_total" not in text:
        fail("metrics", (status, body))


def check_forget(base_url: str, memory_id: str) -> None:
    status, body, _ = request(base_url, "POST", f"/v1/memories/{memory_id}/forget")
    if status != 200 or not isinstance(body, dict) or "forgotten_at" not in body["metadata"]:
        fail("forget", (status, body))


def one_shot(base_url: str) -> None:
    check_readiness(base_url)
    memory = write_memory(base_url, WRITE_PAYLOAD)
    memory_id = memory["memory_id"]
    check_pgvector_search(base_url, memory_id)
    check_explain(base_url, memory_id)
    check_feedback(base_url, memory_id)
    check_negative_cross_scope(base_url, memory_id)
    check_metrics(base_url)
    check_forget(base_url, memory_id)
    print("compose smoke ok (one-shot)", flush=True)


def seed(base_url: str, state_file: Path) -> None:
    embedding = check_readiness(base_url)
    memory = write_memory(base_url, WRITE_PAYLOAD)
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state = {
        "memory_id": memory["memory_id"],
        "tenant_id": SCOPE_HEADERS["X-Tenant-Id"],
        "user_id": SCOPE_HEADERS["X-User-Id"],
        "session_id": SCOPE_HEADERS["X-Session-Id"],
        "embedding": embedding,
    }
    state_file.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"compose smoke seed ok: {state_file}", flush=True)


def verify(base_url: str, state_file: Path) -> None:
    if not state_file.exists():
        fail("verify state file", state_file)
    state = json.loads(state_file.read_text(encoding="utf-8"))
    memory_id = state.get("memory_id")
    if not isinstance(memory_id, str):
        fail("verify state memory_id", state)

    embedding = check_readiness(base_url)
    if embedding != state.get("embedding"):
        fail("verify embedding identity drift", (state.get("embedding"), embedding))

    check_explain(base_url, memory_id)
    check_pgvector_search(base_url, memory_id)
    check_feedback(base_url, memory_id)
    check_negative_cross_scope(base_url, memory_id)
    check_metrics(base_url)
    check_forget(base_url, memory_id)
    print("compose smoke ok (verify: persistence proven before forget)", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument(
        "--phase", choices=("seed", "verify"), default=None,
        help="two-phase persistence check; omit for one-shot mode",
    )
    parser.add_argument("--state-file", type=Path)
    args = parser.parse_args()

    if args.phase == "seed":
        if args.state_file is None:
            parser.error("--phase seed requires --state-file")
        seed(args.base_url, args.state_file)
    elif args.phase == "verify":
        if args.state_file is None:
            parser.error("--phase verify requires --state-file")
        verify(args.base_url, args.state_file)
    else:
        if args.state_file is not None:
            parser.error("--state-file requires --phase seed or --phase verify")
        one_shot(args.base_url)


if __name__ == "__main__":
    main()
