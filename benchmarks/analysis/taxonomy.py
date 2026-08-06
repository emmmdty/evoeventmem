"""Typed failure taxonomy and stratified human-review handoff (C6).

C6 replaces the coarse M15 categories with a typed, trace-based taxonomy:

- extraction/provenance rejection;
- router classification/fallback;
- candidate-generation miss;
- temporal filtering/ranking error;
- evidence-constraint exclusion;
- budget truncation;
- answer absent from packed context;
- answer present but reader wrong;
- adversarial/no-answer.

Classification is deterministic and trace-based: it consumes normalized
``AnalysisRow`` fields plus the retrieval/extraction traces the loaders
attach (``source_failures``, ``packing_bound``, ``intent``,
``candidate_count``, ``exclusion_reasons``, ``extraction_rejection_reasons``,
packed ``context_text``). No model-based evaluation is ever invoked.

The review sheet is a deterministic stratified sample across dataset, method,
category, and failure type: at least ``target_min`` failures, or all failures
when fewer exist. Automatic labels are explicit hypotheses: every row carries
blank ``reviewer_label`` / ``reviewer_comment`` / ``reviewed_at`` fields, and
human review coverage is computed separately from the automatic labels.
"""

from __future__ import annotations

import json
import re
import string
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from enum import StrEnum
from typing import Any

from benchmarks.analysis.models import AnalysisRow

ANSWER_RECOVERABLE_RECALL = 0.5

_ARTICLES = re.compile(r"\b(a|an|the)\b", flags=re.IGNORECASE)
_PUNCTUATION = str.maketrans("", "", string.punctuation)

EVENT_METHODS = frozenset({"event_no_etec", "etec", "full"})
ROUTER_FALLBACK_CODES = ("router_fallback", "dense_unavailable", "fallback", "degraded")


class FailureCategory(StrEnum):
    """Legacy M15 failure categories (kept for historical diagnostics)."""

    ADVERSARIAL_NO_GOLD = "adversarial_no_gold_answer"
    ANSWER_NOT_RECOVERABLE = "answer_not_recoverable_from_context"
    ANSWER_RECOVERABLE_WRONG = "answer_recoverable_wrong_prediction"
    CONTEXT_BUDGET_TRUNCATION = "context_budget_truncation"
    EMPTY_PREDICTION = "empty_prediction"
    NO_GOLD_ANSWER = "no_gold_answer"
    NO_MEMORY_BASELINE = "no_memory_baseline"
    OTHER = "other"


class FailureType(StrEnum):
    """C6 typed failure taxonomy (exact nine categories)."""

    EXTRACTION_PROVENANCE_REJECTION = "extraction_provenance_rejection"
    ROUTER_CLASSIFICATION_FALLBACK = "router_classification_fallback"
    CANDIDATE_GENERATION_MISS = "candidate_generation_miss"
    TEMPORAL_FILTERING_RANKING_ERROR = "temporal_filtering_ranking_error"
    EVIDENCE_CONSTRAINT_EXCLUSION = "evidence_constraint_exclusion"
    BUDGET_TRUNCATION = "budget_truncation"
    ANSWER_ABSENT_FROM_PACKED_CONTEXT = "answer_absent_from_packed_context"
    ANSWER_PRESENT_READER_WRONG = "answer_present_reader_wrong"
    ADVERSARIAL_NO_ANSWER = "adversarial_no_answer"


def gold_token_recall(gold_answer: str | None, context_text: str) -> float | None:
    gold = answer_tokens(gold_answer)
    context = answer_tokens(context_text)
    if not gold:
        return None
    if not context:
        return 0.0
    overlap = sum((Counter(gold) & Counter(context)).values())
    return overlap / len(gold)


def answer_tokens(text: str | None) -> list[str]:
    if not text:
        return []
    normalized = text.lower().translate(_PUNCTUATION)
    normalized = _ARTICLES.sub(" ", normalized)
    return normalized.split()


# --------------------------------------------------------------------------- #
# C6 trace-based classification.
# --------------------------------------------------------------------------- #


