from __future__ import annotations

import json

from benchmarks.analysis.validate_report import (
    hash_json,
    validate_runs,
)
from tests.analysis.conftest import build_question_artifacts, build_run


def _valid(report) -> bool:
    return report.valid


def test_valid_run_passes(synthetic_run) -> None:
    report = validate_runs(synthetic_run.parent)
    assert report.valid
    assert all(
        issue.severity != "error" for issue in report.run_issues
    )
    assert report.pair_issues == []


def test_config_hash_mismatch_is_caught(synthetic_run) -> None:
    summary_path = synthetic_run / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["config_hash"] = "sha256:tampered"
    summary_path.write_text(json.dumps(summary), encoding="utf-8")
    report = validate_runs(synthetic_run.parent)
    assert not report.valid
    assert any(
        issue.code == "config_hash_mismatch" and issue.severity == "error"
        for issue in report.run_issues
    )


def test_incomplete_questions_are_caught(synthetic_run) -> None:
    summary_path = synthetic_run / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["question_validation"]["valid"] = False
    summary["question_validation"]["completed_question_count"] = 10
    summary_path.write_text(json.dumps(summary), encoding="utf-8")
    report = validate_runs(synthetic_run.parent)
    assert not report.valid
    assert any(issue.code == "incomplete_questions" for issue in report.run_issues)


def test_missing_derived_artifact_is_caught(synthetic_run) -> None:
    (synthetic_run / "full" / "retrieval.jsonl").unlink()
    report = validate_runs(synthetic_run.parent)
    assert not report.valid
    assert any(issue.code == "missing_derived_artifact" for issue in report.run_issues)


def test_incompatible_pair_is_caught(tmp_path) -> None:
    root = tmp_path / "runs"
    build_run(root / "a", max_input_tokens=4096)
    build_question_artifacts(root / "a")
    build_run(root / "b", max_input_tokens=2048)
    build_question_artifacts(root / "b")
    report = validate_runs(root)
    assert not report.valid
    assert any(
        issue.code == "incompatible_config" and "max_input_tokens" in issue.message
        for issue in report.pair_issues
    )


def test_embedding_model_mismatch_is_caught(tmp_path) -> None:
    root = tmp_path / "runs"
    build_run(root / "a", embedding_model_id="qwen3-embedding-0.6b")
    build_question_artifacts(root / "a")
    build_run(root / "b", embedding_model_id="bge-m3")
    build_question_artifacts(root / "b")
    report = validate_runs(root)
    assert not report.valid
    assert any(
        issue.code == "incompatible_config" and "embedding_model_id" in issue.message
        for issue in report.pair_issues
    )


def test_subset_scope_is_warned(tmp_path) -> None:
    root = tmp_path / "runs"
    build_run(root / "a", sample_limit=1)
    build_question_artifacts(root / "a")
    build_run(root / "b", sample_limit=None)
    build_question_artifacts(root / "b")
    report = validate_runs(root)
    assert report.valid
    assert any(issue.code == "subset_scope" for issue in report.pair_issues)


def test_two_identical_runs_are_compatible(tmp_path) -> None:
    root = tmp_path / "runs"
    build_run(root / "a")
    build_question_artifacts(root / "a")
    build_run(root / "b")
    build_question_artifacts(root / "b")
    report = validate_runs(root)
    assert report.valid
    assert report.pair_issues == []


def test_hash_json_is_stable() -> None:
    assert hash_json({"b": 1, "a": 2}) == hash_json({"a": 2, "b": 1})
    assert hash_json({"a": 2}).startswith("sha256:")


def test_validation_artifact_is_written(synthetic_run) -> None:
    report = validate_runs(synthetic_run.parent)
    from benchmarks.analysis.validate_report import write_validation_artifact

    path = write_validation_artifact(report, synthetic_run.parent)
    assert path.is_file()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["valid"] is True


def test_incompatible_runs_report_errors(tmp_path) -> None:
    root = tmp_path / "runs"
    build_run(root / "a", max_input_tokens=4096)
    build_question_artifacts(root / "a")
    build_run(root / "b", max_input_tokens=1024)
    build_question_artifacts(root / "b")
    report = validate_runs(root)
    error_messages = [
        issue.message
        for issue in report.pair_issues
        if issue.severity == "error"
    ]
    assert error_messages, "expected at least one incompatible-pair error"
