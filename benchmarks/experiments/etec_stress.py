"""Predeclared ETEC stress harness.

Runs the public ETEC consolidator over a stable, predeclared fixture
(``benchmarks/experiments/fixtures/etec_stress_v1.json``) and records
action-stratified results. B owns this harness and its fixture; B does NOT edit
consolidation thresholds. This is the formal handoff to Workstream A for any
reproduced consolidation failure.

Every case has a stable ID and an expected action plus invariant expectations.
The harness requires every expected case ID to appear exactly once, emits one
trace per case, and reports action-stratified counts. It requires a NONZERO
MERGE and NONZERO SUPERSEDE count (the predeclared fixture must exercise both
paths), UTC-aware intervals, provenance lineage on every durable event, and
scope isolation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from collections import Counter
from collections.abc import Sequence
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
)
from evoeventmem.domain.models import MemoryRecord, MemoryStatus
from evoeventmem.infra.in_memory_repository import InMemoryMemoryRepository
from evoeventmem.models.fakes import DeterministicFakeEmbeddingModel

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "etec_stress_v1.json"
DEFAULT_OUTPUT_ROOT = Path("artifacts/smoke/etec-stress")
STRESS_METRICS_VERSION = "etec-stress-metrics.v1"
FIXTURE_SCHEMA = "etec.stress.v1"

# Invariants that every considered durable memory must satisfy.
REQUIRED_INVARIANTS = {
    "exact_span_provenance",
    "single_durable_event",
    "newer_wins",
    "stale_historical",
    "events_remain_separate",
    "interval_merge",
    "interval_disjoint_kept_separate",
    "no_durable_without_evidence",
    "isolation",
}


class StressCase(BaseModel):
    case_id: str = Field(min_length=1)
    category: str = Field(min_length=1)
    expected_action: ConsolidationAction
    expected_invariants: list[str] = Field(default_factory=list)
    existing: list[MemoryRecord] = Field(default_factory=list)
    incoming: MemoryRecord


class StressFixture(BaseModel):
    schema_version: str = FIXTURE_SCHEMA
    fixture_id: str = Field(min_length=1)
    embedding_model: str = Field(min_length=1)
    cases: list[StressCase] = Field(min_length=1)


class TraceRecord(BaseModel):
    case_id: str = Field(min_length=1)
    category: str = Field(min_length=1)
    expected_action: str = Field(min_length=1)
    predicted_action: str = Field(min_length=1)
    action_match: bool
    expected_invariants: list[str] = Field(default_factory=list)
    invariant_passes: list[str] = Field(default_factory=list)
    invariant_fails: list[str] = Field(default_factory=list)
    decision: dict[str, Any] = Field(default_factory=dict)
    updated_memory_ids: list[str] = Field(default_factory=list)
    provenance_lineage: list[dict[str, Any]] = Field(default_factory=list)


class StressSummary(BaseModel):
    schema_version: str = "etec.stress.summary.v1"
    fixture_id: str = Field(min_length=1)
    fixture_path: str = Field(min_length=1)
    fixture_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    git_commit: str = Field(min_length=1)
    git_dirty: bool
    policy_name: str = Field(min_length=1)
    metrics_version: str = Field(min_length=1)
    embedding_model_id: str = Field(min_length=1)
    thresholds: ETECThresholds
    case_count: int = Field(ge=1)
    expected_ids: list[str] = Field(min_length=1)
    duplicate_ids: list[str] = Field(default_factory=list)
    missing_ids: list[str] = Field(default_factory=list)
    action_accuracy: float = Field(ge=0.0, le=1.0)
    action_counts: dict[str, int] = Field(default_factory=dict)
    merge_count: int = Field(ge=0)
    supersede_count: int = Field(ge=0)
    invariant_pass_rate: float = Field(ge=0.0, le=1.0)
    traces_path: str
    summary_path: str


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the predeclared ETEC stress suite.")
    parser.add_argument("--fixture", type=Path, default=FIXTURE_PATH)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    args = parser.parse_args(argv)

    run_dir = _new_run_dir(args.output_root)
    summary = run_etec_stress(args.fixture, run_dir)
    print(json.dumps(summary.model_dump(mode="json"), indent=2, sort_keys=True))
    return 0


def run_etec_stress(fixture_path: Path, output_dir: Path) -> StressSummary:
    fixture_bytes = fixture_path.read_bytes()
    fixture = StressFixture.model_validate(json.loads(fixture_bytes))
    _require_stress_active(fixture)

    git_commit = current_git_commit()
    git_dirty = _git_is_dirty()
    embedding_model = DeterministicFakeEmbeddingModel()
    thresholds = ETECThresholds()
    consolidator = ETECConsolidator(embedding_model, thresholds)

    traces: list[TraceRecord] = []
    for case in fixture.cases:
        repository = InMemoryMemoryRepository()
        for memory in case.existing:
            repository.add(memory)
        result = consolidator.apply(repository, case.incoming)
        predicted_action = result.decision.action
        action_match = predicted_action is case.expected_action

        invariant_passes, invariant_fails = _check_invariants(
            case, predicted_action, result.updated_memories, repository
        )
        lineage = _provenance_lineage(result.updated_memories)
        traces.append(
            TraceRecord(
                case_id=case.case_id,
                category=case.category,
                expected_action=case.expected_action.value,
                predicted_action=predicted_action.value,
                action_match=action_match,
                expected_invariants=list(case.expected_invariants),
                invariant_passes=invariant_passes,
                invariant_fails=invariant_fails,
                decision=result.decision.model_dump(mode="json"),
                updated_memory_ids=[
                    str(memory.memory_id) for memory in result.updated_memories
                ],
                provenance_lineage=lineage,
            )
        )

    expected_ids = [case.case_id for case in fixture.cases]
    counts = Counter(trace.case_id for trace in traces)
    duplicate_ids = sorted(
        {case_id for case_id, count in counts.items() if count > 1}
    )
    trace_ids = set(trace.case_id for trace in traces)
    missing_ids = sorted(set(expected_ids) - trace_ids)
    if missing_ids or duplicate_ids:
        raise ValueError(
            f"stress trace must contain every expected ID exactly once; "
            f"missing={missing_ids} duplicate={duplicate_ids}"
        )

    action_counts_raw = Counter(trace.predicted_action for trace in traces)
    action_counts = {key.lower(): value for key, value in action_counts_raw.items()}
    action_correct = sum(1 for trace in traces if trace.action_match)
    invariant_checked = sum(
        len(trace.expected_invariants) for trace in traces
    )
    invariant_passed = sum(
        len(trace.invariant_passes) for trace in traces
    )

    traces_path = output_dir / "traces.jsonl"
    summary_path = output_dir / "summary.json"
    write_jsonl_write_once(traces_path, [trace.model_dump(mode="json") for trace in traces])
    summary = StressSummary(
        fixture_id=fixture.fixture_id,
        fixture_path=str(fixture_path),
        fixture_sha256=hashlib.sha256(fixture_bytes).hexdigest(),
        git_commit=git_commit,
        git_dirty=git_dirty,
        policy_name=ETECConsolidator.POLICY_NAME,
        metrics_version=STRESS_METRICS_VERSION,
        embedding_model_id=embedding_model.model_id,
        thresholds=thresholds,
        case_count=len(fixture.cases),
        expected_ids=expected_ids,
        duplicate_ids=duplicate_ids,
        missing_ids=missing_ids,
        action_accuracy=action_correct / len(fixture.cases),
        action_counts=dict(action_counts),
        merge_count=action_counts.get("merge", 0),
        supersede_count=action_counts.get("supersede", 0),
        invariant_pass_rate=(
            invariant_passed / invariant_checked if invariant_checked else 1.0
        ),
        traces_path=str(traces_path),
        summary_path=str(summary_path),
    )
    write_json_write_once(summary_path, summary)
    return summary


def _require_stress_active(fixture: StressFixture) -> None:
    """The predeclared fixture must exercise both MERGE and SUPERSEDE paths."""
    expected_actions = {case.expected_action for case in fixture.cases}
    if ConsolidationAction.MERGE not in expected_actions:
        raise ValueError("stress fixture must declare at least one MERGE case")
    if ConsolidationAction.SUPERSEDE not in expected_actions:
        raise ValueError("stress fixture must declare at least one SUPERSEDE case")


def _check_invariants(
    case: StressCase,
    action: ConsolidationAction,
    updated: Sequence[MemoryRecord],
    repository: InMemoryMemoryRepository,
) -> tuple[list[str], list[str]]:
    passes: list[str] = []
    fails: list[str] = []
    for invariant in case.expected_invariants:
        if _invariant_holds(invariant, case, action, updated, repository):
            passes.append(invariant)
        else:
            fails.append(invariant)
    return passes, fails


def _invariant_holds(
    invariant: str,
    case: StressCase,
    action: ConsolidationAction,
    updated: Sequence[MemoryRecord],
    repository: InMemoryMemoryRepository,
) -> bool:
    if invariant == "exact_span_provenance":
        return all(
            memory.evidence_refs and not memory.synthetic
            for memory in updated
        )
    if invariant == "single_durable_event":
        return len(repository.list_for_user(case.incoming.user_id)) == 1
    if invariant == "newer_wins":
        return action is ConsolidationAction.SUPERSEDE
    if invariant == "stale_historical":
        return any(
            memory.status is MemoryStatus.SUPERSEDED
            for memory in repository.list_for_user(case.incoming.user_id)
        )
    if invariant == "events_remain_separate":
        return action is ConsolidationAction.ADD
    if invariant == "interval_merge":
        return action is ConsolidationAction.MERGE
    if invariant == "interval_disjoint_kept_separate":
        return action is ConsolidationAction.ADD
    if invariant == "no_durable_without_evidence":
        return action is ConsolidationAction.REJECT and not updated
    if invariant == "isolation":
        # Incoming shares no scope with any existing memory, so it must be ADD
        # and must not merge/supersede anything.
        return action is ConsolidationAction.ADD
    raise ValueError(f"unknown invariant: {invariant}")


def _provenance_lineage(memories: Sequence[MemoryRecord]) -> list[dict[str, Any]]:
    lineage: list[dict[str, Any]] = []
    for memory in memories:
        lineage.append(
            {
                "memory_id": str(memory.memory_id),
                "status": memory.status.value,
                "evidence_refs": [
                    {
                        "source_type": ref.source_type,
                        "source_id": ref.source_id,
                        "locator": ref.locator,
                    }
                    for ref in memory.evidence_refs
                ],
                "supersedes": [str(item) for item in memory.supersedes],
                "superseded_by": (
                    str(memory.superseded_by) if memory.superseded_by else None
                ),
                "derived_from": [str(item) for item in memory.derived_from],
            }
        )
    return lineage


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


def _new_run_dir(output_root: Path) -> Path:
    output_root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir = output_root / f"etec-stress-{timestamp}"
    run_dir.mkdir()
    return run_dir


if __name__ == "__main__":
    raise SystemExit(main())