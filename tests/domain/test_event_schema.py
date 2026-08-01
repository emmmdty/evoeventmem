from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID

import pytest

from evoeventmem.domain.models import (
    EntityRef,
    EvidenceRef,
    MemoryKind,
    MemoryRecord,
    MemoryStatus,
    RelationRef,
)


def evidence() -> EvidenceRef:
    return EvidenceRef(source_type="turn", source_id="session-1:turn-2", locator="messages[2]")


def test_durable_memory_requires_evidence_unless_synthetic() -> None:
    with pytest.raises(
        ValueError,
        match="durable memories require at least one evidence reference",
    ):
        MemoryRecord(user_id="u1", content="The user prefers UTC timestamps.")

    synthetic = MemoryRecord(
        user_id="u1",
        content="Synthetic calibration memory.",
        synthetic=True,
    )

    assert synthetic.evidence_refs == []
    assert synthetic.synthetic is True


def test_invalid_temporal_intervals_are_rejected() -> None:
    with pytest.raises(ValueError, match="valid_to must not be earlier than valid_from"):
        MemoryRecord(
            user_id="u1",
            content="The user lived in Seattle.",
            valid_from=datetime(2024, 2, 1, tzinfo=UTC),
            valid_to=datetime(2024, 1, 1, tzinfo=UTC),
            evidence_refs=[evidence()],
        )


def test_temporal_fields_reject_naive_values_and_normalize_to_utc() -> None:
    with pytest.raises(ValueError, match="event_time must be timezone-aware"):
        MemoryRecord(
            user_id="u1",
            content="The user moved.",
            event_time=datetime(2024, 1, 1),
            evidence_refs=[evidence()],
        )

    offset = timezone(timedelta(hours=8))
    memory = MemoryRecord(
        user_id="u1",
        content="The user joined a Taipei call.",
        event_time=datetime(2024, 1, 1, 8, 30, tzinfo=offset),
        valid_from=datetime(2024, 1, 1, 8, 0, tzinfo=offset),
        evidence_refs=[evidence()],
    )

    assert memory.event_time == datetime(2024, 1, 1, 0, 30, tzinfo=UTC)
    assert memory.valid_from == datetime(2024, 1, 1, 0, 0, tzinfo=UTC)


def test_status_and_supersession_links_are_consistent() -> None:
    replacement_id = UUID("11111111-1111-1111-1111-111111111111")
    old_id = UUID("22222222-2222-2222-2222-222222222222")

    with pytest.raises(ValueError, match="superseded memories must identify superseded_by"):
        MemoryRecord(
            user_id="u1",
            content="Old preference.",
            status=MemoryStatus.SUPERSEDED,
            evidence_refs=[evidence()],
        )

    active = MemoryRecord(
        user_id="u1",
        content="Updated preference.",
        status=MemoryStatus.ACTIVE,
        supersedes=[old_id],
        evidence_refs=[evidence()],
    )
    superseded = MemoryRecord(
        memory_id=old_id,
        user_id="u1",
        content="Old preference.",
        status=MemoryStatus.SUPERSEDED,
        superseded_by=replacement_id,
        evidence_refs=[evidence()],
    )

    assert active.supersedes == [old_id]
    assert superseded.superseded_by == replacement_id


def test_entities_relations_derivation_and_legacy_aliases_serialize_to_json() -> None:
    source_id = UUID("33333333-3333-3333-3333-333333333333")
    memory = MemoryRecord(
        user_id="u1",
        kind="event",
        content="Alice deployed the release.",
        entities=["Alice", EntityRef(name="release", kind="artifact")],
        roles={"Alice": "actor"},
        relations=[RelationRef(source="Alice", predicate="deployed", target="release")],
        evidence=[evidence()],
        derived_from=[source_id],
    )

    payload = memory.to_json_dict()

    assert memory.memory_kind is MemoryKind.EVENT
    assert memory.kind is MemoryKind.EVENT
    assert memory.evidence == memory.evidence_refs
    assert memory.normalized_content == "alice deployed the release."
    assert payload["schema_version"] == "memory.v1"
    assert payload["memory_kind"] == "event"
    assert payload["entities"] == [
        {"entity_id": None, "name": "Alice", "kind": None, "role": None},
        {"entity_id": None, "name": "release", "kind": "artifact", "role": None},
    ]
    assert payload["relations"][0]["predicate"] == "deployed"
    assert payload["evidence_refs"][0]["source_id"] == "session-1:turn-2"
    assert payload["derived_from"] == [str(source_id)]
    assert datetime.fromisoformat(payload["created_at"].replace("Z", "+00:00")).tzinfo == UTC


def test_normalized_content_is_always_derived_from_content() -> None:
    memory = MemoryRecord(
        user_id="u1",
        content="  Straße\t\n  Update  ",
        normalized_content="caller supplied stale value",
        evidence_refs=[evidence()],
    )

    assert memory.normalized_content == "strasse update"
