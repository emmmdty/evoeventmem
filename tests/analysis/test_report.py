"""C7/C8: report rendering and content-addressed artifact behavior."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmarks.analysis.finalization import (
    load_analysis_finalization,
    load_config,
    snapshot_source_runs,
)
from benchmarks.analysis.loaders import load_base_run
from benchmarks.analysis.report import (
    generate_report,
    method_overview,
    render_markdown,
    write_report_files,
)


@pytest.fixture
def generated(analysis_fixture, tmp_path) -> dict:
    output_root = tmp_path / "artifacts" / "analysis"
    analysis_id = generate_report(
        config_path=Path("configs/analysis/main.toml"),
        source_runs=analysis_fixture["source_runs"],
        controlled_run=analysis_fixture["controlled_run"],
        ablation_runs=analysis_fixture["ablation_runs"],
        output_root=output_root,
    )
    return {
        "analysis_id": analysis_id,
        "output_root": output_root,
        "artifact": output_root / analysis_id,
        "source_runs": analysis_fixture["source_runs"],
        "controlled_run": analysis_fixture["controlled_run"],
        "ablation_runs": analysis_fixture["ablation_runs"],
    }


def test_report_writes_below_output_root_and_seals(generated) -> None:
    artifact = generated["artifact"]
    assert artifact.is_dir()
    assert (artifact / "report.md").is_file()
    assert (artifact / "report.json").is_file()
    assert (artifact / "tables" / "claims.csv").is_file()
    assert (artifact / "tables" / "overall_longmemeval.csv").is_file()
    assert (artifact / "tables" / "categories_locomo.csv").is_file()
    assert (artifact / "plots" / "overall_em_locomo.svg").is_file()
    assert (artifact / "review_longmemeval.jsonl").is_file()
    seal = load_analysis_finalization(artifact)
    assert seal.analysis_id == generated["analysis_id"]
    assert set(seal.output_hashes) >= {
        "report.md",
        "report.json",
        "tables/claims.csv",
    }


def test_report_json_structure(generated) -> None:
    payload = json.loads((generated["artifact"] / "report.json").read_text(encoding="utf-8"))
    assert payload["analysis_id"] == generated["analysis_id"]
    assert payload["headline"] is not None
    assert set(payload["datasets"]) == {"longmemeval", "locomo"}
    for _dataset, section in payload["datasets"].items():
        assert section["run_id"]
        assert section["config_hash"]
        assert section["methods"]
        assert section["categories"]
        assert section["overall"]
        assert section["claims"]
        assert section["taxonomy"]["failure_total"] > 0
        assert section["taxonomy"]["sample"]
        assert section["taxonomy"]["coverage"]["reviewed_count"] == 0


def test_rerun_validates_without_mutation(generated, tmp_path) -> None:
    from benchmarks.analysis.finalization import derive_analysis_id

    config = load_config(Path("configs/analysis/main.toml"))
    analysis_id = derive_analysis_id(
        config,
        generated["source_runs"],
        generated["controlled_run"],
        generated["ablation_runs"],
    )
    assert analysis_id == generated["analysis_id"]
    before = {
        str(path): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in sorted(generated["artifact"].rglob("*"))
        if path.is_file()
    }
    seal = load_analysis_finalization(generated["artifact"])
    assert seal.analysis_id == analysis_id
    after = {
        str(path): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in sorted(generated["artifact"].rglob("*"))
        if path.is_file()
    }
    assert before == after


def test_source_runs_unchanged_after_generation(analysis_fixture, tmp_path) -> None:
    run_dirs = [
        *analysis_fixture["source_runs"],
        analysis_fixture["controlled_run"],
        *analysis_fixture["ablation_runs"],
    ]
    snapshot = snapshot_source_runs(run_dirs)
    generate_report(
        config_path=Path("configs/analysis/main.toml"),
        source_runs=analysis_fixture["source_runs"],
        controlled_run=analysis_fixture["controlled_run"],
        ablation_runs=analysis_fixture["ablation_runs"],
        output_root=tmp_path / "artifacts" / "analysis",
    )
    assert snapshot.verify()


def test_method_overview_is_manifest_driven(analysis_fixture) -> None:
    loaded = load_base_run(analysis_fixture["longmemeval"]["run_dir"])
    overview = method_overview(loaded)
    assert [row["method"] for row in overview] == list(loaded.manifest.methods)
    for row in overview:
        assert 0.0 <= row["exact_match"] <= 1.0
        assert row["questions"] == len(loaded.manifest.expected_question_ids)


def test_report_markdown_renders_structured_data(generated) -> None:
    markdown = (generated["artifact"] / "report.md").read_text(encoding="utf-8")
    assert f"analysis_id: `{generated['analysis_id']}`" in markdown
    assert "## Two-dataset headline" in markdown
    assert "## Dataset: longmemeval" in markdown
    assert "## Dataset: locomo" in markdown
    assert "retrieval-diagnostic" in markdown or "retrieval" in markdown
    assert "reviewed" in markdown


def test_report_refuses_invalid_inputs(analysis_fixture, tmp_path) -> None:
    from benchmarks.analysis.finalization import AnalysisInputError

    run_dir = analysis_fixture["longmemeval"]["run_dir"]
    (run_dir / "full" / "predictions.jsonl").unlink()
    with pytest.raises(AnalysisInputError):
        generate_report(
            config_path=Path("configs/analysis/main.toml"),
            source_runs=[run_dir],
            controlled_run=None,
            ablation_runs=[],
            output_root=tmp_path / "artifacts" / "analysis",
        )


def test_single_dataset_config_blocks_headline_not_crash(analysis_fixture, tmp_path) -> None:
    config_path = tmp_path / "single.toml"
    config_path.write_text(
        """
