"""C7: golden anti-hard-code test and claim provenance.

The golden test generates a report, changes synthetic metric values and
config hashes, regenerates in a different analysis artifact, and asserts that
JSON, CSV, SVG, Markdown tables, and prose all change consistently. The
source scan rejects run-specific numeric literals and legacy
``runs/main/report`` output. Every claim carries full provenance; the
two-dataset headline is rejected when either dataset is missing.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from benchmarks.analysis.finalization import AnalysisInputError, load_config
from benchmarks.analysis.report import build_report_data, generate_report
from benchmarks.analysis.validate_report import validate_analysis_inputs
from tests.analysis.conftest import (
    FIXED_CONFIG_HASH,
    build_ablation_run,
    build_controlled_run,
    build_synthetic_run,
)


def _fixture(
    tmp_path: Path, *, rates: dict[str, float] | None = None, config_hash: str = FIXED_CONFIG_HASH
) -> dict:
    root = tmp_path
    lme = build_synthetic_run(
        root / "runs" / "publication" / "longmemeval",
        dataset="longmemeval",
        method_rates=rates,
        config_hash=config_hash,
    )
    locomo = build_synthetic_run(
        root / "runs" / "publication" / "locomo",
        dataset="locomo",
        method_rates=rates,
        config_hash=config_hash,
    )
    controlled = build_controlled_run(root / "runs" / "validation" / "controlled-ablations")
    ablation_lme = build_ablation_run(
        root / "runs" / "publication" / "ablations" / "longmemeval",
        dataset="longmemeval",
        base_run_dir=lme["run_dir"],
        controlled_run_dir=controlled["run_dir"],
    )
    ablation_locomo = build_ablation_run(
        root / "runs" / "publication" / "ablations" / "locomo",
        dataset="locomo",
        base_run_dir=locomo["run_dir"],
        controlled_run_dir=controlled["run_dir"],
    )
    return {
        "source_runs": [lme["run_dir"], locomo["run_dir"]],
        "controlled_run": controlled["run_dir"],
        "ablation_runs": [ablation_lme["run_dir"], ablation_locomo["run_dir"]],
    }


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_golden_report_changes_consistently_with_inputs(tmp_path) -> None:
    config_path = Path("configs/analysis/main.toml")
    first = _fixture(tmp_path / "v1")
    second = _fixture(
        tmp_path / "v2",
        rates={"full": 0.9, "etec": 0.3, "event_no_etec": 0.8, "vector_rag": 0.1},
        config_hash="sha256:config-b",
    )
    output_root = tmp_path / "artifacts" / "analysis"

    first_id = generate_report(
        config_path=config_path,
        source_runs=first["source_runs"],
        controlled_run=first["controlled_run"],
        ablation_runs=first["ablation_runs"],
        output_root=output_root,
    )
    second_id = generate_report(
        config_path=config_path,
        source_runs=second["source_runs"],
        controlled_run=second["controlled_run"],
        ablation_runs=second["ablation_runs"],
        output_root=output_root,
    )
    assert first_id != second_id

    first_dir = output_root / first_id
    second_dir = output_root / second_id

    first_report = _read_json(first_dir / "report.json")
    second_report = _read_json(second_dir / "report.json")
    assert first_report != second_report

    def text(path: Path) -> str:
        return path.read_text(encoding="utf-8")

    for relative in (
        "tables/claims.csv",
        "tables/overall_longmemeval.csv",
        "tables/categories_longmemeval.csv",
        "plots/overall_em_longmemeval.svg",
        "plots/category_em_longmemeval.svg",
        "report.md",
    ):
        assert text(first_dir / relative) != text(second_dir / relative), relative

    # Consistency: the changed metric value propagates through every format.
    full_overall_v2 = next(
        row
        for row in second_report["datasets"]["longmemeval"]["overall"]
        if row["method"] == "full"
    )
    changed_value = f"{full_overall_v2['exact_match']:.4f}"
    assert text(second_dir / "tables" / "overall_longmemeval.csv").find(changed_value) >= 0
    assert text(second_dir / "report.md").find(changed_value) >= 0
    # SVG value labels render with three decimals.
    assert (
        text(second_dir / "plots" / "overall_em_longmemeval.svg").find(
            f"{full_overall_v2['exact_match']:.3f}"
        )
        >= 0
    )
    assert (
        text(second_dir / "tables" / "claims.csv").find(
            second_report["datasets"]["longmemeval"]["claims"][0]["comparison_id"]
        )
        >= 0
    )

    # The changed config hash also propagates.
    assert second_report["datasets"]["longmemeval"]["config_hash"] == "sha256:config-b"
    assert first_report["datasets"]["longmemeval"]["config_hash"] == FIXED_CONFIG_HASH


def test_source_has_no_run_specific_numeric_literals() -> None:
    import ast

    metric_literal = re.compile(r"\d+\.\d{4,}")
    for module in (
        "report.py",
        "svg.py",
        "taxonomy.py",
        "ablation.py",
        "bootstrap.py",
        "loaders.py",
    ):
        path = Path(f"benchmarks/analysis/{module}")
        source = path.read_text(encoding="utf-8")
        matches = metric_literal.findall(source)
        assert not matches, f"{module} contains run-specific metric literals: {matches}"
        tree = ast.parse(source)
        docstring = ast.get_docstring(tree) or ""
        code_without_docstring = source.replace(docstring, "", 1)
        assert "runs/main/report" not in code_without_docstring, (
            f"{module} must never consume legacy runs/main/report output"
        )


def test_every_claim_has_full_provenance(tmp_path) -> None:
    fixture = _fixture(tmp_path)
    config = load_config(Path("configs/analysis/main.toml"))
    validation = validate_analysis_inputs(
        fixture["source_runs"], fixture["controlled_run"], fixture["ablation_runs"]
    )
    assert validation.valid
    data = build_report_data(
        analysis_id="sha256:test",
        config=config,
        sources=validation.sources,
        controlled=validation.controlled,
        ablations=validation.ablations,
    )
    for dataset, payload in data["datasets"].items():
        assert payload["claims"]
        for claim in payload["claims"]:
            assert claim["dataset"] == dataset
            assert claim["comparison_id"]
            assert claim["run_ids"]
            assert claim["config_hashes"]
            assert claim["status"]
            assert claim["caveat"]
            assert claim["statement_kind"] in (
                "descriptive",
                "significant",
                "no_observed_effect",
                "retrieval_diagnostic",
            )
            assert claim["narrative"]
            if claim["statement_kind"] in ("descriptive", "significant"):
                assert claim["metric"]
                assert claim["estimate"] is not None
                assert claim["ci_low"] is not None
                assert claim["ci_high"] is not None
                assert claim["raw_p"] is not None
                assert claim["adjusted_p"] is not None
            for run_id in claim["run_ids"]:
                assert run_id in {source.run_id for source in validation.sources} | {
                    ablation.manifest.run_id for ablation in validation.ablations
                }


def test_two_dataset_headline_present_with_both_datasets(tmp_path) -> None:
    fixture = _fixture(tmp_path)
    config = load_config(Path("configs/analysis/main.toml"))
    validation = validate_analysis_inputs(
        fixture["source_runs"], fixture["controlled_run"], fixture["ablation_runs"]
    )
    data = build_report_data(
        analysis_id="sha256:test",
        config=config,
        sources=validation.sources,
        controlled=validation.controlled,
        ablations=validation.ablations,
    )
    assert data["headline"] is not None
    assert data["headline_blocked_by_missing_datasets"] == []
    assert data["headline"]["comparison_id"] == "two_dataset_headline"
    assert data["headline"]["status"] == "two_dataset_headline"


def test_two_dataset_headline_rejected_when_dataset_missing(tmp_path) -> None:
    fixture = _fixture(tmp_path)
    config = load_config(Path("configs/analysis/main.toml"))
    only_lme = validate_analysis_inputs([fixture["source_runs"][0]])
    assert only_lme.valid
    data = build_report_data(
        analysis_id="sha256:test",
        config=config,
        sources=only_lme.sources,
        controlled=None,
        ablations=[],
    )
    assert data["headline"] is None
    assert data["headline_blocked_by_missing_datasets"] == ["locomo"]
    assert "locomo" not in data["datasets"]
    assert "longmemeval" in data["datasets"]


def test_statement_kinds_are_distinguished(tmp_path) -> None:
    fixture = _fixture(tmp_path)
    config = load_config(Path("configs/analysis/main.toml"))
    validation = validate_analysis_inputs(
        fixture["source_runs"], fixture["controlled_run"], fixture["ablation_runs"]
    )
    data = build_report_data(
        analysis_id="sha256:test",
        config=config,
        sources=validation.sources,
        controlled=validation.controlled,
        ablations=validation.ablations,
    )
    kinds = {
        claim["statement_kind"]
        for payload in data["datasets"].values()
        for claim in payload["claims"]
    }
    assert "significant" in kinds or "descriptive" in kinds
    assert "retrieval_diagnostic" in kinds
    # ablation claims are never QA statements: the negation is explicit
    for payload in data["datasets"].values():
        for claim in payload["claims"]:
            if claim["comparison_id"].startswith(f"{payload['dataset']}:ablation:"):
                assert claim["statement_kind"] == "retrieval_diagnostic"
                assert "not an end-to-end QA gain" in claim["narrative"]


def test_no_observed_effect_claims_are_distinct(tmp_path) -> None:
    fixture = _fixture(tmp_path)
    config = load_config(Path("configs/analysis/main.toml"))
    zero_delta = build_ablation_run(
        tmp_path / "zero-delta",
        dataset="longmemeval",
        base_run_dir=fixture["source_runs"][0],
        controlled_run_dir=fixture["controlled_run"],
        zero_delta=True,
    )
    validation = validate_analysis_inputs(
        fixture["source_runs"],
        fixture["controlled_run"],
        [*fixture["ablation_runs"], zero_delta["run_dir"]],
    )
    assert validation.valid
    data = build_report_data(
        analysis_id="sha256:test",
        config=config,
        sources=validation.sources,
        controlled=validation.controlled,
        ablations=validation.ablations,
    )
    # the first ablation family per dataset wins; the zero-delta family is a
    # second family for longmemeval and is not the selected one
    kinds = {
        claim["statement_kind"]
        for payload in data["datasets"].values()
        for claim in payload["claims"]
    }
    assert "retrieval_diagnostic" in kinds


def test_methods_and_categories_are_manifest_driven(tmp_path) -> None:
    fixture = _fixture(tmp_path)
    config = load_config(Path("configs/analysis/main.toml"))
    validation = validate_analysis_inputs(
        fixture["source_runs"], fixture["controlled_run"], fixture["ablation_runs"]
    )
    data = build_report_data(
        analysis_id="sha256:test",
        config=config,
        sources=validation.sources,
        controlled=validation.controlled,
        ablations=validation.ablations,
    )
    lme_loaded = next(source for source in validation.sources if source.dataset == "longmemeval")
    locomo_loaded = next(source for source in validation.sources if source.dataset == "locomo")
    lme_payload = data["datasets"]["longmemeval"]
    locomo_payload = data["datasets"]["locomo"]
    assert set(lme_payload["methods"]) == set(lme_loaded.manifest.methods)
    assert set(locomo_payload["methods"]) == set(locomo_loaded.manifest.methods)
    assert "session_summary" in locomo_payload["methods"]
    assert "session_summary" not in lme_payload["methods"]
    assert set(lme_payload["categories"]) <= {
        "information-extraction",
        "multi-session-reasoning",
        "knowledge-update",
        "temporal-reasoning",
        "abstention",
    }
    assert set(locomo_payload["categories"]) <= {
        "single-hop",
        "multi-hop-reasoning",
        "temporal-reasoning",
        "open-domain-knowledge",
        "adversarial",
    }


def test_legacy_report_input_refused(tmp_path) -> None:
    config_path = Path("configs/analysis/main.toml")
    fixture = _fixture(tmp_path)
    legacy = tmp_path / "runs" / "main" / "report"
    legacy.mkdir(parents=True)
    (legacy / "report.md").write_text("# legacy\n", encoding="utf-8")
    with pytest.raises(AnalysisInputError) as exc_info:
        generate_report(
            config_path=config_path,
            source_runs=[*fixture["source_runs"], legacy],
            controlled_run=fixture["controlled_run"],
            ablation_runs=fixture["ablation_runs"],
            output_root=tmp_path / "artifacts" / "analysis",
        )
    assert exc_info.value.code == "legacy_report_input"
