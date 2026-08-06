from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from pydantic import BaseModel
from pydantic_core import to_jsonable_python

from evoeventmem.core.ports import RequestScope
from evoeventmem.domain.models import (
    MemoryRecord,
    MemoryStatus,
    normalize_memory_content,
)

# Shared pure memory business rules.
#
# These functions carry only the deterministic decisions shared by the
# synchronous ``MemoryService`` and the async ``AsyncMemoryService``. They hold
# no repository or API state and no database clients, so both services call the
# same logic without duplicating business behavior.


def idempotency_key(memory: MemoryRecord, extractor_version: str) -> str:
    """Deterministic write identity for a candidate memory and extractor version."""
    evidence_payload = sorted(
        (
            {
                "source_type": ref.source_type,
                "source_id": ref.source_id,
                "locator": ref.locator,
                "quote": ref.quote,
                "metadata": _canonical_json_value(ref.metadata),
            }
            for ref in memory.evidence_refs
        ),
        key=_canonical_sort_key,
    )
    payload = {
        "extractor_version": extractor_version,
        "evidence_refs": evidence_payload,
        "candidate_identity": _candidate_identity(memory),
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"memory-write.v1:{digest}"


def legacy_write_identity(memory: MemoryRecord) -> tuple[str | None, str, str]:
    """Legacy collision identity: scope plus normalized content."""
    return (
        memory.tenant_id,
        memory.user_id,
        normalize_memory_content(memory.content),
    )


def collision_identity(
    memory: MemoryRecord, idempotency_key_value: str | None
) -> tuple[str | None, str, str | None]:
    """Durable collision identity: scope plus the write idempotency key."""
    return (memory.tenant_id, memory.user_id, idempotency_key_value)


def memory_idempotency_key(memory: MemoryRecord) -> str | None:
    """Read the persisted write idempotency key from a durable memory."""
    key = memory.metadata.get("write_idempotency_key")
    if isinstance(key, str):
        return key
    pipeline = memory.metadata.get("write_pipeline")
    if isinstance(pipeline, dict):
        pipeline_key = pipeline.get("idempotency_key")
        if isinstance(pipeline_key, str):
            return pipeline_key
    return None


def scope_key(memory: MemoryRecord) -> tuple[str | None, str, str | None]:
    return (memory.tenant_id, memory.user_id, memory.session_id)


def scope_matches_memory(
    memory: MemoryRecord,
    *,
    tenant_id: str | None,
    user_id: str | None,
    session_id: str | None = None,
) -> bool:
    """Exact scope agreement; presence of a constraint requires equality.

    Used to reject a record whose identity disagrees with the request scope
    without revealing whether a record exists under another scope.
    """
    return not (
        (tenant_id is not None and memory.tenant_id != tenant_id)
        or (user_id is not None and memory.user_id != user_id)
        or (session_id is not None and memory.session_id != session_id)
    )


def in_scope(
    memory: MemoryRecord,
    *,
    user_id: str | None,
    tenant_id: str | None,
) -> bool:
    return (user_id is None or memory.user_id == user_id) and (
        tenant_id is None or memory.tenant_id == tenant_id
    )


def scope_matches(memory: MemoryRecord, scope: RequestScope) -> bool:
    return (
        memory.tenant_id == scope.tenant_id
        and memory.user_id == scope.user_id
        and (scope.session_id is None or memory.session_id == scope.session_id)
    )


def apply_feedback(
    memory: MemoryRecord,
    *,
    outcome: str,
    rating: float | None,
    recorded_at: datetime,
    request_id: str | None,
) -> dict[str, Any]:
    """Return the model_copy update dict that appends a feedback event."""
    metadata = dict(memory.metadata)
    events = metadata.get("feedback_events")
    existing_events = [event for event in events if isinstance(event, dict)] if isinstance(
        events, list
    ) else []
    existing_events.append(
        {
            "outcome": outcome,
            "rating": rating,
            "recorded_at": recorded_at.isoformat(),
            "request_id": request_id,
        }
    )
    return {
        "metadata": {**metadata, "feedback_events": existing_events},
        "updated_at": recorded_at,
    }


def apply_forget(
    memory: MemoryRecord,
    *,
    forgotten_at: datetime,
    request_id: str | None,
) -> dict[str, Any]:
    """Return the model_copy update dict that marks a memory deleted."""
    metadata = dict(memory.metadata)
    metadata["forgotten_at"] = forgotten_at.isoformat()
    if request_id is not None:
        metadata["forget_request_id"] = request_id
    return {
        "status": MemoryStatus.DELETED,
        "metadata": metadata,
        "updated_at": forgotten_at,
    }


# ---------------------------------------------------------------------------
# Canonicalization helpers shared by the identity rules.
# ---------------------------------------------------------------------------


def _canonical_json_value(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _canonical_json_value(value.model_dump(mode="python"))
    if isinstance(value, Mapping):
        return {
            str(key): _canonical_json_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, set | frozenset):
        return sorted(
            (_canonical_json_value(item) for item in value),
            key=_canonical_sort_key,
        )
    if isinstance(value, list | tuple):
        return [_canonical_json_value(item) for item in value]
    return to_jsonable_python(value)


def _canonical_sort_key(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _candidate_identity(memory: MemoryRecord) -> dict[str, Any]:
    temporal = memory.model_dump(
        mode="json",
        include={"event_time", "valid_from", "valid_to"},
    )
    return {
        "memory_kind": memory.memory_kind.value,
        "normalized_content": normalize_memory_content(memory.content),
        "event_time": temporal["event_time"],
        "valid_from": temporal["valid_from"],
        "valid_to": temporal["valid_to"],
        "entities": sorted(
            (
                {
                    "entity_id": entity.entity_id,
                    "name": normalize_memory_content(entity.name),
                    "kind": entity.kind,
                    "role": entity.role,
                }
                for entity in memory.entities
            ),
            key=_canonical_sort_key,
        ),
        "roles": sorted(
            ([role_key, role_value] for role_key, role_value in memory.roles.items()),
            key=_canonical_sort_key,
        ),
        "relations": sorted(
            (relation.model_dump(mode="json") for relation in memory.relations),
            key=_canonical_sort_key,
        ),
        "fact_metadata": {
            field: {
                "present": field in memory.metadata,
                "value": _canonical_json_value(memory.metadata.get(field)),
            }
            for field in ("fact_slot", "fact_value", "multi_valued")
        },
    }