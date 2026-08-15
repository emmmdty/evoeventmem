"""Coding/Debug agent demo with trace capture.

Simulates a short agent loop over the MCP tool surface: a bug report is
matched against memory, retrieved memories and their evidence drive an
action, the outcome is observed, and the whole session is captured as a
JSONL trace. No model service is used; the demo runs on the deterministic
in-memory service behind the real MCP server.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel

from adapters.opencode.mcp_server import build_server
from adapters.opencode.tracing import TraceCapture
from evoeventmem.core.ports import RequestScope
from evoeventmem.domain.models import EntityRef, EvidenceRef, MemoryKind, MemoryRecord
from evoeventmem.infra.async_embedding import DeterministicAsyncEmbeddingModel
from evoeventmem.infra.async_in_memory_repository import AsyncInMemoryRepository
from evoeventmem.services.async_memory_service import AsyncMemoryService

DEFAULT_USER = "demo-user"
DEFAULT_TENANT = "default"


class _Memory(BaseModel):
    memory_id: str
    content: str
    evidence: list[dict[str, Any]]


def _scope() -> RequestScope:
    return RequestScope(tenant_id=DEFAULT_TENANT, user_id=DEFAULT_USER)


async def _seed(service: AsyncMemoryService) -> tuple[MemoryRecord, MemoryRecord, MemoryRecord]:
    earlier = await service.write(
        _scope(),
        MemoryRecord(
            tenant_id=DEFAULT_TENANT,
            user_id=DEFAULT_USER,
            memory_kind=MemoryKind.FACT,
            content="The project pinned the package registry to the default npm registry.",
            entities=[EntityRef(name="package-registry")],
            evidence_refs=[
                EvidenceRef(
                    source_type="chat",
                    source_id="session-1",
                    quote="Keep the default npm registry for now.",
                )
            ],
            event_time=datetime(2026, 8, 1, 9, 0, tzinfo=UTC),
        ),
    )
    switched = await service.write(
        _scope(),
        MemoryRecord(
            tenant_id=DEFAULT_TENANT,
            user_id=DEFAULT_USER,
            memory_kind=MemoryKind.EVENT,
            content="The project switched the package registry to npmmirror for faster installs.",
            entities=[EntityRef(name="package-registry"), EntityRef(name="npmmirror")],
            evidence_refs=[
                EvidenceRef(
                    source_type="chat",
                    source_id="session-2",
                    quote="Switch the registry to npmmirror, installs were slow.",
                )
            ],
            event_time=datetime(2026, 8, 2, 14, 30, tzinfo=UTC),
            supersedes=[earlier.memory_id],
        ),
    )
    broken = await service.write(
        _scope(),
        MemoryRecord(
            tenant_id=DEFAULT_TENANT,
            user_id=DEFAULT_USER,
            memory_kind=MemoryKind.EVENT,
            content="test_mcp.py fails with ModuleNotFoundError 'mcp'; the mcp dependency "
            "is missing from the dev extras.",
            entities=[EntityRef(name="test_mcp.py"), EntityRef(name="mcp")],
            evidence_refs=[
                EvidenceRef(
                    source_type="terminal",
                    source_id="ci-run-42",
                    locator="pytest -q tests/adapters",
                    quote="ModuleNotFoundError: No module named 'mcp'",
                )
            ],
            event_time=datetime(2026, 8, 3, 10, 5, tzinfo=UTC),
        ),
    )
    return earlier, switched, broken


def _compact_memory(memory: MemoryRecord) -> dict[str, Any]:
    return {
        "memory_id": str(memory.memory_id),
        "content": memory.content,
        "evidence": [
            {
                "source_type": ref.source_type,
                "source_id": ref.source_id,
                "locator": ref.locator,
                "quote": ref.quote,
            }
            for ref in memory.evidence_refs
        ],
    }


async def _call_tool(mcp: FastMCP, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    result = await mcp.call_tool(tool, arguments)
    if isinstance(result, tuple):
        result = result[1] if len(result) > 1 else result[0]
    if isinstance(result, dict):
        return result
    text = result[0].text if result else "{}"
    return json.loads(text)


def _print_evidence(evidence: list[dict[str, Any]]) -> None:
    for ref in evidence:
        quote = f" ({ref['quote']})" if ref.get("quote") else ""
        locator = f" at {ref['locator']}" if ref.get("locator") else ""
        print(f"      evidence: {ref['source_type']}/{ref['source_id']}{locator}{quote}")


async def run_demo(*, trace_out: Path | None) -> TraceCapture:
    trace = TraceCapture(trace_out)
    embedding = DeterministicAsyncEmbeddingModel(model_id="demo-deterministic", dimension=32)
    repository = AsyncInMemoryRepository(
        model_id="demo-deterministic",
        dimension=32,
        schema_version="memory.v1",
    )
    service = AsyncMemoryService(
        repository,
        embedding=embedding,
        token_overlap_policy=True,
    )
    _, switched, broken = await _seed(service)
    mcp = build_server(service)
    user = {"user_id": DEFAULT_USER}

    print("Coding/Debug agent demo (deterministic in-memory service, real MCP tools)")
    print("=" * 72)
    print("1. Bug report: CI fails with 'ModuleNotFoundError: No module named mcp'")
    trace.record("bug_reported", bug="ModuleNotFoundError: No module named 'mcp'")

    result = await _call_tool(
        mcp,
        "memory_search",
        {**user, "query": "ModuleNotFoundError mcp dependency", "limit": 3},
    )
    trace.record("memory_search", query="ModuleNotFoundError mcp dependency", result=result)
    print("2. memory_search('ModuleNotFoundError mcp dependency')")
    assert result["status"] == "ok"
    for hit in result["data"]["hits"]:
        print(f"   hit score={hit['score']:.3f}: {hit['content']}")
        _print_evidence(hit["evidence"])

    result = await _call_tool(
        mcp,
        "memory_timeline",
        {**user, "entity_or_topic": "package registry", "limit": 10},
    )
    trace.record("memory_timeline", topic="package registry", result=result)
    print("3. memory_timeline('package registry')")
    assert result["status"] == "ok"
    for event in result["data"]["events"]:
        stamp = event["event_time"] or "untimed"
        print(f"   [{stamp}] {event['content']}")
        _print_evidence(event["evidence"])

    result = await _call_tool(
        mcp,
        "memory_explain",
        {**user, "memory_id": str(switched.memory_id)},
    )
    trace.record("memory_explain", memory_id=str(switched.memory_id), result=result)
    print(f"4. memory_explain({switched.memory_id})")
    assert result["status"] == "ok"
    memory = result["data"]["memory"]
    print(f"   memory: {memory['content']}")
    _print_evidence(memory["evidence"])
    for related in result["data"]["related"]:
        print(f"   related (superseded): {related['content']}")
        _print_evidence(related["evidence"])

    action = (
        "Action: add 'mcp' to the dev extras and run `uv sync --extra dev`, "
        "then re-run `uv run pytest -q tests/adapters`."
    )
    print(f"5. {action}")
    trace.record("agent_action", action=action, driven_by=str(broken.memory_id))

    result = await _call_tool(
        mcp,
        "memory_observe",
        {
            **user,
            "observation": "uv sync installed the mcp dependency; test_mcp.py now passes.",
            "source_type": "terminal",
            "source_id": "ci-run-43",
            "kind": "event",
            "event_time": datetime.now(UTC).isoformat(),
        },
    )
    observed_id = result["data"]["memory"]["memory_id"]
    trace.record("memory_observe", memory_id=observed_id, result=result)
    print(f"6. memory_observe('uv sync installed the mcp dependency; ...') -> {observed_id}")

    result = await _call_tool(
        mcp,
        "memory_feedback",
        {**user, "memory_id": str(broken.memory_id), "outcome": "useful", "rating": 0.9},
    )
    trace.record(
        "memory_feedback",
        memory_id=str(broken.memory_id),
        outcome="useful",
        rating=0.9,
        result=result,
    )
    print(f"7. memory_feedback({broken.memory_id}, outcome='useful', rating=0.9)")

    result = await _call_tool(
        mcp,
        "memory_search",
        {**user, "query": "mcp dependency installed", "limit": 1},
    )
    trace.record("memory_search", query="mcp dependency installed", result=result)
    if result["status"] == "ok" and result["data"]["hits"]:
        hit = result["data"]["hits"][0]
        message = (
            f"8. memory_search('mcp dependency installed') -> {hit['content']} "
            f"(score={hit['score']:.3f})"
        )
        print(message)
    else:
        print("8. memory_search('mcp dependency installed') -> no hits")

    saved = trace.save()
    print(
        f"Trace captured: {len(trace.records)} records"
        + (f" -> {saved}" if saved else " (no file; pass --trace-out)")
    )
    return trace


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="evoeventmem-demo",
        description="Coding/Debug agent demo over the EvoEventMem MCP tools.",
    )
    parser.add_argument(
        "--trace-out",
        type=Path,
        default=None,
        help="write the JSONL session trace to this path",
    )
    args = parser.parse_args()
    asyncio.run(run_demo(trace_out=args.trace_out))


if __name__ == "__main__":
    main()
