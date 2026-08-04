from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmarks.retrieval_smoke import run_retrieval_smoke

ANNOTATIONS = Path("tests/fixtures/retrieval/m12_retrieval_smoke.json")


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_retrieval_smoke_runs_all_strategies_with_full_metric_coverage(tmp_path: Path) -> None:
    summary = run_retrieval_smoke(ANNOTATIONS, tmp_path / "retrieval-smoke")
    results = _read_jsonl(Path(summary.results_path))
    case_count = len(_fixture_payload()["cases"])

    assert summary.sample_count == case_count * 3
    assert summary.intent_accuracy == 1.0
    assert summary.budget_compliance == 1.0
    assert summary.provenance_coverage == 1.0
    assert summary.decomposition_coverage == 1.0
    assert summary.superseded_compliance == 1.0
    assert {record["strategy"] for record in results} == {
        "fixed_vector",
        "fixed_hybrid",
        "qemr",
    }
    assert all(record["budget_compliant"] is True for record in results)


def test_retrieval_smoke_records_score_decompositions_and_exclusions(tmp_path: Path) -> None:
    summary = run_retrieval_smoke(ANNOTATIONS, tmp_path / "retrieval-smoke")

    for record in _read_jsonl(Path(summary.results_path)):
        for item in record["packed_items"]:
            assert item["component_scores"]
            assert item["evidence_refs"]
            assert item["final_score"] >= 0.0
            assert item["reason"]
        assert isinstance(record["candidates"], list)
        assert isinstance(record["exclusions"], list)


def test_retrieval_smoke_tight_budget_never_exceeds_budget(tmp_path: Path) -> None:
    payload = _fixture_payload()
    for case in payload["cases"]:
        case["budget_tokens"] = 2
    annotation_path = _write_annotations(tmp_path / "tight-budget.json", payload)

    summary = run_retrieval_smoke(annotation_path, tmp_path / "tight-budget")

    assert summary.budget_compliance == 1.0
    for record in _read_jsonl(Path(summary.results_path)):
        assert record["total_tokens"] <= record["budget_tokens"]


def test_retrieval_smoke_excludes_synthetic_memory_without_evidence(tmp_path: Path) -> None:
    payload = _fixture_payload()
    case = payload["cases"][0]
    synthetic = dict(case["memories"][0])
    synthetic["memory_id"] = "79000000-0000-0000-0000-000000000099"
    synthetic["synthetic"] = True
    synthetic.pop("evidence_refs")
    case["memories"].append(synthetic)
    annotation_path = _write_annotations(tmp_path / "synthetic.json", payload)

    summary = run_retrieval_smoke(annotation_path, tmp_path / "synthetic")

    assert summary.budget_compliance == 1.0
    records = [
        record
        for record in _read_jsonl(Path(summary.results_path))
        if record["case_id"] == "semantic_favorite_color"
    ]
    assert len(records) == 3
    for record in records:
        assert synthetic["memory_id"] not in record["selected_memory_ids"]
        assert any(
            exclusion["memory_id"] == synthetic["memory_id"]
            and exclusion["reason"] == "missing_evidence_refs"
            for exclusion in record["exclusions"]
        )


def test_retrieval_smoke_detects_router_intent_drift(tmp_path: Path) -> None:
    payload = _fixture_payload()
    payload["cases"][0]["expected_intent"] = "graph"
    annotation_path = _write_annotations(tmp_path / "intent-drift.json", payload)

    summary = run_retrieval_smoke(annotation_path, tmp_path / "intent-drift")

    assert summary.intent_accuracy < 1.0


def test_retrieval_smoke_rejects_invalid_annotations(tmp_path: Path) -> None:
    payload = _fixture_payload()
    payload["cases"][0]["budget_tokens"] = 0
    annotation_path = _write_annotations(tmp_path / "invalid.json", payload)

    with pytest.raises(ValueError, match="budget_tokens"):
        run_retrieval_smoke(annotation_path, tmp_path / "invalid")


def _fixture_payload() -> dict[str, object]:
    return json.loads(ANNOTATIONS.read_text(encoding="utf-8"))


def _write_annotations(path: Path, payload: dict[str, object]) -> Path:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path
