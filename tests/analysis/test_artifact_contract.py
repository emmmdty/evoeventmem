"""C1: freeze B's artifact consumer contract.

These tests import B's producer models from ``benchmarks.common.artifacts`` and
validate minimal LongMemEval and LoCoMo payloads without redefining any producer
field. They also pin the dataset-neutral consumer row model in
``benchmarks/analysis/models.py`` to those producer schemas.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from benchmarks.analysis.models import AnalysisRow
from benchmarks.common.artifacts import (
    AblationRunManifest,
    ArtifactClass,
    BudgetSpec,
    ConsolidationAction,
    FinalizationRecord,
    GitState,
    PolicyVersions,
    ProviderIdentity,
    RunManifest,
    SourceFailure,
    TokenizerIdentity,
)


def provider(*, model_id: str) -> ProviderIdentity:
    return ProviderIdentity(
        kind="http",
        provider="deterministic_fake",
        model_id=model_id,
        version="v1",
        endpoint="http://fake",
    )


def manifest(
    *,
    dataset: str,
    methods: list[str],
    run_id: str = "run-1",
    artifact_class: ArtifactClass = ArtifactClass.PUBLICATION,
    expected_sample_ids: list[str] | None = None,
    expected_question_ids: list[str] | None = None,
) -> RunManifest:
    return RunManifest(
        run_id=run_id,
        artifact_class=artifact_class,
        dataset=dataset,
        dataset_path=f"data/{dataset}.json",
        dataset_hash=f"sha256:{dataset}",
        scope="publication",
        methods=methods,
        reader=provider(model_id="reader-model"),
        extractor=provider(model_id="extractor-model"),
        embedding=provider(model_id="embedding-model"),
        tokenizer=TokenizerIdentity(name="test-estimator", version="v1"),
        policies=PolicyVersions(
            extraction="ext.v1",
            router="router.v1",
            retrieval="retrieval.v1",
            consolidation="etec.v1",
        ),
        budget=BudgetSpec(input_tokens=4096),
        git=GitState(commit="deadbeef", dirty=False),
        config_hash="sha256:config",
        expected_sample_ids=expected_sample_ids or ["s1"],
        expected_question_ids=expected_question_ids or ["s1:qa:0"],
    )


def base_row(*, dataset: str, method: str, **overrides: object) -> dict:
    payload: dict = {
        "dataset": dataset,
        "sample_id": "s1",
        "question_id": "s1:qa:0",
        "run_id": "run-1",
        "method": method,
        "category": "1",
        "prediction": "blue house",
        "gold_answer": "blue house",
        "exact_match": 1.0,
        "token_f1": 1.0,
        "evidence_precision": 1.0,
        "evidence_recall": 1.0,
        "evidence_f1": 1.0,
        "content_tokens": 100,
        "prompt_overhead_tokens": 20,
        "total_input_tokens": 120,
        "packing_bound": False,
        "source_failures": [],
        "packed_item_count": 1,
        "consolidation_actions": [ConsolidationAction.KEEP],
        "reader_model": "reader-model",
        "extractor_model": "extractor-model",
        "embedding_model": "embedding-model",
        "tokenizer": "test-estimator",
        "policy_versions": PolicyVersions(
            extraction="ext.v1",
            router="router.v1",
            retrieval="retrieval.v1",
            consolidation="etec.v1",
        ),
        "config_hash": "sha256:config",
        "git_commit": "deadbeef",
        "manifest_hash": "sha256:manifest",
        "predictions_path": "runs/publication/predictions.jsonl",
        "samples_path": "runs/publication/samples.jsonl",
    }
    payload.update(overrides)
    return payload


def test_longmemeval_manifest_validates_without_redefining_fields() -> None:
    run = manifest(
        dataset="longmemeval",
        methods=["no_memory", "full_context", "vector_rag", "event_no_etec", "etec", "full"],
    )
    assert run.dataset == "longmemeval"
    assert run.manifest_hash().startswith("sha256:")
    assert ArtifactClass(run.artifact_class) is ArtifactClass.PUBLICATION


def test_locomo_manifest_validates_with_session_summary_method() -> None:
    run = manifest(
        dataset="locomo",
        methods=[
            "no_memory",
            "full_context",
            "session_summary",
            "vector_rag",
            "event_no_etec",
            "etec",
            "full",
        ],
    )
    assert "session_summary" in run.methods
    assert run.dataset == "locomo"


def test_ablation_run_manifest_validates() -> None:
    base = manifest(dataset="longmemeval", methods=["etec", "full"])
    ablation = AblationRunManifest(
        **base.model_dump(),
        ablation="evidence_policy",
        controlled_run_hash="sha256:controlled",
        base_run_hash=base.manifest_hash(),
        changed_factors=["evidence_policy"],
    )
    assert ablation.changed_factors == ["evidence_policy"]
    assert ablation.base_run_hash == base.manifest_hash()


def test_finalization_record_round_trips() -> None:
    record = FinalizationRecord(
        artifact_class=ArtifactClass.PUBLICATION,
        manifest_hash="sha256:manifest",
        required_hashes={"manifest.json": "sha256:manifest"},
        completion_counts={"samples": 1},
        finalized_at=datetime(2026, 8, 5, tzinfo=UTC),
    )
    assert record.finalization_hash().startswith("sha256:")
    assert record.format_version == 1


@pytest.mark.parametrize(
    ("dataset", "method"),
    [
        ("longmemeval", "etec"),
        ("longmemeval", "full"),
        ("locomo", "etec"),
        ("locomo", "full"),
        ("locomo", "session_summary"),
    ],
)
def test_analysis_row_consumes_producer_payloads(dataset: str, method: str) -> None:
    row = AnalysisRow.model_validate(base_row(dataset=dataset, method=method))
    assert row.dataset == dataset
    assert row.method == method
    assert row.consolidation_actions == [ConsolidationAction.KEEP]
    assert row.source_failures == []


def test_analysis_row_imports_producer_failure_and_action_types() -> None:
    row = AnalysisRow.model_validate(
        base_row(
            dataset="longmemeval",
            method="full",
            source_failures=[
                SourceFailure(
                    source="dense",
                    reason_code="dense_unavailable",
                    degraded_policy=True,
                    duration_ms=1.0,
                )
            ],
            consolidation_actions=[ConsolidationAction.MERGE, ConsolidationAction.SUPERSEDE],
        )
    )
    assert list(row.consolidation_actions) == [
        ConsolidationAction.MERGE,
        ConsolidationAction.SUPERSEDE,
    ]
    assert row.source_failures[0].degraded_policy is True


def test_analysis_row_requires_identifiers_and_hashes() -> None:
    for field in ("dataset", "sample_id", "question_id", "run_id", "method", "config_hash"):
        bad = base_row(dataset="longmemeval", method="etec")
        bad[field] = ""
        with pytest.raises(ValidationError):
            AnalysisRow.model_validate(bad)


def test_analysis_row_requires_artifact_locations() -> None:
    bad = base_row(dataset="locomo", method="etec")
    bad["predictions_path"] = ""
    with pytest.raises(ValidationError):
        AnalysisRow.model_validate(bad)