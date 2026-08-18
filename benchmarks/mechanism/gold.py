"""Gold value-pair schema, canonicalization, and validation (Eval A).

Schema ``mechanism.gold-pairs.v1`` (spec §4.2): a gold pair declares the
factual update behind one LongMemEval knowledge-update question, with the
evidence turns that assert the old and new values and the expected ETEC
decision. Human annotation fills the pairs; this module validates them
deterministically before they are hashed into the mechanism report.

Canonicalization: values are compared as normalized token sets (lowercase,
punctuation removed, articles dropped), the same normalization used for the
official-answer subset check. Turn ids are resolved against the full
``haystack_sessions`` of the question's record (session ids and
``<session_id>:<index>`` raw turn ids both accepted).
"""

from __future__ import annotations

import re
import string
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

GOLD_PAIRS_SCHEMA_VERSION = "mechanism.gold-pairs.v1"
GOLD_REVIEW_SHEET_SCHEMA_VERSION = "mechanism.gold-review-sheet.v1"

_ARTICLES = re.compile(r"\b(a|an|the)\b", flags=re.IGNORECASE)
_PUNCTUATION = str.maketrans("", "", string.punctuation)


class GoldAction(StrEnum):
    SUPERSEDE = "SUPERSEDE"
    MERGE = "MERGE"
    ADD = "ADD"


class GoldPair(BaseModel):
    # ADD is the no-prior-value update action: when ``gold_action == ADD`` the
    # ``old_value`` may be empty and ``old_value_turn_ids`` may be an empty
    # list (the fact is first asserted at t_q, nothing is superseded). The
    # SUPERSEDE/MERGE actions still require a non-empty old side. This keeps
    # the schema honest about ADD questions like 22d2cb42 (guitar service
    # location) whose gold action is genuinely ADD with no old value.
    question_id: str = Field(min_length=1)
    subject: str = Field(min_length=1)
    attribute: str = Field(min_length=1)
    old_value: str = ""
    new_value: str = Field(min_length=1)
    old_value_turn_ids: list[str] = Field(default_factory=list)
    new_value_turn_ids: list[str] = Field(min_length=1)
    t_q: datetime
    t_old: datetime
    multi_valued: bool = False
    gold_action: GoldAction
    notes: str = ""

    @field_validator("t_q", "t_old")
    @classmethod
    def require_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("gold timestamps must be timezone-aware")
        return value

    @model_validator(mode="after")
    def require_old_side_for_supersede_or_merge(self) -> GoldPair:
        if self.gold_action is not GoldAction.ADD:
            if not self.old_value.strip():
                raise ValueError(
                    f"gold_action={self.gold_action.value} requires a non-empty "
                    "old_value (only ADD may carry an empty old side)"
                )
            if not self.old_value_turn_ids:
                raise ValueError(
                    f"gold_action={self.gold_action.value} requires at least one "
                    "old_value_turn_id (only ADD may carry an empty old side)"
                )
        return self


