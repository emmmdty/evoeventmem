from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmarks.common.artifacts import (
    ArtifactClass,
    RunManifest,
    load_finalized,
    required_hash,
)
from benchmarks.common.memory_inputs import FakeEventExtractor
from benchmarks.longmemeval.run import (
    Method,
    SampleResult,
    _validate_samples,
    load_config,
    main,
    run_experiment,
)

SMOKE_CONFIG = Path("configs/longmemeval/smoke.toml")
MAIN_CONFIG = Path("configs/longmemeval/main.toml")

RAW_TURN_CONTENTS = {
    "I live in Austin.",
    "Austin sounds warm.",
    "I moved to Seattle.",
    "I hope Seattle treats you well.",
}


def test_smoke_config_completes_on_tiny_subset_and_finalizes(tmp_path: Path) -> None:
    config = load_config(SMOKE_CONFIG)
    assert config.provider == "deterministic_fake"
    assert config.sample_limit is None

    run_dir = tmp_path / "run"
    summary = run_experiment(config, run_dir)

    assert summary.sample_validation.valid
    assert summary.sample_validation.missing_sample_ids == []
    assert summary.sample_validation.duplicate_sample_ids == []
    assert set(summary.methods) == {method.value for method in Method}
    assert all(method_summary.sample_count == 1 for method_summary in summary.methods.values())
    assert (run_dir / "manifest.json").exists()
    assert (run_dir / "summary.json").exists()
    finalized = load_finalized(run_dir)
    assert finalized.artifact_class is ArtifactClass.SMOKE
    assert finalized.manifest_hash == RunManifest.model_validate_json(
        (run_dir / "manifest.json").read_text(encoding="utf-8")
    ).manifest_hash()
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


def test_memory_methods_respect_complete_token_budget(tmp_path: Path) -> None:
    config = load_config(SMOKE_CONFIG)
    run_dir = tmp_path / "run"
    run_experiment(config, run_dir)

    for method in ("vector_rag", "event_no_etec", "etec", "full"):
        retrievals = _read_jsonl(run_dir / method / "retrieval.jsonl")
        assert retrievals
        for record in retrievals:
            assert record["total_tokens"] <= record["budget_tokens"]
            assert record["content_tokens"] >= 0
            assert record["prompt_overhead_tokens"] >= 0
            assert record["total_input_tokens_estimate"] <= record["budget_tokens"]
            assert record["total_input_tokens_estimate"] == (
                record["content_tokens"] + record["prompt_overhead_tokens"]
            )
            for item in record["packed_items"]:
                assert item["evidence_refs"]
                assert item["component_scores"]
                assert 0.0 <= item["final_score"] <= 1.0


def test_vector_rag_indexes_raw_turn_chunks_only(tmp_path: Path) -> None:
    config = load_config(SMOKE_CONFIG)
    run_dir = tmp_path / "run"
    run_experiment(config, run_dir)

    sample = _read_sample(run_dir)
    assert sample.ingestion["raw_turn"]["input_kind"] == "raw_turn"
    assert sample.ingestion["raw_turn"]["chunk_count"] == 4
    record = sample.methods["vector_rag"]
    assert record.retrieval is not None
    for item in record.retrieval["packed_items"]:
        assert item["content"] in RAW_TURN_CONTENTS
        for ref in item["evidence_refs"]:
            assert ref["source_type"] == "turn"
            assert ref["raw_turn_id"]
    assert record.context is not None
    assert record.context["input_kind"] == "raw_turn"
    assert "snapshot_id" not in record.context


def test_vector_rag_never_receives_extraction_snapshot(tmp_path: Path) -> None:
    config = load_config(SMOKE_CONFIG)
    run_dir = tmp_path / "run"
    run_experiment(config, run_dir)

    sample = _read_sample(run_dir)
    snapshot_id = sample.ingestion["event"]["snapshot_id"]
    assert snapshot_id
    vector_record = sample.methods["vector_rag"]
    assert vector_record.context is not None
    assert vector_record.context["input_kind"] == "raw_turn"
    for method in ("event_no_etec", "etec", "full"):
        record = sample.methods[method]
        assert record.context is not None
        assert record.context["input_kind"] == "event_snapshot"
        assert record.context["snapshot_id"] == snapshot_id


