from __future__ import annotations

from datetime import UTC, datetime

from evoeventmem.core.ports import RequestScope
from evoeventmem.domain.models import EvidenceRef, MemoryKind, MemoryRecord
from evoeventmem.services import memory_rules


def _record(
    *,
    tenant_id: str | None = "tenant-1",
    user_id: str = "user-1",
    session_id: str | None = "session-1",
    content: str = "Caroline joined a support group.",
) -> MemoryRecord:
    event_time = datetime(2023, 5, 7, tzinfo=UTC)
    return MemoryRecord(
        tenant_id=tenant_id,
        user_id=user_id,
        session_id=session_id,
        memory_kind=MemoryKind.EVENT,
        content=content,
        evidence_refs=[
            EvidenceRef(
                source_type="turn",
                source_id="D1:1",
                locator="chars=0:43",
                quote="I went to an LGBTQ support group yesterday.",
            )
        ],
        event_time=event_time,
        valid_from=event_time,
    )


# ---------------------------------------------------------------------------
# Collision / idempotency identity
# ---------------------------------------------------------------------------


def test_idempotency_key_is_deterministic_and_scoped() -> None:
    first = memory_rules.idempotency_key(_record(), "rule.v1")
    second = memory_rules.idempotency_key(_record(), "rule.v1")
    assert first == second
    assert first.startswith("memory-write.v1:")


def test_idempotency_key_changes_with_extractor_version() -> None:
    assert memory_rules.idempotency_key(_record(), "rule.v1") != memory_rules.idempotency_key(
        _record(), "rule.v2"
    )


def test_idempotency_key_distinguishes_memory_semantics() -> None:
    base = _record()
    changed = _record().model_copy(
        update={"event_time": datetime(2023, 5, 8, tzinfo=UTC)}
    )
    assert memory_rules.idempotency_key(base, "rule.v1") != memory_rules.idempotency_key(
        changed, "rule.v1"
    )


def test_memory_idempotency_key_reads_metadata() -> None:
    memory = _record().model_copy(
        update={"metadata": {"write_idempotency_key": "memory-write.v1:abc"}}
    )
    assert memory_rules.memory_idempotency_key(memory) == "memory-write.v1:abc"


def test_memory_idempotency_key_reads_pipeline_metadata() -> None:
    memory = _record().model_copy(
        update={"metadata": {"write_pipeline": {"idempotency_key": "memory-write.v1:xyz"}}}
    )
    assert memory_rules.memory_idempotency_key(memory) == "memory-write.v1:xyz"


def test_memory_idempotency_key_returns_none_when_absent() -> None:
    assert memory_rules.memory_idempotency_key(_record()) is None


def test_legacy_write_identity_uses_tenant_user_and_normalized_content() -> None:
    identity = memory_rules.legacy_write_identity(_record(content="  CAROLINE Joined "))
    assert identity[0] == "tenant-1"
    assert identity[1] == "user-1"
    assert identity[2] == "caroline joined"


def test_collision_identity_uses_tenant_user_idempotency() -> None:
    memory = _record()
    collision = memory_rules.collision_identity(memory, "memory-write.v1:k")
    assert collision == ("tenant-1", "user-1", "memory-write.v1:k")


# ---------------------------------------------------------------------------
# Scope consistency
# ---------------------------------------------------------------------------


def test_scope_key_includes_tenant_user_and_session() -> None:
    assert memory_rules.scope_key(_record()) == ("tenant-1", "user-1", "session-1")


def test_scope_key_allows_none_tenant_and_session() -> None:
    memory = _record(tenant_id=None, session_id=None)
    assert memory_rules.scope_key(memory) == (None, "user-1", None)


def test_scope_matches_memory_agrees_on_identity() -> None:
    memory = _record()
    assert memory_rules.scope_matches_memory(
        memory, tenant_id="tenant-1", user_id="user-1", session_id="session-1"
    )


