from __future__ import annotations

import json

from benchmarks.analysis.loaders import load_base_run
from benchmarks.analysis.models import AnalysisRow
from benchmarks.analysis.taxonomy import (
    FailureCategory,
    FailureType,
    answer_tokens,
    build_review_rows,
    build_review_sheet_rows,
    classify_failure,
    classify_failure_type,
    evidence_mapping_gap,
    gold_token_recall,
    review_coverage,
    stratified_failure_sample,
    write_review_sheet,
)
from benchmarks.common.artifacts import ConsolidationAction, PolicyVersions, SourceFailure


def _classify(**kwargs) -> FailureCategory:
    defaults = {
        "method": "full",
        "category": "single-hop",
        "gold_answer": "blue house",
        "prediction": "green car",
        "context_text": "blue house words",
        "context_truncated": False,
    }
    defaults.update(kwargs)
    return classify_failure(**defaults)


def test_adversarial_is_classified_before_recoverability() -> None:
    assert (
        _classify(category="adversarial", gold_answer=None, prediction="I do not know")
        is FailureCategory.ADVERSARIAL_NO_GOLD
    )


def test_recoverable_answer_wrong_prediction() -> None:
    assert (
        _classify(context_text="the answer is blue house here")
        is FailureCategory.ANSWER_RECOVERABLE_WRONG
    )


def test_unrecoverable_answer() -> None:
    assert (
        _classify(context_text="completely unrelated tokens")
        is FailureCategory.ANSWER_NOT_RECOVERABLE
    )


def test_empty_prediction() -> None:
    assert _classify(prediction="") is FailureCategory.EMPTY_PREDICTION


def test_no_gold_answer() -> None:
    assert _classify(gold_answer=None) is FailureCategory.NO_GOLD_ANSWER


def test_no_memory_baseline() -> None:
    assert _classify(method="no_memory") is FailureCategory.NO_MEMORY_BASELINE


def test_context_truncation() -> None:
    assert _classify(context_truncated=True) is FailureCategory.CONTEXT_BUDGET_TRUNCATION


def test_gold_token_recall() -> None:
    assert gold_token_recall("blue house", "blue house here") == 1.0
    assert gold_token_recall("blue house", "nothing related") == 0.0
    assert gold_token_recall(None, "anything") is None


def test_answer_tokens_normalize_punctuation_and_articles() -> None:
    assert answer_tokens("The Blue, house!") == ["blue", "house"]


def test_evidence_mapping_gap() -> None:
    assert evidence_mapping_gap(["D0:1"], []) is True
    assert evidence_mapping_gap([], []) is False
    assert evidence_mapping_gap(["D0:1"], ["D0:1"]) is False


def test_build_review_rows_emits_only_failures() -> None:
    questions = [
        {
            "question_id": f"q{index}",
            "sample_id": "s1",
            "category": "single-hop",
            "gold_answer": "blue house",
            "prediction": "green car" if index % 2 else "blue house",
            "exact_match": 0.0 if index % 2 else 1.0,
            "gold_evidence": ["D0:1"],
            "predicted_evidence": [],
            "context_text": "blue house words",
            "context_truncated": False,
        }
        for index in range(20)
    ]
    rows = build_review_rows(
        run_id="run-1", config_hash="sha256:c", method="full", questions=questions
    )
    assert len(rows) == 10
    assert all(row["failure_category"] for row in rows)
    assert all(row["evidence_mapping_gap"] is True for row in rows)


def test_build_review_rows_counts_at_least_fifty() -> None:
    questions = [
        {
            "question_id": f"q{index}",
            "sample_id": "s1",
            "category": "single-hop",
            "gold_answer": "blue house",
            "prediction": "green car",
            "exact_match": 0.0,
            "gold_evidence": ["D0:1"],
            "predicted_evidence": [],
            "context_text": "unrelated text",
            "context_truncated": False,
        }
        for index in range(60)
    ]
    rows = build_review_rows(
        run_id="run-1", config_hash="sha256:c", method="full", questions=questions
    )
    assert len(rows) == 60
    assert all(
        row["failure_category"] == FailureCategory.ANSWER_NOT_RECOVERABLE.value for row in rows
    )


def test_write_review_sheet_writes_jsonl(tmp_path) -> None:
    rows = [
        {
            "run_id": "run-1",
            "config_hash": "sha256:c",
            "method": "full",
            "question_id": "q1",
            "failure_category": "answer_recoverable_wrong_prediction",
        }
    ]
    path = tmp_path / "review.jsonl"
    write_review_sheet(rows, path)
    loaded = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert loaded == rows


# --------------------------------------------------------------------------- #
# C6 typed taxonomy, stratified sampling, review coverage.
# --------------------------------------------------------------------------- #


