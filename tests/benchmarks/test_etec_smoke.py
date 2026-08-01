from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest

from benchmarks import etec_smoke
from benchmarks.etec_smoke import (
    _binary_f1,
    _has_stale_active_contradiction,
    run_etec_smoke,
)
from evoeventmem.consolidation import ETECConsolidator, ETECThresholds
from evoeventmem.domain.models import MemoryRecord
from evoeventmem.models.fakes import DeterministicFakeEmbeddingModel

ANNOTATIONS = Path("tests/fixtures/consolidation/m10_etec_annotations.json")


def test_etec_smoke_matches_every_annotated_action_and_metrics(tmp_path: Path) -> None:
    summary = run_etec_smoke(ANNOTATIONS, tmp_path / "etec-smoke")
    decisions = _read_jsonl(Path(summary.decisions_path))

    assert decisions
    for decision in decisions:
        assert decision["predicted_action"] == decision["expected_action"], decision["case_id"]
    assert summary.sample_count == 8
    assert summary.action_accuracy == 1.0
    assert summary.target_accuracy == 1.0
    assert summary.merge_f1 == 1.0
    assert summary.conflict_accuracy == 1.0
    assert summary.provenance_coverage == 1.0
    assert summary.stale_memory_error == 0.0
    assert all(decision["action_match"] is True for decision in decisions)
    assert all(decision["target_match"] is True for decision in decisions)


def test_etec_smoke_decisions_retain_auditable_rule_inputs(tmp_path: Path) -> None:
    summary = run_etec_smoke(ANNOTATIONS, tmp_path / "etec-smoke")

    for record in _read_jsonl(Path(summary.decisions_path)):
        decision = record["decision"]
        assert decision["features"]
        assert decision["thresholds"]
        assert decision["rule_hits"]
        assert decision["reason"]


def test_etec_smoke_counts_mismatched_action_label(tmp_path: Path) -> None:
    payload = _fixture_payload()
    payload["cases"][0]["expected_action"] = "REJECT"
    annotation_path = _write_annotations(tmp_path / "action-mismatch.json", payload)

    summary = run_etec_smoke(annotation_path, tmp_path / "action-mismatch")
    decisions = _read_jsonl(Path(summary.decisions_path))

    assert summary.action_accuracy == 7 / 8
    assert decisions[0]["action_match"] is False


def test_etec_smoke_counts_mismatched_target_label_including_none(tmp_path: Path) -> None:
    payload = _fixture_payload()
    payload["cases"][0]["expected_target_memory_id"] = (
        "69000000-0000-0000-0000-000000000001"
    )
    annotation_path = _write_annotations(tmp_path / "target-mismatch.json", payload)

    summary = run_etec_smoke(annotation_path, tmp_path / "target-mismatch")
    decisions = _read_jsonl(Path(summary.decisions_path))

    assert summary.target_accuracy == 7 / 8
    assert decisions[0]["expected_target_memory_id"] == (
        "69000000-0000-0000-0000-000000000001"
    )
    assert decisions[0]["target_match"] is False


@pytest.mark.parametrize("gold_field", ["merge_gold", "conflict_gold"])
def test_etec_smoke_rejects_inconsistent_binary_action_labels(
    tmp_path: Path,
    gold_field: str,
) -> None:
    payload = _fixture_payload()
    payload["cases"][0][gold_field] = True
    annotation_path = _write_annotations(tmp_path / f"bad-{gold_field}.json", payload)

    with pytest.raises(ValueError, match=gold_field):
        run_etec_smoke(annotation_path, tmp_path / f"bad-{gold_field}")


def test_etec_smoke_rejects_empty_cases(tmp_path: Path) -> None:
    annotation_path = _write_annotations(tmp_path / "empty.json", {"cases": []})

    with pytest.raises(ValueError, match="at least one case"):
        run_etec_smoke(annotation_path, tmp_path / "empty")


def test_binary_f1_without_positive_labels_or_predictions_is_zero() -> None:
    assert _binary_f1([False, False], [False, False]) == 0.0


def test_all_reject_smoke_has_non_vacuous_zero_output_metrics(tmp_path: Path) -> None:
    payload = _fixture_payload()
    payload["cases"] = [payload["cases"][3]]
    annotation_path = _write_annotations(tmp_path / "all-reject.json", payload)

    summary = run_etec_smoke(annotation_path, tmp_path / "all-reject")

    assert summary.merge_f1 == 0.0
    assert summary.provenance_coverage == 0.0


def test_annotation_fingerprint_hashes_exact_file_bytes(tmp_path: Path) -> None:
    annotation_bytes = ANNOTATIONS.read_bytes()
    first_path = tmp_path / "first.json"
    second_path = tmp_path / "second.json"
    first_path.write_bytes(annotation_bytes)
    second_path.write_bytes(annotation_bytes + b"\n")

    first = run_etec_smoke(first_path, tmp_path / "first")
    second = run_etec_smoke(second_path, tmp_path / "second")

    assert first.annotation_sha256 == hashlib.sha256(annotation_bytes).hexdigest()
    assert second.annotation_sha256 == hashlib.sha256(annotation_bytes + b"\n").hexdigest()
    assert first.annotation_sha256 != second.annotation_sha256


def test_etec_smoke_records_reproducible_policy_and_git_provenance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected_commit = "a" * 40
    monkeypatch.setattr(etec_smoke, "current_git_commit", lambda: expected_commit)
    monkeypatch.setattr(etec_smoke, "_git_is_dirty", lambda: False)

    summary = run_etec_smoke(ANNOTATIONS, tmp_path / "provenance")

    assert summary.metrics_version == "etec-smoke-metrics.v2"
    assert summary.policy_name == ETECConsolidator.POLICY_NAME
    assert summary.embedding_model_id == DeterministicFakeEmbeddingModel().model_id
    assert summary.thresholds == ETECThresholds()
    assert summary.git_commit == expected_commit
    assert summary.git_dirty is False


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


def _fixture_payload() -> dict[str, object]:
    return deepcopy(json.loads(ANNOTATIONS.read_text(encoding="utf-8")))


def _write_annotations(path: Path, payload: dict[str, object]) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path
