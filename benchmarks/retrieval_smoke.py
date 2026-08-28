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
from evoeventmem.domain.models import MemoryRecord, MemoryStatus
from evoeventmem.infra.in_memory_repository import InMemoryMemoryRepository
from evoeventmem.models.fakes import DeterministicFakeEmbeddingModel
from evoeventmem.retrieval import (
    RRF_K,
    EvidencePolicy,
    QEMRRetrievalResult,
    RetrievalControls,
    RetrievalHarness,
    RetrievalStrategy,
    RoutingMode,
    WeightProfile,
)
from evoeventmem.router import QueryIntent

ANNOTATIONS = Path("tests/fixtures/retrieval/m12_retrieval_smoke.json")
DEFAULT_OUTPUT_ROOT = Path("artifacts/m12_retrieval_smoke")
# S8: ``RetrievalStrategy`` grew to 6 members when S3 added the
# ``qemr_no_temporal`` / ``qemr_no_graph`` / ``qemr_uniform`` ablation
# arms. The retrieval smoke is a base-strategy contract (M12), not an
# ablation runner — only the three base strategies (fixed_vector,
# fixed_hybrid, qemr) are in scope. Pinning the list keeps the
# per-case record count at 3 (one per base strategy); using
# ``list(RetrievalStrategy)`` would produce 6 records per case and
# trip ``test_retrieval_smoke_excludes_synthetic_memory_without_evidence``.
STRATEGIES = [
    RetrievalStrategy.FIXED_VECTOR,
    RetrievalStrategy.FIXED_HYBRID,
    RetrievalStrategy.QEMR,
]
RECENCY_REFERENCE = datetime(2026, 8, 1, tzinfo=UTC)
WRRF_STRATEGIES = frozenset({RetrievalStrategy.FIXED_HYBRID, RetrievalStrategy.QEMR})


