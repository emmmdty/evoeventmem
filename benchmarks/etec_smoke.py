from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from benchmarks.common.artifacts import (
    current_git_commit,
    write_json_write_once,
    write_jsonl_write_once,
)
from evoeventmem.consolidation import (
    ConsolidationAction,
    ETECConsolidator,
    ETECThresholds,
    fact_slot_key,
    fact_value_key,
)
from evoeventmem.domain.models import MemoryRecord, MemoryStatus
from evoeventmem.infra.in_memory_repository import InMemoryMemoryRepository
from evoeventmem.models.fakes import DeterministicFakeEmbeddingModel

ANNOTATIONS = Path("tests/fixtures/consolidation/m10_etec_annotations.json")
DEFAULT_OUTPUT_ROOT = Path("artifacts/m10_etec_smoke")
METRICS_VERSION = "etec-smoke-metrics.v2"


class ETECSmokeSummary(BaseModel):
    schema_version: str = "etec.smoke.v2"
    run_id: str = Field(min_length=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    git_commit: str = Field(min_length=1)
    git_dirty: bool
    annotation_path: str
    annotation_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    metrics_version: str = Field(min_length=1)
    policy_name: str = Field(min_length=1)
    embedding_model_id: str = Field(min_length=1)
    thresholds: ETECThresholds
    sample_count: int = Field(ge=1)
    action_accuracy: float = Field(ge=0.0, le=1.0)
    target_accuracy: float = Field(ge=0.0, le=1.0)
    merge_f1: float = Field(ge=0.0, le=1.0)
    conflict_accuracy: float = Field(ge=0.0, le=1.0)
    provenance_coverage: float = Field(ge=0.0, le=1.0)
    stale_memory_error: float = Field(ge=0.0, le=1.0)
    decisions_path: str


def main() -> None:
    parser = argparse.ArgumentParser(description="Run deterministic ETEC fixture smoke evaluation.")
    parser.add_argument("--annotation-path", type=Path, default=ANNOTATIONS)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    args = parser.parse_args()

    run_dir = _new_run_dir(args.output_root)
    summary = run_etec_smoke(args.annotation_path, run_dir)
    print(json.dumps(summary.model_dump(mode="json"), indent=2, sort_keys=True))


def run_etec_smoke(annotation_path: Path, output_dir: Path) -> ETECSmokeSummary:
    annotation_bytes = annotation_path.read_bytes()
    payload = json.loads(annotation_bytes)
    cases = _validated_cases(payload["cases"])
    git_commit = current_git_commit()
    git_dirty = _git_is_dirty()
    embedding_model = DeterministicFakeEmbeddingModel()
    thresholds = ETECThresholds()
    consolidator = ETECConsolidator(embedding_model, thresholds)
    decisions: list[dict[str, Any]] = []
    merge_expected: list[bool] = []
    merge_predicted: list[bool] = []
    action_correct = 0
    target_correct = 0
    conflict_correct = 0
    provenance_checks = 0
    provenance_passes = 0
    stale_errors = 0

    for case in cases:
        repository = InMemoryMemoryRepository()
        for item in case["existing"]:
            repository.add(MemoryRecord.model_validate(item))
        incoming = MemoryRecord.model_validate(case["incoming"])
        result = consolidator.apply(repository, incoming)
        action = result.decision.action
        predicted_target_memory_id = (
            str(result.decision.target_memory_id)
            if result.decision.target_memory_id is not None
            else None
        )
        expected_target_memory_id = case.get("expected_target_memory_id")
        action_match = action.value == case["expected_action"]
        target_match = predicted_target_memory_id == expected_target_memory_id

        merge_expected.append(bool(case["merge_gold"]))
        merge_predicted.append(action is ConsolidationAction.MERGE)
        action_correct += int(action_match)
        target_correct += int(target_match)
        if (action is ConsolidationAction.SUPERSEDE) == bool(case["conflict_gold"]):
            conflict_correct += 1
        for memory in result.updated_memories:
            provenance_checks += 1
            if memory.evidence_refs and "etec" in memory.metadata:
                provenance_passes += 1
        if _has_stale_active_contradiction(repository.list_for_user(incoming.user_id)):
            stale_errors += 1

        decisions.append(
            {
                "case_id": case["case_id"],
                "expected_action": case["expected_action"],
                "predicted_action": action.value,
                "action_match": action_match,
                "expected_target_memory_id": expected_target_memory_id,
                "target_memory_id": predicted_target_memory_id,
                "target_match": target_match,
                "decision": result.decision.model_dump(mode="json"),
                "updated_memory_ids": [str(memory.memory_id) for memory in result.updated_memories],
            }
        )

    decisions_path = output_dir / "decisions.jsonl"
    summary_path = output_dir / "summary.json"
    write_jsonl_write_once(decisions_path, decisions)
    summary = ETECSmokeSummary(
        run_id=output_dir.name,
        git_commit=git_commit,
        git_dirty=git_dirty,
        annotation_path=str(annotation_path),
        annotation_sha256=hashlib.sha256(annotation_bytes).hexdigest(),
        metrics_version=METRICS_VERSION,
        policy_name=ETECConsolidator.POLICY_NAME,
        embedding_model_id=embedding_model.model_id,
        thresholds=thresholds,
        sample_count=len(cases),
        action_accuracy=action_correct / len(cases),
        target_accuracy=target_correct / len(cases),
        merge_f1=_binary_f1(merge_expected, merge_predicted),
        conflict_accuracy=conflict_correct / len(cases),
        provenance_coverage=provenance_passes / provenance_checks if provenance_checks else 0.0,
        stale_memory_error=stale_errors / len(cases),
        decisions_path=str(decisions_path),
    )
    write_json_write_once(summary_path, summary)
    return summary


def _binary_f1(expected: list[bool], predicted: list[bool]) -> float:
    true_positive = sum(1 for gold, pred in zip(expected, predicted, strict=True) if gold and pred)
    false_positive = sum(
        1 for gold, pred in zip(expected, predicted, strict=True) if not gold and pred
    )
    false_negative = sum(
        1 for gold, pred in zip(expected, predicted, strict=True) if gold and not pred
    )
    denominator = 2 * true_positive + false_positive + false_negative
    if denominator == 0:
        return 0.0
    return (2 * true_positive) / denominator


def _validated_cases(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise ValueError("ETEC smoke annotations must contain at least one case")
    cases: list[dict[str, Any]] = []
    for value_case in value:
        if not isinstance(value_case, dict):
            raise ValueError("each ETEC smoke case must be an object")
        case = dict(value_case)
        case_id = case.get("case_id", "<unknown>")
        try:
            expected_action = ConsolidationAction(case.get("expected_action"))
        except ValueError as error:
            raise ValueError(f"{case_id}: expected_action is invalid") from error
        for field, action in (
            ("merge_gold", ConsolidationAction.MERGE),
            ("conflict_gold", ConsolidationAction.SUPERSEDE),
        ):
            gold = case.get(field)
            if not isinstance(gold, bool) or gold != (expected_action is action):
                raise ValueError(f"{case_id}: {field} is inconsistent with expected_action")
        expected_target = case.get("expected_target_memory_id")
        if expected_target is not None and not isinstance(expected_target, str):
            raise ValueError(f"{case_id}: expected_target_memory_id must be a string or null")
        cases.append(case)
    return cases


def _git_is_dirty() -> bool:
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return True
    return bool(result.stdout.strip())


def _has_stale_active_contradiction(memories: list[MemoryRecord]) -> bool:
    active = [
        memory
        for memory in memories
        if memory.status is MemoryStatus.ACTIVE
        and memory.valid_to is None
        and memory.metadata.get("multi_valued") is not True
    ]
    for index, left in enumerate(active):
        for right in active[index + 1 :]:
            slot = fact_slot_key(left)
            if (
                slot is not None
                and left.tenant_id == right.tenant_id
                and left.user_id == right.user_id
                and slot == fact_slot_key(right)
                and fact_value_key(left) != fact_value_key(right)
            ):
                return True
    return False


def _new_run_dir(output_root: Path) -> Path:
    output_root.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(str(datetime.now(UTC).timestamp()).encode("utf-8")).hexdigest()[:12]
    run_dir = output_root / f"etec-smoke-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{digest}"
    run_dir.mkdir()
    return run_dir


if __name__ == "__main__":
    main()
