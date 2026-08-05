from __future__ import annotations

import json
from pathlib import Path

from benchmarks.locomo.run import (
    LOCOMO_CATEGORY_BY_ID,
    Method,
    SampleResult,
    _evidence_from_packed_items,
    _make_models,
    _validate_question_ids,
    _validate_samples,
    load_config,
    run_experiment,
)
from evoeventmem.core.ports import ChatMessage
from evoeventmem.domain.models import EvidenceRef, MemoryKind, MemoryRecord
from evoeventmem.models.fakes import DeterministicFakeChatModel
from evoeventmem.retrieval import PackedItem

SMOKE_CONFIG = Path("configs/locomo/smoke.toml")
MAIN_CONFIG = Path("configs/locomo/main.toml")
SMOKE_METHODS = {method.value for method in Method}


def test_smoke_config_completes_on_tiny_subset(tmp_path: Path) -> None:
    config = load_config(SMOKE_CONFIG)
    assert config.provider == "deterministic_fake"

    run_dir = tmp_path / "run"
    summary = run_experiment(config, run_dir)

    assert summary.sample_validation.valid
    assert summary.sample_validation.missing_sample_ids == []
    assert summary.question_validation.valid
    assert set(summary.methods) == SMOKE_METHODS
    assert all(method_summary.sample_count == 1 for method_summary in summary.methods.values())
    assert (run_dir / "summary.json").exists()
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


def test_predicted_evidence_maps_to_official_dia_ids() -> None:
    memory = MemoryRecord(
        user_id="u",
        memory_kind=MemoryKind.EVENT,
        content="Caroline attended a support group.",
        evidence_refs=[
            EvidenceRef(
                source_type="event_summary",
                source_id="summary-0",
                metadata={"session_id": "session_1"},
            ),
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


def test_evidence_metrics_use_official_dia_ids(tmp_path: Path) -> None:
    config = load_config(SMOKE_CONFIG)
    summary = run_experiment(config, tmp_path / "run")

    sample_path = tmp_path / "run" / "samples" / "conv-tiny.json"
    sample = SampleResult.model_validate(json.loads(sample_path.read_text(encoding="utf-8")))
    question = sample.questions["conv-tiny:qa:0"]
    record = question.methods["full"]
    assert record.predicted_evidence == []
    assert record.evidence_recall == 0.0
    assert record.evidence_precision == 0.0
    assert summary.methods["full"].evidence_f1 == 0.0


def test_event_structure_metrics_compare_official_and_extracted(tmp_path: Path) -> None:
    config = load_config(SMOKE_CONFIG)
    summary = run_experiment(config, tmp_path / "run")

    raw = summary.event_structure["raw"]
    assert raw.official_event_count == 1
    assert raw.extracted_event_count == 1
    assert raw.coverage == 1.0
    assert raw.precision == 1.0
    assert raw.f1 == 1.0
    assert summary.event_structure["etec"].coverage == 1.0


def test_etec_consolidation_is_reflected_in_structure(tmp_path: Path) -> None:
    config = load_config(SMOKE_CONFIG).model_copy(
        update={"dataset_path": Path("data/raw/locomo/locomo10.json"), "sample_limit": 1}
    )
    summary = run_experiment(config, tmp_path / "run")

    raw = summary.event_structure["raw"]
    etec = summary.event_structure["etec"]
    assert raw.coverage == 1.0
    assert 0.0 < etec.coverage <= raw.coverage
    assert raw.extracted_event_count >= 1


def test_reference_time_is_last_session_timestamp(tmp_path: Path) -> None:
    config = load_config(SMOKE_CONFIG)
    run_dir = tmp_path / "run"
    run_experiment(config, run_dir)

    sample_path = run_dir / "samples" / "conv-tiny.json"
    sample = SampleResult.model_validate(json.loads(sample_path.read_text(encoding="utf-8")))
    question = sample.questions["conv-tiny:qa:0"]
    assert sample.reference_time == "2023-05-08T13:56:00+00:00"
    assert question.reference_time == "2023-05-08T13:56:00+00:00"
    for method in ("vector_rag", "event_no_etec", "etec", "full"):
        assert question.methods[method].retrieval is not None


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


def test_main_config_records_dataset_and_versions() -> None:
    config = load_config(MAIN_CONFIG)

    assert config.provider == "openai_compatible"
    assert config.embedding_provider == "openai_compatible"
    assert config.live_provider is not None
    assert config.live_provider.api_key_env == "OPENAI_API_KEY"
    assert config.live_provider.base_url == "https://ark.cn-beijing.volces.com/api/plan/v3"
    assert config.live_provider.chat_model == "minimax-m3"
    assert config.live_provider.embedding_model == "bge-m3"
    assert config.live_provider.embedding_base_url == "http://127.0.0.1:11435/v1"
    assert set(config.methods) == SMOKE_METHODS


def test_live_chat_with_remote_embeddings(monkeypatch, tmp_path: Path) -> None:
    config = load_config(MAIN_CONFIG)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("EMBEDDING_API_KEY", "test-embed-key")

    chat_model, embedding_model = _make_models(config, tmp_path / "run")

    assert chat_model.model_id == "minimax-m3"
    assert embedding_model.model_id == "bge-m3"


def test_live_chat_with_deterministic_embeddings(monkeypatch, tmp_path: Path) -> None:
    config = load_config(SMOKE_CONFIG).model_copy(
        update={
            "provider": "openai_compatible",
            "embedding_provider": "deterministic_fake",
            "live_provider": load_config(MAIN_CONFIG).live_provider,
        }
    )
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    chat_model, embedding_model = _make_models(config, tmp_path / "run")

    assert chat_model.model_id == "minimax-m3"
    assert embedding_model.model_id == "deterministic-local-embedding"


def test_extracted_events_carry_summary_evidence_refs(tmp_path: Path) -> None:
    config = load_config(SMOKE_CONFIG)
    run_dir = tmp_path / "run"
    run_experiment(config, run_dir)

    sample_path = run_dir / "samples" / "conv-tiny.json"
    sample = SampleResult.model_validate(json.loads(sample_path.read_text(encoding="utf-8")))
    assert sample.ingestion["raw"]["candidate_count"] == 1
    assert sample.ingestion["raw"]["memory_count"] == 1
    retrieval = sample.questions["conv-tiny:qa:0"].methods["full"].retrieval
    assert retrieval is not None
    item = retrieval["packed_items"][0]
    assert item["content"] == "Caroline went to an LGBTQ support group on 7 May 2023."
    assert item["evidence_refs"][0]["source_type"] == "event_summary"
    assert item["evidence_refs"][0]["raw_turn_id"] is None


def test_deterministic_fake_answer_path(tmp_path: Path) -> None:
    model = DeterministicFakeChatModel()
    content = "Context: X\nQuestion: When did Caroline go to the support group yesterday?"
    response = model.generate([ChatMessage(role="user", content=content)])
    assert response.text == "7 May 2023"


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