def _row(**overrides) -> AnalysisRow:
    payload: dict = {
        "dataset": "locomo",
        "sample_id": "s1",
        "question_id": "s1:qa:0",
        "run_id": "run-1",
        "method": "full",
        "category": "single-hop",
        "prediction": "wrong answer",
        "gold_answer": "blue house",
        "exact_match": 0.0,
        "token_f1": 0.0,
        "evidence_precision": 0.0,
        "evidence_recall": 0.0,
        "evidence_f1": 0.0,
        "content_tokens": 100,
        "prompt_overhead_tokens": 20,
        "total_input_tokens": 120,
        "packing_bound": False,
        "source_failures": [],
        "packed_item_count": 3,
        "context_text": "blue house words",
        "intent": "semantic",
        "candidate_count": 20,
        "exclusion_reasons": [],
        "extraction_rejection_reasons": [],
        "consolidation_actions": [ConsolidationAction.KEEP],
        "reader_model": "reader",
        "extractor_model": "extractor",
        "embedding_model": "embedding",
        "tokenizer": "estimator",
        "policy_versions": PolicyVersions(
            extraction="ext.v1", router="r.v1", retrieval="ret.v1", consolidation="etec.v1"
        ),
        "config_hash": "sha256:c",
        "git_commit": "deadbeef",
        "manifest_hash": "sha256:m",
        "predictions_path": "p.jsonl",
        "samples_path": "s.jsonl",
    }
    payload.update(overrides)
    return AnalysisRow.model_validate(payload)


def test_failure_types_are_exactly_the_declared_nine() -> None:
    assert set(FailureType) == {
        FailureType.EXTRACTION_PROVENANCE_REJECTION,
        FailureType.ROUTER_CLASSIFICATION_FALLBACK,
        FailureType.CANDIDATE_GENERATION_MISS,
        FailureType.TEMPORAL_FILTERING_RANKING_ERROR,
        FailureType.EVIDENCE_CONSTRAINT_EXCLUSION,
        FailureType.BUDGET_TRUNCATION,
        FailureType.ANSWER_ABSENT_FROM_PACKED_CONTEXT,
        FailureType.ANSWER_PRESENT_READER_WRONG,
        FailureType.ADVERSARIAL_NO_ANSWER,
    }


def test_classify_success_returns_none() -> None:
    assert classify_failure_type(_row(exact_match=1.0)) is None


def test_classify_adversarial_no_answer() -> None:
    assert (
        classify_failure_type(
            _row(category="adversarial", gold_answer=None, prediction="I do not know")
        )
        is FailureType.ADVERSARIAL_NO_ANSWER
    )
    assert classify_failure_type(_row(gold_answer="")) is FailureType.ADVERSARIAL_NO_ANSWER


def test_classify_extraction_provenance_rejection() -> None:
    row = _row(
        method="etec",
        extraction_rejection_reasons=["no_exact_span"],
        context_text="completely unrelated",
    )
    assert classify_failure_type(row) is FailureType.EXTRACTION_PROVENANCE_REJECTION
    # the raw-turn baseline has no extraction input, so rejection traces do
    # not apply and the failure falls through to answer recoverability
    assert (
        classify_failure_type(
            _row(
                method="vector_rag",
                extraction_rejection_reasons=["no_exact_span"],
                context_text="",
            )
        )
        is FailureType.ANSWER_ABSENT_FROM_PACKED_CONTEXT
    )


def test_classify_router_classification_fallback() -> None:
    row = _row(
        source_failures=[
            SourceFailure(
                source="dense",
                reason_code="dense_unavailable",
                degraded_policy=True,
                duration_ms=1.0,
            )
        ]
    )
    assert classify_failure_type(row) is FailureType.ROUTER_CLASSIFICATION_FALLBACK


def test_classify_candidate_generation_miss() -> None:
    assert classify_failure_type(_row(candidate_count=0)) is FailureType.CANDIDATE_GENERATION_MISS


def test_classify_temporal_filtering_ranking_error() -> None:
    row = _row(
        intent="temporal",
        exclusion_reasons=["temporal_filtered"],
        context_text="blue house words",
    )
    assert classify_failure_type(row) is FailureType.TEMPORAL_FILTERING_RANKING_ERROR


def test_classify_evidence_constraint_exclusion() -> None:
    row = _row(
        exclusion_reasons=["evidence_missing_refs"],
        context_text="blue house words",
    )
    assert classify_failure_type(row) is FailureType.EVIDENCE_CONSTRAINT_EXCLUSION


def test_classify_budget_truncation() -> None:
    row = _row(packing_bound=True, context_text="blue house words")
    assert classify_failure_type(row) is FailureType.BUDGET_TRUNCATION


def test_classify_answer_absent_from_packed_context() -> None:
    row = _row(context_text="completely unrelated tokens")
    assert classify_failure_type(row) is FailureType.ANSWER_ABSENT_FROM_PACKED_CONTEXT


def test_classify_answer_present_reader_wrong() -> None:
    assert classify_failure_type(_row()) is FailureType.ANSWER_PRESENT_READER_WRONG


