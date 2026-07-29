from __future__ import annotations

import json
from pathlib import Path

from benchmarks.common.artifacts import RunSummary
from benchmarks.vector_baseline import load_vector_baseline_config, run_vector_baseline

CONFIGS = Path("benchmarks/configs")


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_vector_baseline_runs_on_fixtures_without_network_and_persists_retrieval(
    tmp_path: Path,
) -> None:
    config = load_vector_baseline_config(CONFIGS / "m05_vector_fixture.json")

    summary = run_vector_baseline(config, tmp_path / "vector")

    assert summary.metadata.metadata["baseline"] == "vector_rag"
    assert summary.metadata.metadata["provider"] == "deterministic_fake"
    assert summary.sample_count == 2

    run_dir = tmp_path / "vector"
    predictions = _read_jsonl(run_dir / "predictions.jsonl")
    retrieval = _read_jsonl(run_dir / "retrieval.jsonl")
    assert [record["prediction"] for record in predictions] == ["Seattle", "7 May 2023"]
    assert len(retrieval) == 2

    first_retrieval = retrieval[0]["selected_context"]
    assert first_retrieval[0]["source_turn_id"] == "session-new:0"
    assert first_retrieval[0]["score"] > 0
    assert "I moved to Seattle." in first_retrieval[0]["text"]
    assert predictions[0]["metadata"]["retrieval"]["selected_context"] == first_retrieval

    second_retrieval = retrieval[1]["selected_context"]
    assert second_retrieval[0]["source_turn_id"] == "D1:1"
    assert "support group yesterday" in second_retrieval[0]["text"]

    summary_from_disk = RunSummary.model_validate(
        json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    )
    assert Path(summary_from_disk.predictions_path).name == "predictions.jsonl"
    assert Path(summary_from_disk.samples_path).name == "samples.jsonl"
    assert (run_dir / "model_cache/embeddings").exists()
