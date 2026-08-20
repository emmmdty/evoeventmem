"""Unit tests for the S3 Step 4 M2 stale-judge response parser."""

from __future__ import annotations

from benchmarks.mechanism.m2_stale_judge import _parse_judge_response


def test_parse_full_less_stale() -> None:
    text = '{"less_stale": "A", "reason": "Answer A is more recent."}'
    parsed = _parse_judge_response(text)
    assert parsed["less_stale"] == "a"
    assert "Answer A is more recent" in parsed["reason"]


def test_parse_etec_less_stale() -> None:
    text = '{"less_stale": "B", "reason": "Answer B is more recent."}'
    parsed = _parse_judge_response(text)
    assert parsed["less_stale"] == "b"


def test_parse_tie() -> None:
    text = '{"less_stale": "tie", "reason": "Both same."}'
    parsed = _parse_judge_response(text)
    assert parsed["less_stale"] == "tie"


def test_parse_tolerates_surrounding_prose() -> None:
    text = 'Here is my verdict: {"less_stale": "A", "reason": "A wins"} Thanks!'
    parsed = _parse_judge_response(text)
    assert parsed["less_stale"] == "a"


def test_parse_lowercase_ab() -> None:
    text = '{"less_stale": "a", "reason": "lowercase"}'
    parsed = _parse_judge_response(text)
    assert parsed["less_stale"] == "a"


def test_parse_error_on_garbage() -> None:
    parsed = _parse_judge_response("no json here at all")
    assert parsed["less_stale"] == "parse_error"


def test_parse_error_on_invalid_value() -> None:
    text = '{"less_stale": "neither", "reason": "bad"}'
    parsed = _parse_judge_response(text)
    assert parsed["less_stale"] == "parse_error"


def test_parse_truncated_json_treated_as_error() -> None:
    # The real judge occasionally emits a truncated JSON object; the parser
    # must record a parse_error rather than silently dropping the sample.
    text = '{"less_stale": "tie", '
    parsed = _parse_judge_response(text)
    assert parsed["less_stale"] == "parse_error"