class RetrievalSmokeSummary(BaseModel):
    schema_version: str = "retrieval.smoke.v1"
    run_id: str = Field(min_length=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    git_commit: str = Field(min_length=1)
    git_dirty: bool
    annotation_path: str
    annotation_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    policy_name: str = Field(min_length=1)
    embedding_model_id: str = Field(min_length=1)
    sample_count: int = Field(ge=1)
    intent_accuracy: float = Field(ge=0.0, le=1.0)
    budget_compliance: float = Field(ge=0.0, le=1.0)
    provenance_coverage: float = Field(ge=0.0, le=1.0)
    decomposition_coverage: float = Field(ge=0.0, le=1.0)
    superseded_compliance: float = Field(ge=0.0, le=1.0)
    relevance_first_compliance: float = Field(ge=0.0, le=1.0)
    wrrf_component_coverage: float = Field(ge=0.0, le=1.0)
    fallback_event_coverage: float = Field(ge=0.0, le=1.0)
    controlled_switch_compliance: float = Field(ge=0.0, le=1.0)
    budget_breakdown_compliance: float = Field(ge=0.0, le=1.0)
    results_path: str


def main() -> None:
    parser = argparse.ArgumentParser(description="Run deterministic QEMR retrieval fixture smoke.")
    parser.add_argument("--annotation-path", type=Path, default=ANNOTATIONS)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    args = parser.parse_args()

    run_dir = _new_run_dir(args.output_root)
    summary = run_retrieval_smoke(args.annotation_path, run_dir)
    print(json.dumps(summary.model_dump(mode="json"), indent=2, sort_keys=True))


def run_retrieval_smoke(annotation_path: Path, output_dir: Path) -> RetrievalSmokeSummary:
    annotation_bytes = annotation_path.read_bytes()
    payload = json.loads(annotation_bytes)
    cases = _validated_cases(payload["cases"])
    git_commit = current_git_commit()
    git_dirty = _git_is_dirty()
    embedding_model = DeterministicFakeEmbeddingModel()
    results: list[dict[str, Any]] = []
    intent_correct = 0
    budget_compliant = 0
    provenance_checks = 0
    provenance_passes = 0
    decomposition_checks = 0
    decomposition_passes = 0
    superseded_compliant = 0
    relevance_checks = 0
    relevance_passes = 0
    wrrf_checks = 0
    wrrf_passes = 0
    fallback_checks = 0
    fallback_passes = 0
    budget_breakdown_checks = 0
    budget_breakdown_passes = 0

    for case in cases:
        for strategy in STRATEGIES:
            repository = InMemoryMemoryRepository()
            for item in case["memories"]:
                repository.add(MemoryRecord.model_validate(item))
            harness_cls: type[RetrievalHarness] = RetrievalHarness
            if case.get("fail_dense_source"):
                harness_cls = _FlakyDenseHarness
            harness = harness_cls(
                repository,
                embedding_model,
                clock=lambda: RECENCY_REFERENCE,
            )
            result = harness.retrieve(
                case["query"],
                user_id="u1",
                strategy=strategy,
                budget_tokens=case["budget_tokens"],
            )
            intent_correct += int(result.intent.value == case["expected_intent"])
            budget_ok = result.total_tokens <= result.budget_tokens
            budget_compliant += int(budget_ok)
            superseded_ok = all(
                item.memory.status is not MemoryStatus.SUPERSEDED or item.historical
                for item in result.selected_context
            )
            superseded_compliant += int(superseded_ok)
            for item in result.selected_context:
                provenance_checks += 1
                if item.evidence_refs:
                    provenance_passes += 1
                decomposition_checks += 1
                if item.component_scores and item.final_score >= 0.0:
                    decomposition_passes += 1
            if case.get("expected_top_memory_id") and strategy in WRRF_STRATEGIES:
                relevance_checks += 1
                if (
                    result.selected_context
                    and result.selected_context[0].memory.memory_id
                    == _uuid(case["expected_top_memory_id"])
                ):
                    relevance_passes += 1
            for candidate in result.candidates:
                for score in candidate.source_scores:
                    wrrf_checks += 1
                    if _wrrf_component_ok(strategy, score):
                        wrrf_passes += 1
            if case.get("fail_dense_source"):
                fallback_checks += 1
                if _dense_failure_ok(result):
                    fallback_passes += 1
            budget_breakdown_checks += 1
            if _budget_breakdown_ok(result):
                budget_breakdown_passes += 1
            results.append(_sample_record(case, strategy, result, budget_ok, superseded_ok))

    sample_count = len(cases) * len(STRATEGIES)
    switch_pairs, switch_deltas = _controlled_switch_deltas(cases, embedding_model)
    results_path = output_dir / "results.jsonl"
    summary_path = output_dir / "summary.json"
    write_jsonl_write_once(results_path, results)
    summary = RetrievalSmokeSummary(
        run_id=output_dir.name,
        git_commit=git_commit,
        git_dirty=git_dirty,
        annotation_path=str(annotation_path),
        annotation_sha256=hashlib.sha256(annotation_bytes).hexdigest(),
        policy_name=RetrievalHarness.POLICY_NAME,
        embedding_model_id=embedding_model.model_id,
        sample_count=sample_count,
        intent_accuracy=intent_correct / sample_count,
        budget_compliance=budget_compliant / sample_count,
        provenance_coverage=(
            provenance_passes / provenance_checks if provenance_checks else 0.0
        ),
        decomposition_coverage=(
            decomposition_passes / decomposition_checks if decomposition_checks else 0.0
        ),
        superseded_compliance=superseded_compliant / sample_count,
        relevance_first_compliance=(
            relevance_passes / relevance_checks if relevance_checks else 0.0
        ),
        wrrf_component_coverage=(
            wrrf_passes / wrrf_checks if wrrf_checks else 0.0
        ),
        fallback_event_coverage=(
            fallback_passes / fallback_checks if fallback_checks else 0.0
        ),
        controlled_switch_compliance=(
            switch_deltas / switch_pairs if switch_pairs else 0.0
        ),
        budget_breakdown_compliance=(
            budget_breakdown_passes / budget_breakdown_checks
            if budget_breakdown_checks
            else 0.0
        ),
        results_path=str(results_path),
    )
    write_json_write_once(summary_path, summary)
    return summary


def _sample_record(
    case: dict[str, Any],
    strategy: RetrievalStrategy,
    result: QEMRRetrievalResult,
    budget_ok: bool,
    superseded_ok: bool,
) -> dict[str, Any]:
    return {
        "case_id": case["case_id"],
        "query": case["query"],
        "expected_intent": case["expected_intent"],
        "intent": result.intent.value,
        "intent_match": result.intent.value == case["expected_intent"],
        "strategy": strategy.value,
        "budget_tokens": result.budget_tokens,
        "total_tokens": result.total_tokens,
        "budget": {
            "content_tokens": result.budget.content_tokens,
            "prompt_overhead_tokens": result.budget.prompt_overhead_tokens,
            "total_input_tokens_estimate": result.budget.total_input_tokens_estimate,
        },
        "estimator_name": result.estimator_name,
        "estimator_version": result.estimator_version,
        "budget_compliant": budget_ok,
        "superseded_compliant": superseded_ok,
        "selected_memory_ids": [str(item.memory.memory_id) for item in result.selected_context],
        "packed_items": [
            {
                "memory_id": str(item.memory.memory_id),
                "final_score": item.final_score,
                "component_scores": item.component_scores,
                "token_count": item.token_count,
                "evidence_refs": [
                    {
                        "source_type": ref.source_type,
                        "source_id": ref.source_id,
                        "locator": ref.locator,
                    }
                    for ref in item.evidence_refs
                ],
                "historical": item.historical,
                "reason": item.reason,
            }
            for item in result.selected_context
        ],
        "candidates": [
            {
                "memory_id": str(item.memory.memory_id),
                "final_score": item.final_score,
                "source_scores": [
                    {
                        "source": score.source.value,
                        "normalized_score": score.normalized_score,
                        "weighted_score": score.weighted_score,
                        "raw_score": score.raw_score,
                        "rank": score.rank,
                        "weight": score.weight,
                        "fusion_contribution": score.fusion_contribution,
                    }
                    for score in item.source_scores
                ],
                "historical": item.historical,
            }
            for item in result.candidates
        ],
        "source_failures": [
            {
                "source": event.source.value,
                "reason_code": event.reason_code,
                "degraded_policy": event.degraded_policy.value,
                "duration_ms": event.duration_ms,
            }
            for event in result.source_failures
        ],
        "exclusions": [
            {"memory_id": str(exclusion.memory_id), "reason": exclusion.reason}
            for exclusion in result.exclusions
        ],
    }


class _FlakyDenseHarness(RetrievalHarness):
    """Smoke harness whose dense source deterministically fails."""

    def _dense_candidates(
        self,
        query: str,
        routing: Any,
        memories: list[MemoryRecord],
        reference: datetime,
    ) -> list:
        raise RuntimeError("deterministic dense source failure")


def _wrrf_component_ok(
    strategy: RetrievalStrategy,
    score: Any,
) -> bool:
    if score.raw_score is None or score.weight is None or score.fusion_contribution is None:
        return False
    if strategy not in WRRF_STRATEGIES:
        return True
    if score.rank is None:
        return score.raw_score <= 0.0 and score.fusion_contribution == 0.0
    expected = score.weight / (RRF_K + score.rank)
    return score.rank >= 1 and abs(score.fusion_contribution - expected) <= 1e-9


def _uuid(value: str) -> Any:
    from uuid import UUID

    return UUID(value)


def _dense_failure_ok(result: QEMRRetrievalResult) -> bool:
    return any(
        event.source.value == "dense"
        and event.reason_code == "dense_source_error"
        and event.degraded_policy is EvidencePolicy.CONSTRAINED
        and event.duration_ms >= 0.0
        for event in result.source_failures
    )


def _budget_breakdown_ok(result: QEMRRetrievalResult) -> bool:
    return (
        bool(result.estimator_name)
        and bool(result.estimator_version)
        and result.budget.total_input_tokens_estimate
        == result.budget.content_tokens + result.budget.prompt_overhead_tokens
        and result.budget.total_input_tokens_estimate <= result.budget_tokens
        and sum(item.token_count for item in result.selected_context) == result.total_tokens
    )


def _decision_signature(result: QEMRRetrievalResult) -> tuple[object, object, object]:
    selected = tuple(item.memory.memory_id for item in result.selected_context)
    excluded = tuple(
        sorted(
            (exclusion.memory_id, exclusion.reason) for exclusion in result.exclusions
        )
    )
    ranked = tuple(
        (candidate.memory.memory_id, candidate.final_score)
        for candidate in sorted(result.candidates, key=lambda item: str(item.memory.memory_id))
    )
    return selected, excluded, ranked


def _controlled_switch_deltas(
    cases: list[dict[str, Any]],
    embedding_model: Any,
) -> tuple[int, int]:
    """Run one synthetic control pair per declared switch on the fixture.

    Every pair changes exactly one control while all other inputs stay equal;
    compliance requires at least one selection, exclusion, ranking, or packing
    decision to differ between the two runs.
    """
    cases_by_id = {case["case_id"]: case for case in cases}
    pairs: list[tuple[str, RetrievalControls, RetrievalControls]] = [
        (
            "controlled_evidence_policy",
            RetrievalControls(evidence_policy=EvidencePolicy.CONSTRAINED, budget_tokens=300),
            RetrievalControls(
                evidence_policy=EvidencePolicy.PROVENANCE_ONLY,
                budget_tokens=300,
            ),
        ),
        (
            "controlled_temporal_source",
            RetrievalControls(enable_temporal_source=True),
            RetrievalControls(enable_temporal_source=False),
        ),
        (
            "controlled_graph_source",
            RetrievalControls(enable_graph_source=True),
            RetrievalControls(enable_graph_source=False),
        ),
        (
            "controlled_forced_routing",
            RetrievalControls(routing_mode=RoutingMode.RULE),
            RetrievalControls(
                routing_mode=RoutingMode.FORCED,
                forced_intent=QueryIntent.NO_MEMORY,
            ),
        ),
        (
            "controlled_strategy",
            RetrievalControls(strategy=RetrievalStrategy.QEMR),
            RetrievalControls(strategy=RetrievalStrategy.FIXED_VECTOR),
        ),
        (
            "controlled_weight_profile",
            RetrievalControls(
                strategy=RetrievalStrategy.QEMR,
                weight_profile=WeightProfile.INTENT,
                budget_tokens=300,
            ),
            RetrievalControls(
                strategy=RetrievalStrategy.QEMR,
                weight_profile=WeightProfile.FIXED_HYBRID,
                budget_tokens=300,
            ),
        ),
        (
            "controlled_budget",
            RetrievalControls(budget_tokens=195),
            RetrievalControls(budget_tokens=215),
        ),
    ]
    pairs_checked = 0
    pairs_passing = 0
    for case_id, controls_a, controls_b in pairs:
        case = cases_by_id.get(case_id)
        if case is None:
            raise ValueError(f"controlled switch pair requires case {case_id}")
        repository = InMemoryMemoryRepository()
        for item in case["memories"]:
            repository.add(MemoryRecord.model_validate(item))
        harness = RetrievalHarness(
            repository,
            embedding_model,
            clock=lambda: RECENCY_REFERENCE,
        )
        result_a = harness.retrieve(case["query"], user_id="u1", controls=controls_a)
        result_b = harness.retrieve(case["query"], user_id="u1", controls=controls_b)
        pairs_checked += 1
        if _decision_signature(result_a) != _decision_signature(result_b):
            pairs_passing += 1
    return pairs_checked, pairs_passing


def _validated_cases(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise ValueError("retrieval smoke annotations must contain at least one case")
    cases: list[dict[str, Any]] = []
    for value_case in value:
        if not isinstance(value_case, dict):
            raise ValueError("each retrieval smoke case must be an object")
        case = dict(value_case)
        case_id = case.get("case_id", "<unknown>")
        query = case.get("query")
        if not isinstance(query, str) or not query:
            raise ValueError(f"{case_id}: query must be a non-empty string")
        try:
            QueryIntent(case.get("expected_intent"))
        except ValueError as error:
            raise ValueError(f"{case_id}: expected_intent is invalid") from error
        budget_tokens = case.get("budget_tokens")
        if not isinstance(budget_tokens, int) or budget_tokens < 1:
            raise ValueError(f"{case_id}: budget_tokens must be a positive integer")
        memories = case.get("memories")
        if not isinstance(memories, list) or not memories:
            raise ValueError(f"{case_id}: memories must be a non-empty list")
        for item in memories:
            MemoryRecord.model_validate(item)
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


def _new_run_dir(output_root: Path) -> Path:
    output_root.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(str(datetime.now(UTC).timestamp()).encode("utf-8")).hexdigest()[:12]
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir = output_root / f"retrieval-smoke-{timestamp}-{digest}"
    run_dir.mkdir()
    return run_dir


if __name__ == "__main__":
    main()
