from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmarks.common.artifacts import ArtifactClass, RunManifest, load_finalized
from benchmarks.common.memory_inputs import FakeEventExtractor
from benchmarks.locomo.run import (
    LOCOMO_CATEGORY_BY_ID,
    Method,
    SampleResult,
    _evidence_from_packed_items,
    _validate_question_ids,
    _validate_samples,
    load_config,
    main,
    run_experiment,
)
from evoeventmem.core.ports import ChatMessage
from evoeventmem.domain.models import EvidenceRef, MemoryKind, MemoryRecord
from evoeventmem.models.fakes import DeterministicFakeChatModel
from evoeventmem.retrieval import PackedItem

SMOKE_CONFIG = Path("configs/locomo/smoke.toml")
MAIN_CONFIG = Path("configs/locomo/main.toml")
SMOKE_METHODS = {method.value for method in Method}

RAW_TURN_CONTENTS = {
    "I went to an LGBTQ support group yesterday.",
    "I'm glad you felt supported.",
}
OFFICIAL_SUMMARY_TEXT = "Caroline went to an LGBTQ support group on 7 May 2023."


def test_smoke_config_completes_on_tiny_subset_and_finalizes(tmp_path: Path) -> None:
    config = load_config(SMOKE_CONFIG)
    assert config.provider == "deterministic_fake"

    run_dir = tmp_path / "run"
    summary = run_experiment(config, run_dir)

    assert summary.sample_validation.valid
    assert summary.sample_validation.missing_sample_ids == []
    assert summary.question_validation.valid
    assert set(summary.methods) == SMOKE_METHODS
    assert all(method_summary.sample_count == 1 for method_summary in summary.methods.values())
    assert (run_dir / "manifest.json").exists()
    assert (run_dir / "summary.json").exists()
    finalized = load_finalized(run_dir)
    assert finalized.artifact_class is ArtifactClass.SMOKE
    for method in Method:
        assert (run_dir / method.value / "predictions.jsonl").exists()
        assert (run_dir / method.value / "samples.jsonl").exists()
        assert (run_dir / method.value / "retrieval.jsonl").exists()


def test_all_questions_per_sample_are_iterated(tmp_path: Path) -> None:
    config = load_config(SMOKE_CONFIG).model_copy(
        update={"dataset_path": Path("data/raw/locomo/locomo10.json"), "sample_limit": 1}
    )
    summary = run_experiment(config, tmp_path / "run")

    assert summary.question_validation.valid
    assert summary.question_validation.expected_question_count == 199
    assert summary.question_validation.completed_question_count == 199
    sample_path = tmp_path / "run" / "samples" / "conv-26.json"
    sample = SampleResult.model_validate(json.loads(sample_path.read_text(encoding="utf-8")))
    assert len(sample.questions) == 199
    assert sample.question_count == 199
    assert "conv-26:qa:0" in sample.questions
    assert "conv-26:qa:198" in sample.questions


def test_smoke_answers_and_category_metrics(tmp_path: Path) -> None:
    config = load_config(SMOKE_CONFIG)
    summary = run_experiment(config, tmp_path / "run")

    assert summary.methods["no_memory"].exact_match == 0.0
    assert summary.methods["full_context"].exact_match == 1.0
    for method in ("vector_rag", "event_no_etec", "etec", "full"):
        assert summary.methods[method].efficiency.tokens_per_query is not None
    assert "temporal-reasoning" in summary.methods["full"].categories
    category = summary.methods["full"].categories["temporal-reasoning"]
    assert category.sample_count == 1


def test_all_five_official_categories_reported(tmp_path: Path) -> None:
    config = load_config(SMOKE_CONFIG).model_copy(
        update={"dataset_path": Path("data/raw/locomo/locomo10.json"), "sample_limit": 1}
    )
    summary = run_experiment(config, tmp_path / "run")

    assert set(LOCOMO_CATEGORY_BY_ID.values()) == {
        "single-hop",
        "temporal-reasoning",
        "open-domain-knowledge",
        "multi-hop-reasoning",
        "adversarial",
    }
    categories = summary.methods["full"].categories
    assert "single-hop" in categories
    assert "temporal-reasoning" in categories
    assert "open-domain-knowledge" in categories
    assert "multi-hop-reasoning" in categories
    assert "adversarial" in categories


