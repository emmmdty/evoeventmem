from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import CallToolResult

from adapters.opencode.demo import run_demo
from adapters.opencode.mcp_server import build_server
from adapters.opencode.tracing import TraceCapture
from evoeventmem.core.ports import RequestScope, SearchHit
from evoeventmem.domain.models import (
    EntityRef,
    EvidenceRef,
    MemoryKind,
    MemoryRecord,
    MemoryStatus,
)
from evoeventmem.infra.async_embedding import EmbeddingModelError
from evoeventmem.infra.postgres_repository import RepositoryUnavailableError
from evoeventmem.services.memory_service import MemoryExplainResult

EXPECTED_TOOLS = {
    "memory_search",
    "memory_observe",
    "memory_explain",
    "memory_timeline",
    "memory_feedback",
    "memory_forget",
}


class FakeMemoryService:
    """Deterministic in-memory fake of the public memory service surface.

    Supports fault injection: setting ``fail_with`` makes every subsequent
    call raise the configured exception until it is cleared.
    """

    def __init__(self) -> None:
        self._memories: list[MemoryRecord] = []
        self._fail_with: Exception | None = None

    def fail_with(self, exc: Exception | None) -> None:
        self._fail_with = exc

    def seed(self, memory: MemoryRecord) -> MemoryRecord:
        self._memories.append(memory)
        return memory

    def _guard(self) -> None:
        if self._fail_with is not None:
            raise self._fail_with

    def _get(self, scope: RequestScope, memory_id: UUID) -> MemoryRecord | None:
        for memory in self._memories:
            if (
                memory.memory_id == memory_id
                and memory.tenant_id == scope.tenant_id
                and memory.user_id == scope.user_id
            ):
                return memory
        return None

    async def write(self, scope: RequestScope, memory: MemoryRecord) -> MemoryRecord:
        self._guard()
        for existing in self._memories:
            if (
                existing.tenant_id == scope.tenant_id
                and existing.user_id == scope.user_id
                and existing.normalized_content == memory.normalized_content
            ):
                return existing
        self._memories.append(memory)
        return memory

    async def search(
        self, scope: RequestScope, query: str, limit: int = 5
    ) -> list[SearchHit]:
        self._guard()
        query_tokens = {token.casefold() for token in query.split()}
        hits: list[SearchHit] = []
        for memory in self._memories:
            if memory.tenant_id != scope.tenant_id or memory.user_id != scope.user_id:
                continue
            if memory.status is MemoryStatus.DELETED:
                continue
            memory_tokens = {token.casefold() for token in memory.content.split()}
            overlap = query_tokens & memory_tokens
            if not overlap:
                continue
            union = query_tokens | memory_tokens
            hits.append(
                SearchHit(
                    memory=memory,
                    score=len(overlap) / len(union),
                    reason="fake-token-overlap",
                    source="fake",
                )
            )
        hits.sort(key=lambda hit: (-hit.score, str(hit.memory.memory_id)))
        return hits[:limit]

    async def explain(
        self, scope: RequestScope, memory_id: UUID
    ) -> MemoryExplainResult | None:
        self._guard()
        memory = self._get(scope, memory_id)
        if memory is None:
            return None
        linked_ids = set(memory.supersedes) | set(memory.derived_from)
        related = [
            item
            for item in self._memories
            if item.memory_id in linked_ids
            and item.tenant_id == scope.tenant_id
            and item.user_id == scope.user_id
        ]
        return MemoryExplainResult(memory=memory, related=related)

    async def feedback(
        self,
        scope: RequestScope,
        memory_id: UUID,
        *,
        outcome: str,
        rating: float | None = None,
        request_id: str | None = None,
    ) -> MemoryRecord | None:
        self._guard()
        memory = self._get(scope, memory_id)
        if memory is None:
            return None
        updated = memory.model_copy(
            update={
                "metadata": {
                    **memory.metadata,
                    "feedback_events": [{"outcome": outcome, "rating": rating}],
                }
            }
        )
        self._replace(updated)
        return updated

    async def forget(
        self,
        scope: RequestScope,
        memory_id: UUID,
        *,
        request_id: str | None = None,
    ) -> MemoryRecord | None:
        self._guard()
        memory = self._get(scope, memory_id)
        if memory is None:
            return None
        updated = memory.model_copy(
            update={
                "status": MemoryStatus.DELETED,
                "metadata": {**memory.metadata, "forgotten_at": "trace"},
            }
        )
        self._replace(updated)
        return updated

    def _replace(self, updated: MemoryRecord) -> None:
        self._memories = [
            updated if item.memory_id == updated.memory_id else item
            for item in self._memories
        ]