def test_event_methods_share_one_extraction_snapshot(tmp_path: Path) -> None:
    config = load_config(SMOKE_CONFIG)
    run_dir = tmp_path / "run"
    run_experiment(config, run_dir)

    sample = _read_sample(run_dir)
    snapshot_id = sample.ingestion["event"]["snapshot_id"]
    assert snapshot_id.startswith("sha256:")
    snapshot_path = run_dir / "samples" / "lme-q1.extraction_snapshot.json"
    assert snapshot_path.exists()
    snapshot_hash = sample.ingestion["event"]["snapshot_hash"]
    assert snapshot_hash == required_hash(snapshot_path)
    ids = {
        sample.methods[method].context["snapshot_id"]
        for method in ("event_no_etec", "etec", "full")
    }
    assert ids == {snapshot_id}
    assert sample.ingestion["event"]["extractor_model"] == "deterministic-local-fake-extractor"


def test_extraction_runs_once_per_conversation_from_cleared_input(tmp_path: Path) -> None:
    config = load_config(SMOKE_CONFIG)
    spy = RecordingExtractor(FakeEventExtractor())
    run_experiment(config, tmp_path / "run", extractor=spy)

    assert spy.calls == 1
    assert len(spy.requests) == 1
    request = spy.requests[0]
    assert request.event_summaries == []
    assert request.observations == []


def test_packed_events_carry_exact_raw_turn_provenance(tmp_path: Path) -> None:
    config = load_config(SMOKE_CONFIG)
    run_dir = tmp_path / "run"
    run_experiment(config, run_dir)

    sample = _read_sample(run_dir)
    for method in ("vector_rag", "event_no_etec", "etec", "full"):
        record = sample.methods[method]
        assert record.retrieval is not None
        for item in record.retrieval["packed_items"]:
            assert item["evidence_refs"]
            for ref in item["evidence_refs"]:
                assert ref["source_type"] == "turn"
                assert ref["raw_turn_id"]
                locator = ref["locator"]
                assert locator.startswith("chars=")
                start, end = _parse_locator(locator)
                assert end > start
                assert ref.get("quote") is not None


def test_reader_consumes_rendered_qemr_reader_messages(tmp_path: Path) -> None:
    config = load_config(SMOKE_CONFIG)
    run_dir = tmp_path / "run"
    run_experiment(config, run_dir)

    for method in ("vector_rag", "event_no_etec", "etec", "full"):
        record = _read_sample(run_dir).methods[method]
        assert record.input_tokens == record.retrieval["total_input_tokens_estimate"]
        assert record.context["reader_source"] == "qemr_reader_messages"


def test_construction_cost_separated_from_per_query_cost(tmp_path: Path) -> None:
    config = load_config(SMOKE_CONFIG)
    run_dir = tmp_path / "run"
    run_experiment(config, run_dir)

    sample = _read_sample(run_dir)
    construction = sample.construction
    assert construction.extraction_ms >= 0.0
    assert construction.write_raw_ms >= 0.0
    assert construction.write_etec_ms >= 0.0
    for method in ("vector_rag", "event_no_etec", "etec", "full"):
        record = sample.methods[method]
        assert record.search_latency_ms >= 0.0
        assert record.question_latency_ms >= 0.0
        assert record.write_latency_ms >= 0.0


