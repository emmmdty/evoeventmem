from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmarks.common.normalization import (
    NormalizationError,
    iter_locomo_records,
    iter_longmemeval_records,
)

FIXTURES = Path("tests/fixtures")


def test_longmemeval_fixture_normalizes_deterministically() -> None:
    first = list(iter_longmemeval_records(FIXTURES / "longmemeval/oracle_tiny.json"))
    second = list(iter_longmemeval_records(FIXTURES / "longmemeval/oracle_tiny.json"))

    assert [record.model_dump(mode="json") for record in first] == [
        record.model_dump(mode="json") for record in second
    ]
    record = first[0]
    assert record.dataset == "longmemeval"
    assert record.sample_id == "lme-q1"
    assert [session.session_id for session in record.sessions] == ["session-old", "session-new"]
    assert record.sessions[1].timestamp.isoformat() == "2024-02-01T00:00:00+00:00"
    assert record.sessions[1].turns[0].turn_id == "session-new:0"
    assert record.sessions[1].turns[0].metadata == {"has_answer": True}
    assert record.questions[0].question_id == "lme-q1"
    assert record.questions[0].category == "knowledge-update"
    assert record.questions[0].evidence[0].source_id == "session-new"
    assert record.questions[0].asked_at.isoformat() == "2024-02-03T00:00:00+00:00"


def test_locomo_fixture_normalizes_sessions_questions_and_events() -> None:
    records = list(iter_locomo_records(FIXTURES / "locomo/locomo_tiny.json"))

    record = records[0]
    assert record.dataset == "locomo"
    assert record.sample_id == "conv-tiny"
    assert record.sessions[0].session_id == "session_1"
    assert record.sessions[0].timestamp.isoformat() == "2023-05-08T13:56:00+00:00"
    assert record.sessions[0].turns[0].turn_id == "D1:1"
    assert record.sessions[0].turns[0].speaker == "Caroline"
    assert record.questions[0].question_id == "conv-tiny:qa:0"
    assert record.questions[0].category == "2"
    assert record.questions[0].evidence[0].source_id == "D1:1"
    assert record.event_summaries[0].session_id == "session_1"
    assert record.event_summaries[0].events == {
        "Caroline": ["Caroline went to an LGBTQ support group on 7 May 2023."],
        "Melanie": [],
    }


def test_round_trip_serialization_retains_ids_timestamps_and_evidence() -> None:
    original = next(iter_longmemeval_records(FIXTURES / "longmemeval/oracle_tiny.json"))

    payload = json.loads(original.model_dump_json())
    restored = type(original).model_validate(payload)

    assert restored.sample_id == original.sample_id
    assert restored.sessions[0].timestamp == original.sessions[0].timestamp
    assert restored.questions[0].question_id == original.questions[0].question_id
    assert restored.questions[0].evidence == original.questions[0].evidence


@pytest.mark.parametrize(
    ("loader", "path", "message"),
    [
        (
            iter_longmemeval_records,
            FIXTURES / "longmemeval/malformed_missing_question_id.json",
            "longmemeval sample index 0",
        ),
        (
            iter_locomo_records,
            FIXTURES / "locomo/malformed_missing_dia_id.json",
            "locomo sample conv-bad",
        ),
    ],
)
def test_malformed_records_fail_with_sample_local_diagnostics(
    loader, path: Path, message: str
) -> None:
    with pytest.raises(NormalizationError, match=message):
        list(loader(path))