[analysis]
schema_version = "analysis.config.v1"
datasets = ["longmemeval"]
metrics = ["exact_match", "token_f1", "evidence_f1"]
holm_family = "primary"

[analysis.bootstrap]
n_boot = 100
seed = 0
alpha = 0.05

[analysis.review]
target_min_failures = 50
stratified = true

[analysis.comparisons.longmemeval]
primary = [
  { id = "lme_qemr_vs_fixed_vector", left = "full", right = "vector_rag", metric = "exact_match" },
]
""".lstrip(),
        encoding="utf-8",
    )
    output_root = tmp_path / "artifacts" / "analysis"
    analysis_id = generate_report(
        config_path=config_path,
        source_runs=[analysis_fixture["longmemeval"]["run_dir"]],
        controlled_run=None,
        ablation_runs=[],
        output_root=output_root,
    )
    payload = json.loads(
        (output_root / analysis_id / "report.json").read_text(encoding="utf-8")
    )
    assert payload["headline"] is None
    assert payload["headline_blocked_by_missing_datasets"] == []
    assert set(payload["datasets"]) == {"longmemeval"}
    assert len(payload["datasets"]["longmemeval"]["claims"]) == 1
    markdown = (output_root / analysis_id / "report.md").read_text(encoding="utf-8")
    assert "Blocked: a two-dataset headline" in markdown


def test_write_report_files_writes_only_inside_analysis_dir(generated, tmp_path) -> None:
    payload = json.loads((generated["artifact"] / "report.json").read_text(encoding="utf-8"))
    out_dir = tmp_path / "isolated"
    write_report_files(out_dir, payload)
    written = [path for path in out_dir.rglob("*") if path.is_file()]
    assert written
    assert all(out_dir in path.parents for path in written)
    assert render_markdown(payload)


# --------------------------------------------------------------------------- #
# C8 final CLI contract.
# --------------------------------------------------------------------------- #


def _report_cli_args(analysis_fixture, output_root) -> list[str]:
    return [
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


def test_report_cli_derives_same_analysis_id_as_validator(
    analysis_fixture, tmp_path, capsys
) -> None:
    from benchmarks.analysis.report import main as report_main
    from benchmarks.analysis.validate_report import main as validator_main

    output_root = tmp_path / "artifacts" / "analysis"
    assert report_main(_report_cli_args(analysis_fixture, output_root)) == 0
    report_payload = json.loads(capsys.readouterr().out)
    assert report_payload["valid"] is True
    artifact = Path(report_payload["artifact_dir"])
    assert artifact.is_dir()
    assert (artifact / "FINALIZED.json").is_file()
    assert (artifact / "report.md").is_file()
    assert output_root in artifact.parents

    validator_args = [
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
        str(output_root),
    ]
    assert validator_main(validator_args) == 0
    validator_payload = json.loads(capsys.readouterr().out)
    assert validator_payload["analysis_id"] == report_payload["analysis_id"]


def test_report_cli_missing_source_refuses_with_stable_code(
    analysis_fixture, tmp_path, capsys
) -> None:
    from benchmarks.analysis.report import main as report_main

    missing = tmp_path / "no-such-run"
    args = [
        "--config",
        "configs/analysis/main.toml",
        "--source-run",
        str(analysis_fixture["source_runs"][0]),
        "--source-run",
        str(missing),
        "--output-root",
        str(tmp_path / "artifacts" / "analysis"),
    ]
    assert report_main(args) != 0
    stderr = capsys.readouterr().err
    assert "error[missing_run_dir]" in stderr
    assert not (tmp_path / "artifacts" / "analysis").exists() or not list(
        (tmp_path / "artifacts" / "analysis").iterdir()
    )


def test_report_cli_refuses_legacy_report_input(
    analysis_fixture, tmp_path, capsys
) -> None:
    from benchmarks.analysis.report import main as report_main

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
        "--output-root",
        str(tmp_path / "artifacts" / "analysis"),
    ]
    assert report_main(args) != 0
    assert "error[legacy_report_input]" in capsys.readouterr().err


def test_report_cli_missing_config_returns_usage_error(capsys) -> None:
    from benchmarks.analysis.report import main as report_main

    exit_code = report_main(
        ["--config", "no-such.toml", "--source-run", "x", "--output-root", "out"]
    )
    assert exit_code != 0
    assert "error[missing_config]" in capsys.readouterr().err


def test_real_publication_runs_do_not_exist_and_cli_refuses_cleanly(tmp_path, capsys) -> None:
    """Real publication runs do not exist yet; the CLI must refuse, never fabricate."""
    from benchmarks.analysis.report import main as report_main

    args = [
        "--config",
        "configs/analysis/main.toml",
        "--source-run",
        str(tmp_path / "runs" / "publication" / "longmemeval"),
        "--source-run",
        str(tmp_path / "runs" / "publication" / "locomo"),
        "--output-root",
        str(tmp_path / "artifacts" / "analysis"),
    ]
    assert report_main(args) != 0
    stderr = capsys.readouterr().err
    assert "error[missing_run_dir]" in stderr
    artifacts = tmp_path / "artifacts" / "analysis"
    assert not artifacts.exists() or not any(artifacts.iterdir())
