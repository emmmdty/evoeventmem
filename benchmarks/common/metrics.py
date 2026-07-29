from __future__ import annotations

import re
import string
from collections import Counter
from collections.abc import Iterable
from typing import Any

from pydantic import BaseModel, Field

_ARTICLES = re.compile(r"\b(a|an|the)\b", flags=re.IGNORECASE)
_PUNCTUATION = str.maketrans("", "", string.punctuation)


class AnswerMetrics(BaseModel):
    exact_match: float = Field(ge=0, le=1)
    token_f1: float = Field(ge=0, le=1)


class EvidenceMetrics(BaseModel):
    precision: float = Field(ge=0, le=1)
    recall: float = Field(ge=0, le=1)
    f1: float = Field(ge=0, le=1)


def compute_answer_metrics(gold_answer: str | None, predicted_answer: str | None) -> AnswerMetrics:
    gold_tokens = _answer_tokens(gold_answer)
    predicted_tokens = _answer_tokens(predicted_answer)
    exact_match = 1.0 if " ".join(gold_tokens) == " ".join(predicted_tokens) else 0.0
    token_f1 = _token_f1(gold_tokens, predicted_tokens)
    return AnswerMetrics(exact_match=exact_match, token_f1=token_f1)


def compute_evidence_metrics(
    gold_evidence: Iterable[Any],
    predicted_evidence: Iterable[Any],
) -> EvidenceMetrics:
    gold = {_evidence_key(evidence) for evidence in gold_evidence}
    predicted = {_evidence_key(evidence) for evidence in predicted_evidence}
    if not gold and not predicted:
        return EvidenceMetrics(precision=1.0, recall=1.0, f1=1.0)
    if not gold or not predicted:
        return EvidenceMetrics(precision=0.0, recall=0.0, f1=0.0)

    overlap = len(gold & predicted)
    precision = overlap / len(predicted)
    recall = overlap / len(gold)
    return EvidenceMetrics(precision=precision, recall=recall, f1=_harmonic_mean(precision, recall))


def _answer_tokens(answer: str | None) -> list[str]:
    normalized = "" if answer is None else answer.lower()
    normalized = normalized.translate(_PUNCTUATION)
    normalized = _ARTICLES.sub(" ", normalized)
    return normalized.split()


def _token_f1(gold_tokens: list[str], predicted_tokens: list[str]) -> float:
    if not gold_tokens and not predicted_tokens:
        return 1.0
    if not gold_tokens or not predicted_tokens:
        return 0.0

    overlap = sum((Counter(gold_tokens) & Counter(predicted_tokens)).values())
    if overlap == 0:
        return 0.0
    precision = overlap / len(predicted_tokens)
    recall = overlap / len(gold_tokens)
    return _harmonic_mean(precision, recall)


def _harmonic_mean(precision: float, recall: float) -> float:
    if precision == 0.0 or recall == 0.0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def _evidence_key(evidence: Any) -> tuple[str, str, str | None]:
    if isinstance(evidence, dict):
        return evidence["source_type"], evidence["source_id"], evidence.get("locator")
    return evidence.source_type, evidence.source_id, evidence.locator
