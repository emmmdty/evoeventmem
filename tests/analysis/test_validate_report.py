from __future__ import annotations

import json

from benchmarks.analysis.validate_report import (
    hash_json,
    validate_analysis_inputs,
    validate_runs,
)
from tests.analysis.conftest import build_question_artifacts, build_run


def _valid(report) -> bool:
    return report.valid


def test_valid_run_passes(synthetic_run) -> None:
    report = validate_runs(synthetic_run.parent)
    assert report.valid
    assert all(issue.severity != "error" for issue in report.run_issues)
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
    error_messages = [issue.message for issue in report.pair_issues if issue.severity == "error"]
    assert error_messages, "expected at least one incompatible-pair error"


# --------------------------------------------------------------------------- #
# C2 dataset-neutral validation layer.
# --------------------------------------------------------------------------- #


def test_analysis_validator_accepts_both_datasets(analysis_fixture) -> None:
    result = validate_analysis_inputs(
        analysis_fixture["source_runs"],
        controlled_run=analysis_fixture["controlled_run"],
        ablation_runs=analysis_fixture["ablation_runs"],
    )
    assert result.valid
    assert result.error_codes() == []


def test_analysis_validator_rejects_zero_source_runs() -> None:
    result = validate_analysis_inputs([])
    assert not result.valid
    assert any(issue.code == "zero_source_runs" for issue in result.issues)


def test_analysis_validator_never_writes_below_source_run(analysis_fixture) -> None:
    def snapshot() -> dict[str, tuple[int, int]]:
        root = analysis_fixture["runs_root"]
        return {
            str(path): (path.stat().st_size, path.stat().st_mtime_ns)
            for path in sorted(root.rglob("*"))
            if path.is_file()
        }

    before = snapshot()
    result = validate_analysis_inputs(
        analysis_fixture["source_runs"],
        controlled_run=analysis_fixture["controlled_run"],
        ablation_runs=analysis_fixture["ablation_runs"],
    )
    assert result.valid
    assert snapshot() == before


def test_analysis_validator_allows_different_model_stacks(analysis_fixture) -> None:
    result = validate_analysis_inputs(analysis_fixture["source_runs"])
    assert result.valid
    assert not any(issue.code == "incompatible_within_dataset" for issue in result.issues)


def test_analysis_validator_catches_within_dataset_incompatibility(tmp_path) -> None:
    from tests.analysis.conftest import build_synthetic_run

    first = build_synthetic_run(tmp_path / "a", dataset="locomo")
    second = build_synthetic_run(
        tmp_path / "b",
        dataset="locomo",
        reader_model_id="reader-model-z",
        config_hash="sha256:config-b",
    )
    result = validate_analysis_inputs([first["run_dir"], second["run_dir"]])
    assert not result.valid
    assert any(
        issue.code == "incompatible_within_dataset" and "reader" in issue.message
        for issue in result.issues
    )


def test_analysis_validator_checks_ablation_links(analysis_fixture) -> None:
    result = validate_analysis_inputs(
        analysis_fixture["source_runs"],
        controlled_run=analysis_fixture["controlled_run"],
        ablation_runs=analysis_fixture["ablation_runs"],
    )
    assert result.valid
    assert result.controlled is not None
    for ablation in result.ablations:
        assert len(ablation.arms) >= 7
        for arm in ablation.arms.values():
            assert (
                arm.manifest.controlled_run_hash
                == result.controlled.finalization.finalization_hash()
            )


def test_analysis_validator_returns_structured_issues(analysis_fixture) -> None:
    run_dir = analysis_fixture["longmemeval"]["run_dir"]
    summary = run_dir / "summary.json"
    if not summary.exists():
        (run_dir / "full" / "samples.jsonl").unlink()
    result = validate_analysis_inputs([run_dir])
    payload = result.as_dict()
    assert payload["valid"] is False
    assert payload["issues"]
    for issue in payload["issues"]:
        assert issue["code"]
        assert issue["severity"] in ("error", "warning")
        assert issue["message"]


# --------------------------------------------------------------------------- #
# C8 final CLI contract.
# --------------------------------------------------------------------------- #


def _cli_args(analysis_fixture, artifact_root) -> list[str]:
    args = [
        "--config",
        "configs/analysis/main.toml",
        "--source-run",
        str(analysis_fixture["source_runs"][0]),
        "--source-run",
        str(analysis_fixture["source_runs"][1]),
        "--controlled-run",
        str(analysis_fixture["controlled_run"]),
        "--ablation-run",
        str(analysis_fixture["ablation_runs"][0]),
        "--ablation-run",
        str(analysis_fixture["ablation_runs"][1]),
        "--artifact-root",
        str(artifact_root),
    ]
    return args