def test_manifest_resolves_expected_ids_providers_and_vector_input(tmp_path: Path) -> None:
    config = load_config(SMOKE_CONFIG)
    run_dir = tmp_path / "run"
    run_experiment(config, run_dir)

    manifest = RunManifest.model_validate_json(
        (run_dir / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest.dataset == "longmemeval"
    assert manifest.expected_sample_ids == ["lme-q1"]
    assert manifest.expected_question_ids == ["lme-q1"]
    assert manifest.reader.model_id == "deterministic-local-fake-reader"
    assert manifest.extractor.model_id == "deterministic-local-fake-extractor"
    assert manifest.embedding.model_id == "deterministic-local-embedding"
    assert manifest.reader.model_id != manifest.extractor.model_id
    assert manifest.reader.model_id != manifest.embedding.model_id
    assert manifest.tokenizer.name == "evoeventmem-deterministic-tokens"
    assert manifest.metadata["vector_input_kind"] == "raw_turn"
    assert manifest.policies.retrieval == "qemr-weight-profiles.v2"


def test_manifest_drift_refused(tmp_path: Path) -> None:
    config = load_config(SMOKE_CONFIG)
    run_dir = tmp_path / "run"
    run_experiment(config, run_dir)

    drifted = config.model_copy(update={"max_input_tokens": 512})
    with pytest.raises(ValueError, match="manifest drift"):
        run_experiment(drifted, run_dir)


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


def test_run_dir_cli_first_run_and_identical_resume_address_same_directory(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "runs" / "smoke"
    assert main(["--config", str(SMOKE_CONFIG), "--run-dir", str(run_dir)]) == 0
    assert main(["--config", str(SMOKE_CONFIG), "--run-dir", str(run_dir)]) == 0
    assert (run_dir / "manifest.json").exists()
    assert (run_dir / "finalized" / "FINALIZED.json").exists()
    assert (run_dir / "samples" / "lme-q1.json").exists()


def test_run_dir_resume_dir_output_root_are_mutually_exclusive(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="--run-dir"):
        main(
            [
                "--config",
                str(SMOKE_CONFIG),
                "--run-dir",
                str(tmp_path / "a"),
                "--output-root",
                str(tmp_path / "b"),
            ]
        )
    with pytest.raises(ValueError, match="--resume-dir"):
        main(
            [
                "--config",
                str(SMOKE_CONFIG),
                "--resume-dir",
                str(tmp_path / "a"),
                "--output-root",
                str(tmp_path / "b"),
            ]
        )
    with pytest.raises(ValueError, match="--run-dir"):
        main(
            [
                "--config",
                str(SMOKE_CONFIG),
                "--run-dir",
                str(tmp_path / "a"),
                "--resume-dir",
                str(tmp_path / "b"),
            ]
        )


def test_main_config_resolves_independent_models() -> None:
    config = load_config(MAIN_CONFIG)

    assert config.provider == "openai_compatible"
    assert config.providers.reader.model_id == "deepseek-v4-flash"
    assert config.providers.extractor.model_id == "deepseek-v4-flash"
    assert config.providers.embedding.model_id == "qwen3-embedding-0.6b"
    assert config.providers.reader.base_url == "https://api.deepseek.com"
    assert config.providers.reader.api_key_env == "OPENAI_API_KEY"
    assert config.providers.embedding.api_key_env == "EMBEDDING_API_KEY"
    assert config.providers.reader.thinking == "disabled"
    assert config.max_extraction_tokens == 65536
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
    assert sample.ingestion["raw_turn"]["memory_count"] >= 1
    assert sample.ingestion["event"]["event_count"] == 4


class RecordingExtractor:
    """Deterministic fake extractor that records each extraction input."""

    def __init__(self, inner: FakeEventExtractor) -> None:
        self.inner = inner
        self.calls = 0
        self.requests: list[object] = []

    def extract(self, request):  # noqa: ANN001
        self.calls += 1
        self.requests.append(request)
        return self.inner.extract(request)


def _read_sample(run_dir: Path) -> SampleResult:
    payload = json.loads((run_dir / "samples" / "lme-q1.json").read_text(encoding="utf-8"))
    return SampleResult.model_validate(payload)


def _parse_locator(locator: str) -> tuple[int, int]:
    _, bounds = locator.split("=", 1)
    start, end = bounds.split(":", 1)
    return int(start), int(end)


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
