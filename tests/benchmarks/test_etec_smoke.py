from __future__ import annotations

import json
from pathlib import Path

from benchmarks.etec_smoke import _has_stale_active_contradiction, run_etec_smoke
from evoeventmem.domain.models import MemoryRecord

ANNOTATIONS = Path("tests/fixtures/consolidation/m10_etec_annotations.json")


def test_etec_smoke_matches_every_annotated_action_and_metrics(tmp_path: Path) -> None:
    summary = run_etec_smoke(ANNOTATIONS, tmp_path / "etec-smoke")
    decisions = _read_jsonl(Path(summary.decisions_path))

    assert decisions
    for decision in decisions:
        assert decision["predicted_action"] == decision["expected_action"], decision["case_id"]
    assert summary.sample_count == 8
    assert summary.merge_f1 == 1.0
    assert summary.conflict_accuracy == 1.0
    assert summary.provenance_coverage == 1.0
    assert summary.stale_memory_error == 0.0


def test_etec_smoke_decisions_retain_auditable_rule_inputs(tmp_path: Path) -> None:
    summary = run_etec_smoke(ANNOTATIONS, tmp_path / "etec-smoke")

    for record in _read_jsonl(Path(summary.decisions_path)):
        decision = record["decision"]
        assert decision["features"]
        assert decision["thresholds"]
        assert decision["rule_hits"]
        assert decision["reason"]


def test_stale_check_uses_public_fact_key_normalization() -> None:
    memories = [
        _memory(
            memory_id="65000000-0000-0000-0000-000000000001",
            fact_slot="Profile.City",
            fact_value="Seattle",
        ),
        _memory(
            memory_id="65000000-0000-0000-0000-000000000002",
            fact_slot="profile city",
            fact_value="Boston",
        ),
    ]

    assert _has_stale_active_contradiction(memories)


def test_stale_check_keeps_tenants_isolated() -> None:
    memories = [
        _memory(
            memory_id="66000000-0000-0000-0000-000000000001",
            fact_slot="profile.city",
            fact_value="Seattle",
            tenant_id="tenant-a",
        ),
        _memory(
            memory_id="66000000-0000-0000-0000-000000000002",
            fact_slot="profile.city",
            fact_value="Boston",
            tenant_id="tenant-b",
        ),
    ]

    assert not _has_stale_active_contradiction(memories)


def _memory(
    *,
    memory_id: str,
    fact_slot: str,
    fact_value: str,
    tenant_id: str | None = "tenant-a",
) -> MemoryRecord:
    return MemoryRecord.model_validate(
        {
            "memory_id": memory_id,
            "tenant_id": tenant_id,
            "user_id": "u1",
            "content": f"Caroline lives in {fact_value}.",
            "evidence_refs": [
                {
                    "source_type": "turn",
                    "source_id": memory_id,
                    "locator": "messages[0]",
                }
            ],
            "valid_from": "2024-01-01T00:00:00Z",
            "metadata": {"fact_slot": fact_slot, "fact_value": fact_value},
        }
    )


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
