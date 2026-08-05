"""Typed failure taxonomy and sample review sheet (M15).

Failures are classified with deterministic rules from immutable run artifacts
plus the dataset (gold answers). ``answer_recoverable`` is a deterministic
proxy: the fraction of gold-answer tokens that appear in the context the
method actually saw (gold-token recall >= 0.5). It deliberately avoids LLM
judges so the taxonomy is reproducible offline.
"""

from __future__ import annotations

import json
import re
import string
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from enum import StrEnum
from typing import Any

ANSWER_RECOVERABLE_RECALL = 0.5

_ARTICLES = re.compile(r"\b(a|an|the)\b", flags=re.IGNORECASE)
_PUNCTUATION = str.maketrans("", "", string.punctuation)


class FailureCategory(StrEnum):
    ADVERSARIAL_NO_GOLD = "adversarial_no_gold_answer"
    ANSWER_NOT_RECOVERABLE = "answer_not_recoverable_from_context"
    ANSWER_RECOVERABLE_WRONG = "answer_recoverable_wrong_prediction"
    CONTEXT_BUDGET_TRUNCATION = "context_budget_truncation"
    EMPTY_PREDICTION = "empty_prediction"
    NO_GOLD_ANSWER = "no_gold_answer"
    NO_MEMORY_BASELINE = "no_memory_baseline"
    OTHER = "other"


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


def classify_failure(
    *,
    method: str,
    category: str | None,
    gold_answer: str | None,
    prediction: str | None,
    context_text: str,
    context_truncated: bool = False,
) -> FailureCategory:
    """Classify one failed question (``exact_match == 0``).

    Order matters: protocol-level causes (adversarial, no gold, no-memory
    baseline, budget truncation, empty prediction) are decided before the
    retrieval/reader split.
    """
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
    """Build review-sheet rows for one method of one run.

    ``questions`` is an iterable of question dicts with the fields produced by
    the LoCoMo runner's derived artifacts: ``question_id``, ``sample_id``,
    ``category``, ``gold_answer``, ``prediction``, ``exact_match``,
    ``predicted_evidence``, ``gold_evidence``, ``context_text``,
    ``context_truncated``. Only failed questions (``exact_match == 0``) are
    emitted.
    """
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