class GoldPairs(BaseModel):
    schema_version: str = GOLD_PAIRS_SCHEMA_VERSION
    seed: int = 42
    annotator: str = "orchestrator (human), 33/33-review precedent"
    annotated_at: datetime | None = None
    pairs: list[GoldPair] = Field(default_factory=list)

    @field_validator("annotated_at")
    @classmethod
    def require_aware_annotated_at(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("annotated_at must be timezone-aware")
        return value


def canonical_tokens(text: str | None) -> list[str]:
    """Normalize a value to a token list for subset/coverage comparisons."""
    if not text:
        return []
    normalized = text.lower().translate(_PUNCTUATION)
    normalized = _ARTICLES.sub(" ", normalized)
    return normalized.split()


def token_coverage(candidate: str | None, reference: str | None) -> float | None:
    """Fraction of ``reference`` tokens contained in ``candidate``.

    Returns ``None`` when the reference has no tokens. Used for the
    new-value-subset check against the official answer and for the M1
    content-coverage matching rule.
    """
    reference_tokens = canonical_tokens(reference)
    if not reference_tokens:
        return None
    candidate_tokens = set(canonical_tokens(candidate))
    overlap = sum(token in candidate_tokens for token in reference_tokens)
    return overlap / len(reference_tokens)


def iter_raw_records(dataset_path: Any) -> Iterable[Mapping[str, Any]]:
    """Iterate the raw LongMemEval records (question_id-keyed, as stored)."""
    import json

    payload = json.loads(dataset_path.read_bytes())
    if not isinstance(payload, list):
        raise ValueError("longmemeval dataset must be a JSON array")
    for record in payload:
        if not isinstance(record, dict) or not record.get("question_id"):
            raise ValueError("each longmemeval record must declare a question_id")
        yield record


def record_index(dataset_path: Any) -> dict[str, Mapping[str, Any]]:
    return {record["question_id"]: record for record in iter_raw_records(dataset_path)}


def resolve_turn_id(record: Mapping[str, Any], turn_id: str) -> bool:
    """True when ``turn_id`` names a session or a raw turn of the record.

    Raw turn ids are stored as ``<session_id>:<index>`` in the extraction
    and retrieval artifacts; session ids are the ``haystack_session_ids``.
    Both spellings are accepted so annotated gold can use either level.
    """
    session_ids = [str(item) for item in record.get("haystack_session_ids", [])]
    if turn_id in session_ids:
        return True
    sessions = record.get("haystack_sessions", [])
    for session_id, turns in zip(session_ids, sessions, strict=False):
        if not isinstance(turns, list):
            continue
        for index, turn in enumerate(turns):
            if not isinstance(turn, dict):
                continue
            if not (turn.get("content") or "").strip():
                continue
            if f"{session_id}:{index}" == turn_id:
                return True
    return False


def validate_pairs(pairs: Sequence[GoldPair], dataset_path: Any) -> list[str]:
    """Return a list of validation errors for the gold pairs (empty = valid).

    Checks (spec §4.2, step 3):
    - required fields non-empty and actions in {SUPERSEDE, MERGE, ADD};
    - ``new_value`` is a normalized-token subset of the official answer;
    - every turn id resolves inside the question's full haystack sessions
      (ADD pairs with an empty ``old_value_turn_ids`` skip the old-side
      resolution, since nothing is superseded);
    - ``t_q >= t_old``, and ``gold_action == SUPERSEDE`` requires ``t_old < t_q``.
    """
    records = record_index(dataset_path)
    errors: list[str] = []
    for pair in pairs:
        prefix = pair.question_id
        record = records.get(pair.question_id)
        if record is None:
            errors.append(f"{prefix}: question_id not found in dataset")
            continue
        coverage = token_coverage(pair.new_value, record.get("answer"))
        if coverage is None or coverage < 1.0:
            errors.append(
                f"{prefix}: new_value tokens are not a subset of the official answer"
            )
        for label, turn_ids in (
            ("old_value_turn_ids", pair.old_value_turn_ids),
            ("new_value_turn_ids", pair.new_value_turn_ids),
        ):
            for turn_id in turn_ids:
                if not resolve_turn_id(record, turn_id):
                    errors.append(f"{prefix}: {label} turn {turn_id!r} not in haystack")
        if pair.t_q < pair.t_old:
            errors.append(f"{prefix}: t_q must be >= t_old")
        if pair.gold_action is GoldAction.SUPERSEDE and pair.t_old >= pair.t_q:
            errors.append(f"{prefix}: SUPERSEDE requires t_old < t_q")
    return errors


def assert_pairs_valid(pairs: Sequence[GoldPair], dataset_path: Any) -> None:
    errors = validate_pairs(pairs, dataset_path)
    if errors:
        raise ValueError(
            "gold pair validation failed:\n" + "\n".join(f"- {error}" for error in errors)
        )


def pairs_hash(pairs: Sequence[GoldPair]) -> str:
    from benchmarks.common.artifacts import canonical_json_hash

    return canonical_json_hash(
        {
            "schema_version": GOLD_PAIRS_SCHEMA_VERSION,
            "pairs": [pair.model_dump(mode="json") for pair in pairs],
        }
    )


def load_gold_pairs(path: Path) -> GoldPairs:
    import json

    payload = json.loads(path.read_text(encoding="utf-8"))
    return GoldPairs.model_validate(payload)


def datetime_iso(value: datetime) -> str:
    return value.isoformat()


def parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


__all__ = [
    "GOLD_PAIRS_SCHEMA_VERSION",
    "GOLD_REVIEW_SHEET_SCHEMA_VERSION",
    "GoldAction",
    "GoldPair",
    "GoldPairs",
    "assert_pairs_valid",
    "canonical_tokens",
    "datetime_iso",
    "iter_raw_records",
    "load_gold_pairs",
    "pairs_hash",
    "parse_iso",
    "record_index",
    "resolve_turn_id",
    "token_coverage",
    "validate_pairs",
]