@pytest.fixture
def fake() -> FakeMemoryService:
    return FakeMemoryService()


@pytest.fixture
def server(fake: FakeMemoryService) -> FastMCP:
    return build_server(fake)


def _observe_args(*, observation: str, user: str = "test-user", **overrides: Any) -> dict[str, Any]:
    return {
        "observation": observation,
        "user_id": user,
        "source_type": "opencode-test",
        "source_id": "test-source:1",
        **overrides,
    }


def _memory_payload(
    *,
    content: str,
    memory_id: UUID | None = None,
    user: str = "test-user",
    tenant: str = "default",
    event_time: datetime | None = None,
    supersedes: list[UUID] | None = None,
    kind: MemoryKind = MemoryKind.FACT,
) -> MemoryRecord:
    return MemoryRecord(
        memory_id=memory_id or UUID(int=0),
        tenant_id=tenant,
        user_id=user,
        memory_kind=kind,
        content=content,
        entities=[EntityRef(name="topic")],
        evidence_refs=[
            EvidenceRef(
                source_type="fixture",
                source_id="seed:1",
                locator="tests/adapters/test_mcp.py",
                quote="seeded",
            )
        ],
        event_time=event_time,
        supersedes=supersedes or [],
    )


def _result_data(result: CallToolResult) -> dict[str, Any]:
    assert result.isError is False
    text = result.content[0].text if result.content else ""
    return json.loads(text)