def test_session_summary_is_an_official_baseline_without_retrieval(tmp_path: Path) -> None:
    config = load_config(SMOKE_CONFIG)
    run_dir = tmp_path / "run"
    run_experiment(config, run_dir)

    sample_path = run_dir / "samples" / "conv-tiny.json"
    sample = SampleResult.model_validate(json.loads(sample_path.read_text(encoding="utf-8")))
    question = sample.questions["conv-tiny:qa:0"]
    record = question.methods["session_summary"]
    assert record.retrieval is None
    assert record.write_latency_ms is None
    assert record.context is not None
    assert record.context["baseline"] == "official_session_summary"
    assert record.context["included_history_turn_ids"] == ["session_1"]


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


def test_predicted_evidence_maps_to_official_dia_ids() -> None:
    memory = MemoryRecord(
        user_id="u",
        memory_kind=MemoryKind.EVENT,
        content="Caroline attended a support group.",
        evidence_refs=[
            EvidenceRef(
                source_type="turn",
                source_id="turn-0",
                quote="I went to the support group.",
                metadata={"session_id": "session_1", "raw_turn_id": "D1:3"},
            ),
        ],
    )
    item = PackedItem(
        memory=memory,
        component_scores={"dense": 1.0},
        final_score=1.0,
        token_count=5,
        evidence_refs=memory.evidence_refs,
        reason="packed",
    )

    predicted = _evidence_from_packed_items([item])

    assert len(predicted) == 1
    assert predicted[0].source_type == "locomo_dialogue"
    assert predicted[0].source_id == "D1:3"
    assert predicted[0].locator == "qa.evidence"


def test_predicted_evidence_comes_from_raw_turn_refs_only(tmp_path: Path) -> None:
    config = load_config(SMOKE_CONFIG)
    run_dir = tmp_path / "run"
    run_experiment(config, run_dir)

    sample = _read_sample(run_dir)
    question = sample.questions["conv-tiny:qa:0"]
    record = question.methods["full"]
    predicted_ids = {evidence.source_id for evidence in record.predicted_evidence}
    assert predicted_ids
    assert predicted_ids <= {"D1:1", "D1:2"}
    assert "D1:1" in predicted_ids
    assert all(evidence.source_type == "locomo_dialogue" for evidence in record.predicted_evidence)
    assert record.evidence_recall == 1.0


def test_event_structure_is_a_labeled_structural_proxy(tmp_path: Path) -> None:
    config = load_config(SMOKE_CONFIG)
    summary = run_experiment(config, tmp_path / "run")

    raw = summary.event_structure["raw"]
    assert raw.metric_kind == "structural_proxy"
    assert raw.matching_policy == "session_level_token_f1_ge_0.5"
    assert raw.official_event_count == 1
    assert raw.extracted_event_count == 2
    assert raw.coverage == 1.0
    assert raw.precision == 0.5
    assert raw.f1 == pytest.approx(2.0 * 0.5 / 1.5)
    assert summary.event_structure["etec"].metric_kind == "structural_proxy"


def test_etec_consolidation_never_inflates_extracted_structure(tmp_path: Path) -> None:
    config = load_config(SMOKE_CONFIG).model_copy(
        update={"dataset_path": Path("data/raw/locomo/locomo10.json"), "sample_limit": 1}
    )
    summary = run_experiment(config, tmp_path / "run")

    raw = summary.event_structure["raw"]
    etec = summary.event_structure["etec"]
    assert raw.extracted_event_count >= 1
    assert etec.extracted_event_count <= raw.extracted_event_count
    assert 0.0 <= etec.coverage <= 1.0


