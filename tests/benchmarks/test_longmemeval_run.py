from __future__ import annotations

import json
from pathlib import Path

from benchmarks.longmemeval.run import (
    Method,
    SampleResult,
    _validate_samples,
    load_config,
    run_experiment,
)

SMOKE_CONFIG = Path("configs/longmemeval/smoke.toml")
MAIN_CONFIG = Path("configs/longmemeval/main.toml")


def test_smoke_config_completes_on_tiny_subset(tmp_path: Path) -> None:
    config = load_config(SMOKE_CONFIG)
    assert config.sample_limit is None
    assert config.provider == "deterministic_fake"

    run_dir = tmp_path / "run"
    summary = run_experiment(config, run_dir)

    assert summary.sample_validation.valid
    assert summary.sample_validation.missing_sample_ids == []
    assert summary.sample_validation.duplicate_sample_ids == []
    assert set(summary.methods) == {method.value for method in Method}
    assert all(method_summary.sample_count == 1 for method_summary in summary.methods.values())
    assert (run_dir / "summary.json").exists()
    for method in Method:
        assert (run_dir / method.value / "predictions.jsonl").exists()
        assert (run_dir / method.value / "samples.jsonl").exists()
        assert (run_dir / method.value / "retrieval.jsonl").exists()


def test_smoke_answers_and_category_metrics(tmp_path: Path) -> None:
    config = load_config(SMOKE_CONFIG)
    summary = run_experiment(config, tmp_path / "run")

    assert summary.methods["no_memory"].exact_match == 0.0
    assert summary.methods["full_context"].exact_match == 1.0
    for method in ("vector_rag", "event_no_etec", "etec", "full"):
        assert summary.methods[method].exact_match == 1.0
        assert summary.methods[method].efficiency.tokens_per_query is not None
    assert summary.methods["no_memory"].efficiency.tokens_per_query is not None
    assert "knowledge-update" in summary.methods["full"].categories
    category = summary.methods["full"].categories["knowledge-update"]
    assert category.sample_count == 1
    assert category.exact_match == 1.0


def test_memory_methods_respect_token_budget(tmp_path: Path) -> None:
    config = load_config(SMOKE_CONFIG)
    run_dir = tmp_path / "run"
    run_experiment(config, run_dir)

    for method in ("vector_rag", "event_no_etec", "etec", "full"):
        retrievals = _read_jsonl(run_dir / method / "retrieval.jsonl")
        assert retrievals
        for record in retrievals:
            assert record["total_tokens"] <= record["budget_tokens"]
            for item in record["packed_items"]:
                assert item["evidence_refs"]
                assert item["component_scores"]
                assert 0.0 <= item["final_score"] <= 1.0


def test_resume_skips_completed_samples_without_overwriting(tmp_path: Path) -> None:
    config = load_config(SMOKE_CONFIG)
    run_dir = tmp_path / "run"
    first = run_experiment(config, run_dir)
    sample_path = run_dir / "samples" / "lme-q1.json"
    original = sample_path.read_bytes()

    second = run_experiment(config, run_dir)

    assert sample_path.read_bytes() == original
    assert second.sample_validation.valid
    assert second.sample_validation.completed_sample_count == 1
    assert second.methods == first.methods


def test_summary_detects_missing_and_duplicate_sample_ids() -> None:
    expected = ["a", "b", "c"]
    completed = ["a", "b", "b"]

    validation = _validate_samples(expected, completed)

    assert validation.valid is False
    assert validation.missing_sample_ids == ["c"]
    assert validation.duplicate_sample_ids == ["b"]
    assert validation.expected_sample_count == 3
    assert validation.completed_sample_count == 3


def test_main_config_records_dataset_and_versions() -> None:
    config = load_config(MAIN_CONFIG)

    assert config.provider == "openai_compatible"
    assert config.live_provider is not None
    assert config.live_provider.api_key_env == "OPENAI_API_KEY"
    assert set(config.methods) == {method.value for method in Method}


def test_sample_file_is_immutable_per_sample_record(tmp_path: Path) -> None:
    config = load_config(SMOKE_CONFIG)
    run_dir = tmp_path / "run"
    run_experiment(config, run_dir)

    payload = json.loads((run_dir / "samples" / "lme-q1.json").read_text(encoding="utf-8"))
    sample = SampleResult.model_validate(payload)
    assert sample.sample_id == "lme-q1"
    assert sample.question_type == "knowledge-update"
    assert set(sample.methods) == {method.value for method in Method}
    assert sample.ingestion["raw"]["memory_count"] >= 1


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