def classify_failure_type(row: AnalysisRow) -> FailureType | None:
    """Classify one row deterministically from its normalized traces.

    Returns ``None`` for non-failures (``exact_match > 0``). Protocol-level
    causes (adversarial/no-answer) are decided first, then pipeline causes in
    the documented taxonomy order.
    """
    if row.exact_match > 0.0:
        return None
    if row.category == "adversarial" or not (row.gold_answer or "").strip():
        return FailureType.ADVERSARIAL_NO_ANSWER
    if row.method in EVENT_METHODS and row.extraction_rejection_reasons:
        return FailureType.EXTRACTION_PROVENANCE_REJECTION
    if any(
        failure.degraded_policy or failure.reason_code in ROUTER_FALLBACK_CODES
        for failure in row.source_failures
    ):
        return FailureType.ROUTER_CLASSIFICATION_FALLBACK
    if row.candidate_count is not None and row.candidate_count == 0:
        return FailureType.CANDIDATE_GENERATION_MISS
    if any("temporal" in reason for reason in row.exclusion_reasons):
        return FailureType.TEMPORAL_FILTERING_RANKING_ERROR
    if any("evidence" in reason for reason in row.exclusion_reasons):
        return FailureType.EVIDENCE_CONSTRAINT_EXCLUSION
    if row.packing_bound:
        return FailureType.BUDGET_TRUNCATION
    recall = gold_token_recall(row.gold_answer, row.context_text)
    if recall is None or recall < ANSWER_RECOVERABLE_RECALL:
        return FailureType.ANSWER_ABSENT_FROM_PACKED_CONTEXT
    return FailureType.ANSWER_PRESENT_READER_WRONG


def _review_row(row: AnalysisRow, failure_type: FailureType) -> dict[str, Any]:
    return {
        "dataset": row.dataset,
        "run_id": row.run_id,
        "config_hash": row.config_hash,
        "method": row.method,
        "question_id": row.question_id,
        "sample_id": row.sample_id,
        "category": row.category,
        "failure_type": failure_type.value,
        # Automatic labels are explicit hypotheses until a human reviews them.
        "automatic_label_hypothesis": True,
        "reviewer_label": "",
        "reviewer_comment": "",
        "reviewed_at": None,
        "gold_answer": row.gold_answer,
        "prediction": row.prediction,
        "exact_match": row.exact_match,
        "answer_recall_in_context": gold_token_recall(row.gold_answer, row.context_text),
        "context_token_count": len(row.context_text.split()),
        "packing_bound": row.packing_bound,
        "intent": row.intent,
        "candidate_count": row.candidate_count,
        "trace": {
            "source_failures": [failure.model_dump(mode="json") for failure in row.source_failures],
            "exclusion_reasons": list(row.exclusion_reasons),
            "extraction_rejection_reasons": list(row.extraction_rejection_reasons),
        },
    }


def build_review_sheet_rows(rows: Sequence[AnalysisRow]) -> list[dict[str, Any]]:
    """Build handoff rows for every failed question of a loaded run."""
    sheet: list[dict[str, Any]] = []
    for row in rows:
        failure_type = classify_failure_type(row)
        if failure_type is None:
            continue
        sheet.append(_review_row(row, failure_type))
    return sheet


# --------------------------------------------------------------------------- #
# Deterministic stratified sampling and review coverage.
# --------------------------------------------------------------------------- #