def test_reference_time_is_last_session_timestamp(tmp_path: Path) -> None:
    config = load_config(SMOKE_CONFIG)
    run_dir = tmp_path / "run"
    run_experiment(config, run_dir)

    sample = _read_sample(run_dir)
    question = sample.questions["conv-tiny:qa:0"]
    assert sample.reference_time == "2023-05-08T13:56:00+00:00"
    assert question.reference_time == "2023-05-08T13:56:00+00:00"
    for method in ("vector_rag", "event_no_etec", "etec", "full"):
        assert question.methods[method].retrieval is not None


def test_vector_rag_indexes_raw_dialogue_turns_only(tmp_path: Path) -> None:
    config = load_config(SMOKE_CONFIG)
    run_dir = tmp_path / "run"
    run_experiment(config, run_dir)

    sample = _read_sample(run_dir)
    assert sample.ingestion["raw_turn"]["input_kind"] == "raw_turn"
    record = sample.questions["conv-tiny:qa:0"].methods["vector_rag"]
    assert record.retrieval is not None
    for item in record.retrieval["packed_items"]:
        assert item["content"] in RAW_TURN_CONTENTS
        for ref in item["evidence_refs"]:
            assert ref["source_type"] == "turn"
            assert ref["raw_turn_id"]
    assert record.context is not None
    assert record.context["input_kind"] == "raw_turn"
    assert "snapshot_id" not in record.context


def test_event_methods_share_one_extraction_snapshot(tmp_path: Path) -> None:
    config = load_config(SMOKE_CONFIG)
    run_dir = tmp_path / "run"
    run_experiment(config, run_dir)

    sample = _read_sample(run_dir)
    snapshot_id = sample.ingestion["event"]["snapshot_id"]
    assert snapshot_id.startswith("sha256:")
    snapshot_path = run_dir / "samples" / "conv-tiny.extraction_snapshot.json"
    assert snapshot_path.exists()
    ids = {
        sample.questions["conv-tiny:qa:0"].methods[method].context["snapshot_id"]
        for method in ("event_no_etec", "etec", "full")
    }
    assert ids == {snapshot_id}


def test_extracted_events_carry_raw_turn_evidence_only(tmp_path: Path) -> None:
    config = load_config(SMOKE_CONFIG)
    run_dir = tmp_path / "run"
    run_experiment(config, run_dir)

    sample = _read_sample(run_dir)
    retrieval = sample.questions["conv-tiny:qa:0"].methods["full"].retrieval
    assert retrieval is not None
    for item in retrieval["packed_items"]:
        assert item["content"] in RAW_TURN_CONTENTS
        assert item["content"] != OFFICIAL_SUMMARY_TEXT
        for ref in item["evidence_refs"]:
            assert ref["source_type"] == "turn"
            assert ref["raw_turn_id"] is not None
            assert ref["locator"].startswith("chars=")
    joined = " ".join(
        item["content"]
        for method in ("vector_rag", "event_no_etec", "etec", "full")
        for item in (sample.questions["conv-tiny:qa:0"].methods[method].retrieval or {}).get(
            "packed_items", []
        )
    )
    assert OFFICIAL_SUMMARY_TEXT not in joined


def test_extraction_input_never_contains_official_summaries(tmp_path: Path) -> None:
    config = load_config(SMOKE_CONFIG)
    spy = RecordingExtractor(FakeEventExtractor())
    run_experiment(config, tmp_path / "run", extractor=spy)

    assert spy.calls == 1
    request = spy.requests[0]
    assert request.event_summaries == []
    assert request.observations == []


def test_resume_skips_completed_samples_without_overwriting(tmp_path: Path) -> None:
    config = load_config(SMOKE_CONFIG)
    run_dir = tmp_path / "run"
    first = run_experiment(config, run_dir)
    sample_path = run_dir / "samples" / "conv-tiny.json"
    original = sample_path.read_bytes()

    second = run_experiment(config, run_dir)

    assert sample_path.read_bytes() == original
    assert second.sample_validation.valid
    assert second.sample_validation.completed_sample_count == 1
    assert second.methods == first.methods