def test_classification_is_trace_based_and_llm_free() -> None:
    source = (
        __import__("pathlib").Path("benchmarks/analysis/taxonomy.py").read_text(encoding="utf-8")
    )
    for forbidden in ("openai", "llm", "judge", "anthropic", "chat"):
        assert forbidden not in source.lower(), f"taxonomy must be LLM-free ({forbidden})"


def test_build_review_sheet_rows_from_loaded_run(analysis_fixture) -> None:
    loaded = load_base_run(analysis_fixture["locomo"]["run_dir"])
    sheet = build_review_sheet_rows(loaded.rows)
    assert sheet
    for row in sheet:
        assert row["failure_type"]
        assert row["automatic_label_hypothesis"] is True
        assert row["reviewer_label"] == ""
        assert row["reviewer_comment"] == ""
        assert row["reviewed_at"] is None
        assert row["run_id"] == loaded.run_id
        assert row["config_hash"] == loaded.manifest.config_hash
        assert row["exact_match"] == 0.0
        assert "trace" in row


def test_stratified_sample_takes_at_least_fifty_or_all() -> None:
    failures = [
        {
            "dataset": d,
            "method": m,
            "category": c,
            "failure_type": t.value,
            "question_id": f"{d}:{m}:{c}:{t.value}:{i}",
        }
        for d in ("longmemeval", "locomo")
        for m in ("full", "etec")
        for c in ("single-hop", "temporal-reasoning")
        for t in (FailureType.BUDGET_TRUNCATION, FailureType.ANSWER_PRESENT_READER_WRONG)
        for i in range(4)
    ]
    sample, summary = stratified_failure_sample(failures, target_min=50)
    assert summary["failure_total"] == 64
    assert summary["all_failures_sampled"] is False
    assert len(sample) == 50
    assert len({row["question_id"] for row in sample}) == 50


def test_stratified_sample_takes_all_when_fewer() -> None:
    failures = [
        {
            "dataset": "locomo",
            "method": "full",
            "category": "single-hop",
            "failure_type": FailureType.BUDGET_TRUNCATION.value,
            "question_id": f"q{i}",
        }
        for i in range(10)
    ]
    sample, summary = stratified_failure_sample(failures, target_min=50)
    assert len(sample) == 10
    assert summary["all_failures_sampled"] is True


def test_stratified_sample_covers_all_strata() -> None:
    failures = [
        {
            "dataset": "locomo",
            "method": method,
            "category": "single-hop",
            "failure_type": FailureType.ANSWER_ABSENT_FROM_PACKED_CONTEXT.value,
            "question_id": f"{method}:q{i}",
        }
        for method in ("full", "etec", "vector_rag")
        for i in range(30)
    ]
    sample, _summary = stratified_failure_sample(failures, target_min=50)
    methods_in_sample = {row["method"] for row in sample}
    assert methods_in_sample == {"full", "etec", "vector_rag"}


def test_stratified_sample_is_deterministic() -> None:
    failures = [
        {
            "dataset": d,
            "method": m,
            "category": c,
            "failure_type": t.value,
            "question_id": f"{d}:{m}:{c}:{t.value}:{i}",
        }
        for d in ("longmemeval", "locomo")
        for m in ("full", "etec")
        for c in ("single-hop", "temporal-reasoning")
        for t in (FailureType.BUDGET_TRUNCATION, FailureType.ANSWER_PRESENT_READER_WRONG)
        for i in range(4)
    ]
    first, _ = stratified_failure_sample(failures, target_min=50)
    second, _ = stratified_failure_sample(list(reversed(failures)), target_min=50)
    assert first == second


def test_review_coverage_computed_separately_from_automatic_labels() -> None:
    failures = [
        {
            "dataset": "locomo",
            "method": "full",
            "category": "single-hop",
            "failure_type": FailureType.BUDGET_TRUNCATION.value,
            "question_id": f"q{i}",
        }
        for i in range(20)
    ]
    sample, _summary = stratified_failure_sample(failures, target_min=10)
    coverage = review_coverage(sample, failures)
    assert coverage["sample_size"] == 10
    assert coverage["failure_total"] == 20
    assert coverage["sampled_fraction"] == 0.5
    assert coverage["reviewed_count"] == 0
    # simulating a human review changes only the coverage, not the sample
    sample[0]["reviewer_label"] = "confirmed"
    coverage = review_coverage(sample, failures)
    assert coverage["reviewed_count"] == 1
    assert coverage["reviewed_fraction"] == 0.1


def test_fixture_failures_cover_multiple_types(analysis_fixture) -> None:
    loaded = load_base_run(analysis_fixture["locomo"]["run_dir"])
    types = {row["failure_type"] for row in build_review_sheet_rows(loaded.rows)}
    assert FailureType.ADVERSARIAL_NO_ANSWER.value in types
    assert FailureType.ANSWER_PRESENT_READER_WRONG.value in types
    assert len(types) >= 2