def _stratum_key(row: Mapping[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(row.get("dataset") or ""),
        str(row.get("method") or ""),
        str(row.get("category") or ""),
        str(row.get("failure_type") or ""),
    )


def stratified_failure_sample(
    failures: Sequence[Mapping[str, Any]],
    *,
    target_min: int = 50,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Deterministic stratified sample across dataset/method/category/failure type.

    Returns at least ``target_min`` failures, or all failures when fewer
    exist. Ordering and strata are fully deterministic (no randomness).
    """
    ordered = sorted(
        (dict(failure) for failure in failures),
        key=lambda row: (*_stratum_key(row), str(row.get("question_id") or "")),
    )
    total = len(ordered)
    if total <= target_min:
        return ordered, {"sample_size": total, "failure_total": total, "all_failures_sampled": True}

    strata: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
    for row in ordered:
        strata.setdefault(_stratum_key(row), []).append(row)
    sample: list[dict[str, Any]] = []
    while len(sample) < target_min:
        for key in list(strata):
            bucket = strata[key]
            if not bucket:
                continue
            sample.append(bucket.pop(0))
            if len(sample) == target_min:
                break
    return sample, {
        "sample_size": len(sample),
        "failure_total": total,
        "all_failures_sampled": False,
    }


def review_coverage(
    sample: Sequence[Mapping[str, Any]],
    failures: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Coverage of the sampled handoff against the failure population.

    Computed separately from the automatic labels: human review is measured
    only through the blank ``reviewer_label`` field becoming non-empty.
    """
    by_field = ("dataset", "method", "category", "failure_type")

    def counts(rows: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, int]]:
        result: dict[str, dict[str, int]] = {}
        for field in by_field:
            result[field] = dict(Counter(str(row.get(field) or "") for row in rows))
        return result

    reviewed = sum(1 for row in sample if (row.get("reviewer_label") or "").strip())
    return {
        "sample_size": len(sample),
        "failure_total": len(failures),
        "sampled_fraction": len(sample) / len(failures) if failures else 0.0,
        "population": counts(failures),
        "sample": counts(sample),
        "reviewed_count": reviewed,
        "reviewed_fraction": reviewed / len(sample) if sample else 0.0,
    }


# --------------------------------------------------------------------------- #
# Legacy M15 API (historical diagnostics only; superseded by C6).
# --------------------------------------------------------------------------- #


def classify_failure(
    *,
    method: str,
    category: str | None,
    gold_answer: str | None,
    prediction: str | None,
    context_text: str,
    context_truncated: bool = False,
) -> FailureCategory:
    """Legacy classifier over per-question dicts (M15); kept for old reports."""
    if method == "no_memory":
        return FailureCategory.NO_MEMORY_BASELINE
    if category == "adversarial":
        return FailureCategory.ADVERSARIAL_NO_GOLD
    if gold_answer is None or not gold_answer.strip():
        return FailureCategory.NO_GOLD_ANSWER
    if context_truncated:
        return FailureCategory.CONTEXT_BUDGET_TRUNCATION
    if prediction is None or not prediction.strip():
        return FailureCategory.EMPTY_PREDICTION
    recall = gold_token_recall(gold_answer, context_text)
    if recall is None:
        return FailureCategory.NO_GOLD_ANSWER
    if recall < ANSWER_RECOVERABLE_RECALL:
        return FailureCategory.ANSWER_NOT_RECOVERABLE
    return FailureCategory.ANSWER_RECOVERABLE_WRONG


def context_text_for_memory_method(packed_items: Sequence[Mapping[str, Any]]) -> str:
    return " ".join(str(item["content"]) for item in packed_items)


def evidence_mapping_gap(gold_evidence: Sequence[Any], predicted_evidence: Sequence[Any]) -> bool:
    return bool(gold_evidence) and not predicted_evidence


def build_review_rows(
    *,
    run_id: str,
    config_hash: str,
    method: str,
    questions: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Legacy review-sheet rows for one method of one legacy run (M15)."""
    rows: list[dict[str, Any]] = []
    for question in questions:
        if question.get("exact_match", 1.0) > 0.0:
            continue
        category = classify_failure(
            method=method,
            category=question.get("category"),
            gold_answer=question.get("gold_answer"),
            prediction=question.get("prediction"),
            context_text=str(question.get("context_text") or ""),
            context_truncated=bool(question.get("context_truncated", False)),
        )
        rows.append(
            {
                "run_id": run_id,
                "config_hash": config_hash,
                "method": method,
                "question_id": question["question_id"],
                "sample_id": question.get("sample_id"),
                "category": question.get("category"),
                "failure_category": category.value,
                "gold_answer": question.get("gold_answer"),
                "prediction": question.get("prediction"),
                "gold_evidence": list(question.get("gold_evidence") or []),
                "predicted_evidence": list(question.get("predicted_evidence") or []),
                "evidence_mapping_gap": evidence_mapping_gap(
                    question.get("gold_evidence") or [], question.get("predicted_evidence") or []
                ),
                "answer_recall_in_context": gold_token_recall(
                    question.get("gold_answer"), str(question.get("context_text") or "")
                ),
                "context_token_count": len(str(question.get("context_text") or "").split()),
                "context_truncated": bool(question.get("context_truncated", False)),
            }
        )
    return rows


def write_review_sheet(rows: Sequence[Mapping[str, Any]], path: Any) -> None:
    """Write the review sheet as JSONL (one failure per line)."""
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), sort_keys=True, ensure_ascii=False))
            handle.write("\n")
    temporary.replace(path)