async def _call(server: FastMCP, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    async with create_connected_server_and_client_session(server) as session:
        await session.initialize()
        result = await session.call_tool(tool, arguments)
        return _result_data(result)


async def _tools(server: FastMCP) -> dict[str, dict[str, Any]]:
    async with create_connected_server_and_client_session(server) as session:
        await session.initialize()
        result = await session.list_tools()
        return {tool.name: tool.inputSchema for tool in result.tools}


@pytest.mark.anyio
async def test_tool_surface_is_stable(server: FastMCP) -> None:
    schemas = await _tools(server)
    assert set(schemas) == EXPECTED_TOOLS
    assert "query" in schemas["memory_search"]["required"]
    assert "user_id" in schemas["memory_search"]["required"]
    assert "observation" in schemas["memory_observe"]["required"]
    assert "user_id" in schemas["memory_observe"]["required"]
    assert "memory_id" in schemas["memory_explain"]["required"]
    assert "user_id" in schemas["memory_explain"]["required"]
    assert "memory_id" in schemas["memory_feedback"]["required"]
    assert "memory_id" in schemas["memory_forget"]["required"]
    assert schemas["memory_search"]["properties"]["limit"]["minimum"] == 1


@pytest.mark.anyio
async def test_observe_then_search_returns_content_and_evidence(
    fake: FakeMemoryService, server: FastMCP
) -> None:
    observed = await _call(server, "memory_observe", _observe_args(
        observation="the project uses the npmmirror package registry",
    ))
    assert observed["status"] == "ok"
    memory = observed["data"]["memory"]
    memory_id = memory["memory_id"]
    assert memory["evidence"][0]["source_id"] == "test-source:1"
    assert memory["evidence"][0]["quote"] == "the project uses the npmmirror package registry"

    duplicated = await _call(server, "memory_observe", _observe_args(
        observation="the project uses the npmmirror package registry",
    ))
    assert duplicated["data"]["memory"]["memory_id"] == memory_id

    searched = await _call(server, "memory_search", {
        "query": "npmmirror package registry",
        "user_id": "test-user",
    })
    assert searched["status"] == "ok"
    hits = searched["data"]["hits"]
    assert len(hits) == 1
    assert hits[0]["memory_id"] == memory_id
    assert hits[0]["evidence"][0]["source_type"] == "opencode-test"
    assert hits[0]["source"] == "fake"


@pytest.mark.anyio
async def test_search_respects_user_scope(fake: FakeMemoryService, server: FastMCP) -> None:
    await _call(server, "memory_observe", _observe_args(
        observation="the project uses the npmmirror package registry",
    ))
    searched = await _call(server, "memory_search", {
        "query": "npmmirror",
        "user_id": "other-user",
    })
    assert searched["status"] == "ok"
    assert searched["data"]["hits"] == []


@pytest.mark.anyio
async def test_explain_returns_memory_and_related(fake: FakeMemoryService, server: FastMCP) -> None:
    earlier = fake.seed(_memory_payload(content="registry pinned to npm", event_time=None))
    switched = fake.seed(_memory_payload(
        content="registry switched to npmmirror",
        memory_id=UUID(int=1),
        supersedes=[earlier.memory_id],
        kind=MemoryKind.EVENT,
        event_time=datetime(2026, 8, 1, tzinfo=UTC),
    ))

    explained = await _call(server, "memory_explain", {
        "memory_id": str(switched.memory_id),
        "user_id": "test-user",
    })
    assert explained["status"] == "ok"
    assert explained["data"]["memory"]["memory_id"] == str(switched.memory_id)
    assert [item["memory_id"] for item in explained["data"]["related"]] == [
        str(earlier.memory_id)
    ]
    assert explained["data"]["related"][0]["evidence"][0]["quote"] == "seeded"


@pytest.mark.anyio
async def test_explain_missing_memory_returns_not_found(server: FastMCP) -> None:
    explained = await _call(server, "memory_explain", {
        "memory_id": str(UUID(int=999)),
        "user_id": "test-user",
    })
    assert explained["status"] == "not_found"


@pytest.mark.anyio
async def test_feedback_and_forget_lifecycle(fake: FakeMemoryService, server: FastMCP) -> None:
    observed = await _call(server, "memory_observe", _observe_args(
        observation="deployment is pinned to the eu-west-1 region",
    ))
    memory_id = observed["data"]["memory"]["memory_id"]

    feedback = await _call(server, "memory_feedback", {
        "memory_id": memory_id,
        "outcome": "useful",
        "rating": 0.9,
        "user_id": "test-user",
    })
    assert feedback["status"] == "ok"
    assert feedback["data"]["memory"]["metadata"]["feedback_events"] == [
        {"outcome": "useful", "rating": 0.9}
    ]

    forgotten = await _call(server, "memory_forget", {
        "memory_id": memory_id,
        "user_id": "test-user",
    })
    assert forgotten["status"] == "ok"
    assert forgotten["data"]["memory"]["status"] == "deleted"

    searched = await _call(server, "memory_search", {
        "query": "eu-west-1",
        "user_id": "test-user",
    })
    assert searched["data"]["hits"] == []


@pytest.mark.anyio
async def test_timeline_orders_events_by_event_time_and_puts_untimed_last(
    fake: FakeMemoryService, server: FastMCP
) -> None:
    first = datetime(2026, 7, 1, tzinfo=UTC)
    second = datetime(2026, 7, 2, tzinfo=UTC)
    third = datetime(2026, 7, 3, tzinfo=UTC)
    fake.seed(_memory_payload(
        content="topic discussed on july third", memory_id=UUID(int=3), event_time=third
    ))
    fake.seed(_memory_payload(
        content="topic discussed on july first", memory_id=UUID(int=1), event_time=first
    ))
    fake.seed(_memory_payload(
        content="topic note without a date", memory_id=UUID(int=4), event_time=None
    ))
    fake.seed(_memory_payload(
        content="topic discussed on july second", memory_id=UUID(int=2), event_time=second
    ))

    timeline = await _call(server, "memory_timeline", {
        "entity_or_topic": "topic",
        "user_id": "test-user",
        "limit": 10,
    })
    assert timeline["status"] == "ok"
    stamps = [event["event_time"] for event in timeline["data"]["events"]]
    assert stamps == [
        first.isoformat(),
        second.isoformat(),
        third.isoformat(),
        None,
    ]


@pytest.mark.anyio
async def test_store_outage_returns_clear_fallback() -> None:
    fake = FakeMemoryService()
    down = build_server(fake)
    fake.fail_with(RepositoryUnavailableError("postgres is down"))

    for tool, arguments in [
        ("memory_search", {"query": "npmmirror", "user_id": "test-user"}),
        ("memory_observe", _observe_args(observation="new observation")),
        ("memory_explain", {"memory_id": str(UUID(int=1)), "user_id": "test-user"}),
        ("memory_timeline", {"entity_or_topic": "topic", "user_id": "test-user"}),
        ("memory_feedback", {
            "memory_id": str(UUID(int=1)),
            "outcome": "useful",
            "user_id": "test-user",
        }),
        ("memory_forget", {"memory_id": str(UUID(int=1)), "user_id": "test-user"}),
    ]:
        result = await _call(down, tool, arguments)
        assert result["status"] == "unavailable", f"{tool} must fall back, got {result}"
        assert "proceed without memory" in result["message"]
        assert result["data"]["reason"] == "store_unavailable"

    fake.fail_with(None)
    recovered = await _call(down, "memory_search", {
        "query": "npmmirror",
        "user_id": "test-user",
    })
    assert recovered["status"] == "ok"


@pytest.mark.anyio
async def test_embedding_outage_returns_fallback() -> None:
    fake = FakeMemoryService()
    down = build_server(fake)
    fake.fail_with(EmbeddingModelError("embedding provider unreachable"))
    result = await _call(down, "memory_search", {
        "query": "anything",
        "user_id": "test-user",
    })
    assert result["status"] == "unavailable"
    assert result["data"]["reason"] == "embedding_unavailable"


@pytest.mark.anyio
async def test_invalid_memory_id_returns_error(server: FastMCP) -> None:
    explained = await _call(server, "memory_explain", {
        "memory_id": "not-a-uuid",
        "user_id": "test-user",
    })
    assert explained["status"] == "error"
    assert explained["data"]["reason"] == "invalid_request"

    feedback = await _call(server, "memory_feedback", {
        "memory_id": "not-a-uuid",
        "outcome": "useful",
        "user_id": "test-user",
    })
    assert feedback["status"] == "error"


@pytest.mark.anyio
async def test_schema_rejects_invalid_arguments(server: FastMCP) -> None:
    async with create_connected_server_and_client_session(server) as session:
        await session.initialize()
        result = await session.call_tool("memory_search", {"user_id": "test-user"})
        assert result.isError is True


@pytest.mark.anyio
async def test_demo_runs_captures_trace_and_shows_evidence(tmp_path: Path) -> None:
    trace_path = tmp_path / "demo.jsonl"
    trace = await run_demo(trace_out=trace_path)
    assert trace_path.exists()
    lines = [json.loads(line) for line in trace_path.read_text().splitlines()]
    assert lines
    assert len(trace.records) == len(lines)

    search_records = [record for record in lines if record["event"] == "memory_search"]
    assert search_records
    assert search_records[0]["result"]["status"] == "ok"
    hits = search_records[0]["result"]["data"]["hits"]
    assert hits
    assert hits[0]["evidence"], "demo hits must carry evidence references"

    actions = [record for record in lines if record["event"] == "agent_action"]
    assert actions and actions[0]["action"].startswith("Action:")


@pytest.mark.anyio
async def test_trace_capture_writes_jsonl(tmp_path: Path) -> None:
    capture = TraceCapture(tmp_path / "trace.jsonl")
    capture.record("step", memory_id=UUID(int=7))
    path = capture.save()
    assert path is not None and path.exists()
    parsed = json.loads(path.read_text())
    assert parsed["event"] == "step"
    assert parsed["memory_id"] == str(UUID(int=7))
    assert parsed["ts"]