def test_manifest_drift_refused(tmp_path: Path) -> None:
    config = load_config(SMOKE_CONFIG)
    run_dir = tmp_path / "run"
    run_experiment(config, run_dir)

    drifted = config.model_copy(update={"max_input_tokens": 512})
    with pytest.raises(ValueError, match="manifest drift"):
        run_experiment(drifted, run_dir)


def test_manifest_resolves_expected_ids_and_independent_providers(tmp_path: Path) -> None:
    config = load_config(SMOKE_CONFIG)
    run_dir = tmp_path / "run"
    run_experiment(config, run_dir)

    manifest = RunManifest.model_validate_json(
        (run_dir / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest.dataset == "locomo"
    assert manifest.expected_sample_ids == ["conv-tiny"]
    assert manifest.expected_question_ids == ["conv-tiny:qa:0"]
    assert manifest.reader.model_id == "deterministic-local-fake-reader"
    assert manifest.extractor.model_id == "deterministic-local-fake-extractor"
    assert manifest.embedding.model_id == "deterministic-local-embedding"
    assert manifest.metadata["vector_input_kind"] == "raw_turn"
    assert manifest.metadata["structural_proxy_label"] == "structural_proxy"
    assert manifest.metadata["structural_matching_policy"] == "session_level_token_f1_ge_0.5"


def test_run_dir_cli_first_run_and_identical_resume_address_same_directory(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "runs" / "smoke"
    assert main(["--config", str(SMOKE_CONFIG), "--run-dir", str(run_dir)]) == 0
    assert main(["--config", str(SMOKE_CONFIG), "--run-dir", str(run_dir)]) == 0
    assert (run_dir / "manifest.json").exists()
    assert (run_dir / "finalized" / "FINALIZED.json").exists()
    assert (run_dir / "samples" / "conv-tiny.json").exists()


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


def test_summary_detects_missing_and_duplicate_ids() -> None:
    expected = ["a", "b", "c"]
    completed = ["a", "b", "b"]

    validation = _validate_samples(expected, completed)
    assert validation.valid is False
    assert validation.missing_sample_ids == ["c"]
    assert validation.duplicate_sample_ids == ["b"]

    question_validation = _validate_question_ids(expected, completed)
    assert question_validation.valid is False
    assert question_validation.missing_question_ids == ["c"]
    assert question_validation.duplicate_question_ids == ["b"]


def test_main_config_records_independent_models() -> None:
    config = load_config(MAIN_CONFIG)

    assert config.provider == "openai_compatible"
    assert config.providers.reader.model_id == "deepseek-v4-flash"
    assert config.providers.reader.base_url == "https://api.deepseek.com"
    assert config.providers.reader.api_key_env == "OPENAI_API_KEY"
    assert config.providers.reader.thinking == "disabled"
    assert config.providers.extractor.model_id == "deepseek-v4-flash"
    assert config.providers.extractor.base_url == "https://api.deepseek.com"
    assert config.providers.embedding.model_id == "qwen3-embedding-0.6b"
    assert config.providers.embedding.base_url == "http://127.0.0.1:11436/v1"
    assert config.providers.embedding.api_key_env == "EMBEDDING_API_KEY"
    assert set(config.methods) == SMOKE_METHODS
    assert config.structural_match_f1_threshold == 0.5


def test_deterministic_fake_answer_path(tmp_path: Path) -> None:
    model = DeterministicFakeChatModel()
    content = "Context: X\nQuestion: When did Caroline go to the support group yesterday?"
    response = model.generate([ChatMessage(role="user", content=content)])
    assert response.text == "7 May 2023"


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
    payload = json.loads((run_dir / "samples" / "conv-tiny.json").read_text(encoding="utf-8"))
    return SampleResult.model_validate(payload)


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