def test_scope_matches_memory_rejects_wrong_user() -> None:
    memory = _record()
    assert not memory_rules.scope_matches_memory(
        memory, tenant_id="tenant-1", user_id="user-2", session_id="session-1"
    )


def test_in_scope_ignores_missing_constraints() -> None:
    memory = _record()
    assert memory_rules.in_scope(memory, user_id=None, tenant_id=None)
    assert memory_rules.in_scope(memory, user_id="user-1", tenant_id=None)
    assert not memory_rules.in_scope(memory, user_id="user-2", tenant_id=None)
    assert not memory_rules.in_scope(memory, user_id=None, tenant_id="tenant-2")


def test_scope_disagreement_is_stable() -> None:
    memory = _record()
    assert memory_rules.scope_matches_memory(
        memory, tenant_id="tenant-2", user_id="user-1", session_id="session-1"
    ) is False


# ---------------------------------------------------------------------------
# Feedback transitions
# ---------------------------------------------------------------------------


def test_feedback_appends_event_and_bumps_updated_at() -> None:
    recorded_at = datetime(2024, 1, 1, 12, 0, tzinfo=UTC)
    updates = memory_rules.apply_feedback(
        _record(),
        outcome="useful",
        rating=0.9,
        recorded_at=recorded_at,
        request_id="req-1",
    )
    events = updates["metadata"]["feedback_events"]
    assert len(events) == 1
    assert events[0]["outcome"] == "useful"
    assert events[0]["rating"] == 0.9
    assert events[0]["request_id"] == "req-1"
    assert updates["updated_at"] == recorded_at


def test_feedback_keeps_existing_events() -> None:
    first = datetime(2024, 1, 1, 12, 0, tzinfo=UTC)
    second = datetime(2024, 1, 2, 12, 0, tzinfo=UTC)
    base = _record()
    updates = memory_rules.apply_feedback(
        base, outcome="useful", rating=0.9, recorded_at=first, request_id="req-1"
    )
    next_updates = memory_rules.apply_feedback(
        base.model_copy(update=updates),
        outcome="not-useful",
        rating=0.1,
        recorded_at=second,
        request_id="req-2",
    )
    assert len(next_updates["metadata"]["feedback_events"]) == 2


def test_feedback_preserves_other_metadata() -> None:
    memory = _record().model_copy(update={"metadata": {"source_dataset": "locomo"}})
    updates = memory_rules.apply_feedback(
        memory, outcome="useful", rating=1.0, recorded_at=datetime.now(UTC), request_id=None
    )
    assert updates["metadata"]["source_dataset"] == "locomo"


# ---------------------------------------------------------------------------
# Forget behavior
# ---------------------------------------------------------------------------


def test_forget_marks_deleted_and_records_time() -> None:
    forgotten_at = datetime(2024, 1, 1, 12, 0, tzinfo=UTC)
    updates = memory_rules.apply_forget(_record(), forgotten_at=forgotten_at, request_id="req-9")
    assert updates["status"].value == "deleted"
    assert updates["metadata"]["forgotten_at"] == forgotten_at.isoformat()
    assert updates["metadata"]["forget_request_id"] == "req-9"
    assert updates["updated_at"] == forgotten_at


def test_forget_omits_request_id_when_none() -> None:
    updates = memory_rules.apply_forget(
        _record(), forgotten_at=datetime.now(UTC), request_id=None
    )
    assert "forget_request_id" not in updates["metadata"]


# ---------------------------------------------------------------------------
# No cross-scope existence leak
# ---------------------------------------------------------------------------


def test_scope_matches_request_scope_is_isolation_predicate() -> None:
    memory = _record()
    scope = RequestScope(tenant_id="tenant-1", user_id="user-1", session_id="session-1")
    assert memory_rules.scope_matches(memory, scope)
    wrong = RequestScope(tenant_id="tenant-1", user_id="user-2")
    assert not memory_rules.scope_matches(memory, wrong)