def test_validator_cli_verifies_generated_artifact(
    analysis_fixture, tmp_path, capsys
) -> None:
    from benchmarks.analysis.report import main as report_main

    output_root = tmp_path / "artifacts" / "analysis"
    report_args = [
        "--config",
        "configs/analysis/main.toml",
        "--source-run",
        str(analysis_fixture["source_runs"][0]),
        "--source-run",
        str(analysis_fixture["source_runs"][1]),
        "--controlled-run",
        str(analysis_fixture["controlled_run"]),
        "--ablation-run",
        str(analysis_fixture["ablation_runs"][0]),
        "--ablation-run",
        str(analysis_fixture["ablation_runs"][1]),
        "--output-root",
        str(output_root),
    ]
    assert report_main(report_args) == 0
    report_payload = json.loads(capsys.readouterr().out)

    from benchmarks.analysis.validate_report import main as validator_main

    assert validator_main(_cli_args(analysis_fixture, output_root)) == 0
    validator_payload = json.loads(capsys.readouterr().out)
    assert validator_payload["analysis_id"] == report_payload["analysis_id"]
    assert validator_payload["artifact_dir"] == report_payload["artifact_dir"]
    assert validator_payload["valid"] is True
    assert validator_payload["output_files"] > 0


def test_validator_cli_missing_source_refuses_with_stable_code(
    analysis_fixture, tmp_path, capsys
) -> None:
    from benchmarks.analysis.validate_report import main as validator_main

    missing = tmp_path / "no-such-run"
    args = [
        "--config",
        "configs/analysis/main.toml",
        "--source-run",
        str(analysis_fixture["source_runs"][0]),
        "--source-run",
        str(missing),
        "--artifact-root",
        str(tmp_path / "artifacts" / "analysis"),
    ]
    assert validator_main(args) != 0
    stderr = capsys.readouterr().err
    assert "error[missing_run_dir]" in stderr


def test_validator_cli_legacy_report_refuses_with_stable_code(
    analysis_fixture, tmp_path, capsys
) -> None:
    from benchmarks.analysis.validate_report import main as validator_main

    legacy = tmp_path / "runs" / "main" / "report"
    legacy.mkdir(parents=True)
    (legacy / "report.md").write_text("# legacy\n", encoding="utf-8")
    args = [
        "--config",
        "configs/analysis/main.toml",
        "--source-run",
        str(analysis_fixture["source_runs"][0]),
        "--source-run",
        str(legacy),
        "--artifact-root",
        str(tmp_path / "artifacts" / "analysis"),
    ]
    assert validator_main(args) != 0
    stderr = capsys.readouterr().err
    assert "error[legacy_report_input]" in stderr


def test_validator_cli_missing_artifact_fails(
    analysis_fixture, tmp_path, capsys
) -> None:
    from benchmarks.analysis.validate_report import main as validator_main

    args = _cli_args(analysis_fixture, tmp_path / "artifacts" / "analysis")
    assert validator_main(args) != 0
    stderr = capsys.readouterr().err
    assert "error[missing_analysis_finalization]" in stderr


def test_validator_cli_detects_drifted_artifact(
    analysis_fixture, tmp_path, capsys
) -> None:
    from benchmarks.analysis.report import main as report_main
    from benchmarks.analysis.validate_report import main as validator_main

    output_root = tmp_path / "artifacts" / "analysis"
    report_args = [
        "--config",
        "configs/analysis/main.toml",
        "--source-run",
        str(analysis_fixture["source_runs"][0]),
        "--source-run",
        str(analysis_fixture["source_runs"][1]),
        "--controlled-run",
        str(analysis_fixture["controlled_run"]),
        "--ablation-run",
        str(analysis_fixture["ablation_runs"][0]),
        "--ablation-run",
        str(analysis_fixture["ablation_runs"][1]),
        "--output-root",
        str(output_root),
    ]
    assert report_main(report_args) == 0
    report_payload = json.loads(capsys.readouterr().out)
    from pathlib import Path

    artifact = Path(report_payload["artifact_dir"])
    (artifact / "report.md").write_text("# tampered\n", encoding="utf-8")
    assert validator_main(_cli_args(analysis_fixture, output_root)) != 0
    stderr = capsys.readouterr().err
    assert "error[analysis_output_drift]" in stderr


def test_validator_cli_writes_nothing(analysis_fixture, tmp_path, capsys) -> None:
    from benchmarks.analysis.report import main as report_main
    from benchmarks.analysis.validate_report import main as validator_main

    output_root = tmp_path / "artifacts" / "analysis"
    report_args = [
        "--config",
        "configs/analysis/main.toml",
        "--source-run",
        str(analysis_fixture["source_runs"][0]),
        "--source-run",
        str(analysis_fixture["source_runs"][1]),
        "--controlled-run",
        str(analysis_fixture["controlled_run"]),
        "--ablation-run",
        str(analysis_fixture["ablation_runs"][0]),
        "--ablation-run",
        str(analysis_fixture["ablation_runs"][1]),
        "--output-root",
        str(output_root),
    ]
    assert report_main(report_args) == 0
    capsys.readouterr()

    def snapshot() -> dict[str, tuple[int, int]]:
        return {
            str(path): (path.stat().st_size, path.stat().st_mtime_ns)
            for path in sorted(output_root.rglob("*"))
            if path.is_file()
        }

    before = snapshot()
    assert validator_main(_cli_args(analysis_fixture, output_root)) == 0
    capsys.readouterr()
    assert snapshot() == before
