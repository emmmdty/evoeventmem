from __future__ import annotations

import json

import pytest

from benchmarks.analysis.report import build_report, write_report_artifacts
from benchmarks.analysis.validate_report import validate_runs
from tests.analysis.conftest import build_question_artifacts, build_run


@pytest.fixture
def report(synthetic_run) -> dict:
    return build_report(
        runs_root=synthetic_run.parent,
        dataset=synthetic_run / "data/synthetic/locomo.json",
        ablation_path=None,
    )


def test_report_links_claims_to_run_and_config(report) -> None:
    assert report["claims"]
    for claim in report["claims"]:
        assert claim["run_ids"] == [report["run_id"]]
        assert claim["config_hashes"] == [report["config_hash"]]


def test_report_refuses_invalid_runs(tmp_path) -> None:
    run_dir = tmp_path / "runs" / "broken"
    build_run(run_dir)
    build_question_artifacts(run_dir)
    summary_path = run_dir / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["config_hash"] = "sha256:tampered"
    summary_path.write_text(json.dumps(summary), encoding="utf-8")
    with pytest.raises(ValueError, match="refused"):
        build_report(
            runs_root=run_dir.parent,
            dataset=run_dir / "data/synthetic/locomo.json",
            ablation_path=None,
        )


def test_report_taxonomy_covers_all_failures(report) -> None:
    full = report["taxonomy"]["methods"]["full"]
    assert full["failures"] >= 50
    categorized = sum(full["categories"].values())
    assert categorized == full["failures"]


def test_report_artifacts_written(report, synthetic_run) -> None:
    report_path = write_report_artifacts(report, synthetic_run.parent)
    out_dir = report_path.parent
    assert report_path.is_file()
    assert (out_dir / "claims.json").is_file()
    assert (out_dir / "tables" / "overall.csv").is_file()
    assert (out_dir / "tables" / "categories.csv").is_file()
    assert (out_dir / "tables" / "claims.csv").is_file()
    assert (out_dir / "error_review.jsonl").is_file()
    assert (out_dir / "plots" / "overall_em.svg").is_file()
    assert (out_dir / "plots" / "category_em.svg").is_file()


def test_report_review_sheet_has_expected_rows(report, synthetic_run) -> None:
    out_dir = write_report_artifacts(report, synthetic_run.parent).parent
    rows = [
        json.loads(line)
        for line in (out_dir / "error_review.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    full = report["taxonomy"]["methods"]["full"]
    full_rows = [row for row in rows if row["method"] == "full"]
    assert len(full_rows) == full["failures"] >= 50


def test_report_claims_are_paired_bootstrap(report) -> None:
    for claim in report["claims"]:
        if claim["estimate"] is None:
            continue
        assert claim["n_boot"] > 0
        assert claim["ci_low"] <= claim["estimate"] <= claim["ci_high"]
        assert 0.0 <= claim["p_value"] <= 1.0


def test_validate_report_agrees_with_report(synthetic_run) -> None:
    validation = validate_runs(synthetic_run.parent)
    assert validation.valid
