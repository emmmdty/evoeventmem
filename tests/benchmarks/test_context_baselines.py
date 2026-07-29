from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from benchmarks.common.artifacts import RunSummary
from benchmarks.common.normalization import NormalizedQuestion, NormalizedSession, NormalizedTurn
from benchmarks.context_baselines import (
    FullContextBuilder,
    NoMemoryContextBuilder,
    load_context_baseline_config,
    run_context_baseline,
)

CONFIGS = Path("benchmarks/configs")


class ExplodingHistory:
    def __iter__(self) -> Iterator[NormalizedSession]:
        raise AssertionError("no-memory baseline must not access history")


def test_no_memory_builder_never_accesses_history() -> None:
    question = NormalizedQuestion(question_id="q1", question="Where does the user live?")

    context = NoMemoryContextBuilder(max_input_tokens=16).build(question, ExplodingHistory())

    assert context.prompt == "Question: Where does the user live?"
    assert context.included_history_turn_ids == ()
    assert context.truncations == ()


def test_full_context_budget_overflow_is_deterministic_and_recorded() -> None:
    question = NormalizedQuestion(question_id="q1", question="Where does the user live now?")
    history = [
        NormalizedSession(
            session_id="old",
            timestamp=datetime(2024, 1, 1, tzinfo=UTC),
            turns=[
                NormalizedTurn(
                    turn_id="old:1",
                    speaker="user",
                    content="I live in Austin.",
                )
            ],
        ),
        NormalizedSession(
            session_id="new",
            timestamp=datetime(2024, 2, 1, tzinfo=UTC),
            turns=[
                NormalizedTurn(
                    turn_id="new:1",
                    speaker="user",
                    content="I moved to Seattle.",
                )
            ],
        ),
    ]
    builder = FullContextBuilder(max_input_tokens=16)

    first = builder.build(question, history)
    second = builder.build(question, list(reversed(history)))

    assert first == second
    assert first.included_history_turn_ids == ("new:1",)
    assert "Seattle" in first.prompt
    assert "Austin" not in first.prompt
    assert [decision.source_id for decision in first.truncations] == ["old:1"]
    assert first.truncations[0].reason == "context_budget_exceeded"
    assert first.input_tokens <= 16


def test_context_baselines_produce_standard_run_artifacts(tmp_path: Path) -> None:
    no_memory_config = load_context_baseline_config(CONFIGS / "m04_no_memory_fixture.json")
    full_context_config = load_context_baseline_config(CONFIGS / "m04_full_context_fixture.json")

    no_memory_summary = run_context_baseline(no_memory_config, tmp_path / "no-memory")
    full_context_summary = run_context_baseline(full_context_config, tmp_path / "full-context")

    assert no_memory_summary.metadata.metadata["baseline"] == "no_memory"
    assert full_context_summary.metadata.metadata["baseline"] == "full_context"
    assert no_memory_summary.sample_count == 2
    assert full_context_summary.sample_count == 2

    no_memory_predictions = _read_jsonl(tmp_path / "no-memory/predictions.jsonl")
    full_context_predictions = _read_jsonl(tmp_path / "full-context/predictions.jsonl")
    assert [record["prediction"] for record in no_memory_predictions] == ["", ""]
    assert [record["prediction"] for record in full_context_predictions] == [
        "Seattle",
        "7 May 2023",
    ]
    assert no_memory_predictions[0]["metadata"]["context"]["included_history_turn_ids"] == []
    assert full_context_predictions[0]["metadata"]["context"]["included_history_turn_ids"]

    for run_dir in (tmp_path / "no-memory", tmp_path / "full-context"):
        summary = RunSummary.model_validate(json.loads((run_dir / "summary.json").read_text()))
        assert Path(summary.predictions_path).name == "predictions.jsonl"
        assert Path(summary.samples_path).name == "samples.jsonl"
        assert (run_dir / "samples.jsonl").exists()


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
