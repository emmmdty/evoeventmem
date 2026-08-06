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
    QEMRRetrievalResult,
    RetrievalHarness,
    RetrievalStrategy,
)
from evoeventmem.router import QueryIntent

ANNOTATIONS = Path("tests/fixtures/retrieval/m12_retrieval_smoke.json")
DEFAULT_OUTPUT_ROOT = Path("artifacts/m12_retrieval_smoke")
STRATEGIES = list(RetrievalStrategy)
RECENCY_REFERENCE = datetime(2026, 8, 1, tzinfo=UTC)


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

    for case in cases:
        for strategy in STRATEGIES:
            repository = InMemoryMemoryRepository()
            for item in case["memories"]:
                repository.add(MemoryRecord.model_validate(item))
            harness = RetrievalHarness(
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
            results.append(_sample_record(case, strategy, result, budget_ok, superseded_ok))

    sample_count = len(cases) * len(STRATEGIES)
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
                    }
                    for score in item.source_scores
                ],
                "historical": item.historical,
            }
            for item in result.candidates
        ],
        "exclusions": [
            {"memory_id": str(exclusion.memory_id), "reason": exclusion.reason}
            for exclusion in result.exclusions
        ],
    }


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
