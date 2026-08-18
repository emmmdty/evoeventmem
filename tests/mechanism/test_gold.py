from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from benchmarks.mechanism.gold import (
    GoldAction,
    GoldPair,
    GoldPairs,
    assert_pairs_valid,
    canonical_tokens,
    pairs_hash,
    record_index,
    resolve_turn_id,
    token_coverage,
    validate_pairs,
)

FIXTURE = Path("tests/fixtures/longmemeval/oracle_tiny.json")


def _pair(**overrides: object) -> GoldPair:
    base: dict[str, object] = {
        "question_id": "lme-q1",
        "subject": "user",
        "attribute": "city of residence",
        "old_value": "Austin",
        "new_value": "Seattle",
        "old_value_turn_ids": ["session-old:0"],
        "new_value_turn_ids": ["session-new:0"],
        "t_q": datetime(2024, 2, 3, tzinfo=UTC),
        "t_old": datetime(2024, 1, 1, tzinfo=UTC),
        "multi_valued": False,
        "gold_action": GoldAction.SUPERSEDE,
        "notes": "",
    }
    base.update(overrides)
    return GoldPair.model_validate(base)


def test_gold_pairs_schema_round_trip_and_hash() -> None:
    pairs = GoldPairs(pairs=[_pair()])
    payload = pairs.model_dump(mode="json")
    assert payload["schema_version"] == "mechanism.gold-pairs.v1"
    assert pairs_hash([_pair()]) == pairs_hash([_pair()])
    assert pairs_hash([_pair()]).startswith("sha256:")


def test_canonical_tokens_normalize_case_punctuation_and_articles() -> None:
    assert canonical_tokens("The Austin, TX!") == ["austin", "tx"]
    assert canonical_tokens(None) == []
    assert canonical_tokens("") == []


def test_token_coverage_subset_and_fraction() -> None:
    assert token_coverage("I moved to Seattle", "Seattle") == 1.0
    assert token_coverage("Seattle is rainy", "Seattle") == 1.0
    assert token_coverage("I moved to Austin", "Seattle") == 0.0
    assert token_coverage("anything", "") is None


def test_resolve_turn_id_accepts_session_and_raw_turn_levels() -> None:
    record = record_index(FIXTURE)["lme-q1"]
    assert resolve_turn_id(record, "session-old")
    assert resolve_turn_id(record, "session-new")
    assert resolve_turn_id(record, "session-old:0")
    assert resolve_turn_id(record, "session-new:0")
    assert not resolve_turn_id(record, "session-old:5")
    assert not resolve_turn_id(record, "ghost")


def test_valid_pairs_pass_validation() -> None:
    assert validate_pairs([_pair()], FIXTURE) == []
    assert_pairs_valid([_pair()], FIXTURE)


def test_validation_rejects_unknown_question() -> None:
    errors = validate_pairs([_pair(question_id="ghost-q")], FIXTURE)
    assert any("not found" in error for error in errors)


def test_validation_rejects_new_value_not_in_answer() -> None:
    errors = validate_pairs([_pair(new_value="Tacoma")], FIXTURE)
    assert any("subset" in error for error in errors)


def test_validation_rejects_unknown_turn_ids() -> None:
    errors = validate_pairs([_pair(old_value_turn_ids=["ghost:0"])], FIXTURE)
    assert any(
        "old_value_turn_ids turn" in error and "not in haystack" in error
        for error in errors
    )


def test_validation_rejects_inverted_times() -> None:
    errors = validate_pairs(
        [
            _pair(
                t_q=datetime(2024, 1, 1, tzinfo=UTC),
                t_old=datetime(2024, 2, 3, tzinfo=UTC),
            )
        ],
        FIXTURE,
    )
    assert any("t_q must be >= t_old" in error for error in errors)


def test_supersede_requires_strictly_older_old_value() -> None:
    errors = validate_pairs(
        [
            _pair(
                t_q=datetime(2024, 1, 1, tzinfo=UTC),
                t_old=datetime(2024, 1, 1, tzinfo=UTC),
            )
        ],
        FIXTURE,
    )
    assert any("SUPERSEDE requires t_old < t_q" in error for error in errors)


def test_add_action_without_temporal_order_is_allowed() -> None:
    pair = _pair(
        gold_action=GoldAction.ADD,
        t_q=datetime(2024, 1, 1, tzinfo=UTC),
        t_old=datetime(2024, 1, 1, tzinfo=UTC),
    )
    assert validate_pairs([pair], FIXTURE) == []


def test_naive_datetimes_are_rejected_by_schema() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        _pair(t_q=datetime(2024, 2, 3), t_old=datetime(2024, 1, 1))
