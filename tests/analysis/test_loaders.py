"""C2: dataset-neutral loading and validation of finalized B-schema runs.

The synthetic fixtures are B-schema-valid finalized trees (schema
``analysis.synthetic.v1``): LongMemEval has six methods, LoCoMo has seven
including ``session_summary``; dataset-specific categories are preserved while
common columns are normalized into ``AnalysisRow``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmarks.analysis.loaders import (
    LOCOMO_CATEGORY_BY_ID,
    LoadError,
    load_ablation_run,
    load_base_run,
)
from benchmarks.analysis.validate_report import validate_analysis_inputs
from benchmarks.common.artifacts import ArtifactClass
from tests.analysis.conftest import (
    FIXED_CONFIG_HASH,
    LME_ABILITY_NAMES,
    LME_METHODS,
    LOCOMO_METHODS,
    build_ablation_run,
    build_synthetic_run,
    retamper_arm,
)


def _codes(result) -> list[str]:
    return [issue.code for issue in result.issues]


def test_load_longmemeval_base_run(analysis_fixture) -> None:
    loaded = load_base_run(analysis_fixture["longmemeval"]["run_dir"])
    assert loaded.dataset == "longmemeval"
    assert set(loaded.manifest.methods) == set(LME_METHODS)
    assert "session_summary" not in loaded.manifest.methods
    assert loaded.manifest.artifact_class is ArtifactClass.PUBLICATION
    for method in LME_METHODS:
        rows = [row for row in loaded.rows if row.method == method]
        assert len(rows) == len(loaded.manifest.expected_question_ids)
        assert all(row.dataset == "longmemeval" for row in rows)


def test_load_locomo_base_run(analysis_fixture) -> None:
    loaded = load_base_run(analysis_fixture["locomo"]["run_dir"])
    assert loaded.dataset == "locomo"
    assert set(loaded.manifest.methods) == set(LOCOMO_METHODS)
    assert "session_summary" in loaded.manifest.methods
    for method in LOCOMO_METHODS:
        rows = [row for row in loaded.rows if row.method == method]
        assert len(rows) == len(loaded.manifest.expected_question_ids)
    session_rows = [row for row in loaded.rows if row.method == "session_summary"]
    assert all(row.packed_item_count == 0 for row in session_rows)


def test_dataset_specific_categories_preserved(analysis_fixture) -> None:
    lme = load_base_run(analysis_fixture["longmemeval"]["run_dir"])
    locomo = load_base_run(analysis_fixture["locomo"]["run_dir"])
    lme_categories = {row.category for row in lme.rows}
    locomo_categories = {row.category for row in locomo.rows}
    assert lme_categories <= set(LME_ABILITY_NAMES)
    assert locomo_categories <= set(LOCOMO_CATEGORY_BY_ID.values())
    assert lme_categories != locomo_categories


def test_common_columns_normalized(analysis_fixture) -> None:
    lme = load_base_run(analysis_fixture["longmemeval"]["run_dir"])
    locomo = load_base_run(analysis_fixture["locomo"]["run_dir"])
    for loaded in (lme, locomo):
        row = next(row for row in loaded.rows if row.method == "full")
        assert row.run_id == loaded.manifest.run_id
        assert row.config_hash == loaded.manifest.config_hash
        assert row.manifest_hash == loaded.manifest.manifest_hash()
        assert row.packed_item_count == 3
        assert row.total_input_tokens > 0
        assert row.gold_answer in ("blue house", "red car", "green truck", None)


def test_loading_is_deterministic(analysis_fixture) -> None:
    run_dir = analysis_fixture["locomo"]["run_dir"]
    first = load_base_run(run_dir)
    second = load_base_run(run_dir)
    assert first.rows == second.rows
    assert first.manifest.manifest_hash() == second.manifest.manifest_hash()
    assert first.finalization.finalization_hash() == second.finalization.finalization_hash()


def test_session_summary_never_injected_into_longmemeval(tmp_path) -> None:
    run = build_synthetic_run(
        tmp_path / "bad-lme",
        dataset="longmemeval",
        methods=(*LME_METHODS, "session_summary"),
    )
    with pytest.raises(LoadError, match="session_summary"):
        load_base_run(run["run_dir"])
    result = validate_analysis_inputs([run["run_dir"]])
    assert "injected_session_summary" in _codes(result)
    assert not result.valid


def test_zero_source_runs_rejected() -> None:
    result = validate_analysis_inputs([])
    assert not result.valid
    assert "zero_source_runs" in _codes(result)


def test_unknown_schema_rejected(tmp_path) -> None:
    run = build_synthetic_run(tmp_path / "run", dataset="locomo")
    manifest_path = run["run_dir"] / "manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["schema_version"] = 99
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(LoadError, match="schema_version"):
        load_base_run(run["run_dir"])


def test_missing_finalization_rejected(tmp_path) -> None:
    run = build_synthetic_run(tmp_path / "run", dataset="locomo")
    import shutil

    shutil.rmtree(run["run_dir"] / "finalized")
    with pytest.raises(LoadError, match="not finalized"):
        load_base_run(run["run_dir"])
    result = validate_analysis_inputs([run["run_dir"]])
    assert "missing_finalization" in _codes(result)


def test_hash_drift_rejected(tmp_path) -> None:
    run = build_synthetic_run(tmp_path / "run", dataset="locomo")
    snapshot = run["run_dir"] / "extraction_snapshot.json"
    snapshot.write_text(json.dumps([{"tampered": True}]), encoding="utf-8")
    with pytest.raises(LoadError, match="hash drift"):
        load_base_run(run["run_dir"])
    result = validate_analysis_inputs([run["run_dir"]])
    assert "finalization_hash_drift" in _codes(result)


def test_dirty_publication_run_rejected(tmp_path) -> None:
    run = build_synthetic_run(tmp_path / "run", dataset="locomo", git_dirty=True)
    with pytest.raises(LoadError, match="dirty"):
        load_base_run(run["run_dir"])
    result = validate_analysis_inputs([run["run_dir"]])
    assert "dirty_publication_run" in _codes(result)


def test_diagnostic_class_rejected(tmp_path) -> None:
    run = build_synthetic_run(
        tmp_path / "run",
        dataset="locomo",
        artifact_class=ArtifactClass.DIAGNOSTIC,
    )
    with pytest.raises(LoadError, match="publication-class"):
        load_base_run(run["run_dir"])
    result = validate_analysis_inputs([run["run_dir"]])
    assert "non_publication_class" in _codes(result)


def test_subset_scope_rejected(tmp_path) -> None:
    run = build_synthetic_run(tmp_path / "run", dataset="locomo", scope="sample_limit=1")
    result = validate_analysis_inputs([run["run_dir"]])
    assert "subset_scope" in _codes(result)


def test_missing_derived_artifact_rejected(tmp_path) -> None:
    run = build_synthetic_run(tmp_path / "run", dataset="locomo")
    (run["run_dir"] / "full" / "predictions.jsonl").unlink()
    result = validate_analysis_inputs([run["run_dir"]])
    assert "missing_derived_artifact" in _codes(result)


def test_missing_model_cache_rejected(tmp_path) -> None:
    run = build_synthetic_run(tmp_path / "run", dataset="locomo")
    import shutil

    shutil.rmtree(run["run_dir"] / "model_cache")
    result = validate_analysis_inputs([run["run_dir"]])
    assert "missing_model_cache" in _codes(result)


def test_missing_question_ids_rejected(tmp_path) -> None:
    run = build_synthetic_run(tmp_path / "run", dataset="locomo")
    path = run["run_dir"] / "full" / "samples.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    path.write_text(
        "\n".join(json.dumps(row, sort_keys=True) for row in rows[:-1]) + "\n",
        encoding="utf-8",
    )
    result = validate_analysis_inputs([run["run_dir"]])
    assert "missing_question_ids" in _codes(result)


def test_duplicate_question_ids_rejected(tmp_path) -> None:
    run = build_synthetic_run(tmp_path / "run", dataset="locomo")
    path = run["run_dir"] / "full" / "predictions.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    rows.append(rows[0])
    path.write_text(
        "\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n",
        encoding="utf-8",
    )
    result = validate_analysis_inputs([run["run_dir"]])
    assert "duplicate_question_ids" in _codes(result)


def test_dataset_hash_drift_rejected(tmp_path) -> None:
    run = build_synthetic_run(tmp_path / "run", dataset="locomo")
    dataset = run["run_dir"] / "data" / "synthetic" / "dataset.json"
    dataset.write_text(dataset.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(LoadError, match="dataset hash mismatch"):
        load_base_run(run["run_dir"])
    result = validate_analysis_inputs([run["run_dir"]])
    assert "dataset_drift" in _codes(result)


def test_legacy_report_input_rejected(tmp_path) -> None:
    report_dir = tmp_path / "runs" / "main" / "report"
    report_dir.mkdir(parents=True)
    (report_dir / "report.md").write_text("# legacy\n", encoding="utf-8")
    result = validate_analysis_inputs([report_dir])
    assert "legacy_report_input" in _codes(result)


def test_incompatible_settings_within_dataset_rejected(tmp_path) -> None:
    first = build_synthetic_run(tmp_path / "a", dataset="longmemeval")
    second = build_synthetic_run(
        tmp_path / "b",
        dataset="longmemeval",
        embedding_model_id="bge-m3",
        config_hash="sha256:config-b",
    )
    result = validate_analysis_inputs([first["run_dir"], second["run_dir"]])
    assert not result.valid
    incompatible = [issue for issue in result.issues if issue.code == "incompatible_within_dataset"]
    assert any("embedding" in issue.message for issue in incompatible)


def test_incompatible_policy_within_dataset_rejected(tmp_path) -> None:
    from benchmarks.common.artifacts import PolicyVersions

    first = build_synthetic_run(tmp_path / "a", dataset="locomo")
    second = build_synthetic_run(
        tmp_path / "b",
        dataset="locomo",
        config_hash="sha256:config-b",
        policies=PolicyVersions(
            extraction="shared-snapshot.v1",
            router="query-router.rules.v1",
            retrieval="qemr-weight-profiles.v2",
            consolidation="etec.v1",
        ),
    )
    result = validate_analysis_inputs([first["run_dir"], second["run_dir"]])
    assert not result.valid
    incompatible = [issue for issue in result.issues if issue.code == "incompatible_within_dataset"]
    assert any("retrieval" in issue.message for issue in incompatible)


def test_different_stacks_across_datasets_allowed(analysis_fixture) -> None:
    result = validate_analysis_inputs(analysis_fixture["source_runs"])
    assert result.valid
    assert result.error_codes() == []


def test_incompatible_method_sets_within_dataset_rejected(tmp_path) -> None:
    first = build_synthetic_run(tmp_path / "a", dataset="locomo")
    second = build_synthetic_run(
        tmp_path / "b",
        dataset="locomo",
        methods=tuple(m for m in LOCOMO_METHODS if m != "session_summary"),
        config_hash="sha256:config-b",
    )
    result = validate_analysis_inputs([first["run_dir"], second["run_dir"]])
    assert "incompatible_methods" in _codes(result)


def test_validation_never_writes_below_source_run(analysis_fixture) -> None:
    def snapshot(root: Path) -> dict[str, tuple[int, str]]:
        return {
            str(path): (path.stat().st_size, path.stat().st_mtime_ns)
            for path in sorted(root.rglob("*"))
            if path.is_file()
        }

    before = snapshot(analysis_fixture["runs_root"])
    validate_analysis_inputs(
        analysis_fixture["source_runs"],
        controlled_run=analysis_fixture["controlled_run"],
        ablation_runs=analysis_fixture["ablation_runs"],
    )
    after = snapshot(analysis_fixture["runs_root"])
    assert before == after


def test_full_input_set_validates(analysis_fixture) -> None:
    result = validate_analysis_inputs(
        analysis_fixture["source_runs"],
        controlled_run=analysis_fixture["controlled_run"],
        ablation_runs=analysis_fixture["ablation_runs"],
    )
    assert result.valid
    assert len(result.sources) == 2
    assert result.controlled is not None
    assert len(result.ablations) == 2
    assert result.error_codes() == []


def test_structured_issues_have_stable_codes(analysis_fixture) -> None:
    run_dir = analysis_fixture["locomo"]["run_dir"]
    (run_dir / "full" / "predictions.jsonl").unlink()
    result = validate_analysis_inputs([run_dir])
    assert not result.valid
    issues = result.as_dict()["issues"]
    assert all(issue["severity"] == "error" for issue in issues)
    assert all(issue["code"] for issue in issues)


def test_ablation_run_loads_arms(analysis_fixture) -> None:
    loaded = load_ablation_run(analysis_fixture["ablations"]["longmemeval"]["run_dir"])
    assert set(loaded.arms) == {
        "base",
        "evidence",
        "temporal",
        "graph",
        "router",
        "weights",
        "budget_384",
        "budget_512",
    }
    for arm in loaded.arms.values():
        assert arm.factor == arm.manifest.changed_factors[0]
        assert len(arm.rows) == len(loaded.manifest.expected_question_ids)
        assert all(row["question_id"] for row in arm.rows)
    assert loaded.deltas
    assert loaded.deltas["arms"]["weights"]["delta_question_count"] > 0
    assert loaded.arm("base").factor == "base"
    assert loaded.arm("budget_384").factor == "budget"
    assert loaded.arm("budget_512").factor == "budget"


def test_ablation_controlled_hash_mismatch_rejected(analysis_fixture, tmp_path) -> None:
    ablation_dir = analysis_fixture["ablations"]["locomo"]["run_dir"]
    retamper_arm(ablation_dir / "weights", {"controlled_run_hash": "sha256:tampered"})
    result = validate_analysis_inputs(
        analysis_fixture["source_runs"],
        controlled_run=analysis_fixture["controlled_run"],
        ablation_runs=[ablation_dir],
    )
    assert not result.valid
    assert "ablation_controlled_hash_mismatch" in _codes(result)


def test_ablation_base_run_mismatch_rejected(analysis_fixture) -> None:
    ablation_dir = analysis_fixture["ablations"]["locomo"]["run_dir"]
    retamper_arm(ablation_dir / "budget_384", {"base_run_hash": "sha256:tampered"})
    result = validate_analysis_inputs(
        analysis_fixture["source_runs"],
        controlled_run=analysis_fixture["controlled_run"],
        ablation_runs=[ablation_dir],
    )
    assert not result.valid
    assert "ablation_base_run_mismatch" in _codes(result)


def test_ablation_without_controlled_run_rejected(analysis_fixture) -> None:
    result = validate_analysis_inputs(
        analysis_fixture["source_runs"],
        ablation_runs=[analysis_fixture["ablations"]["locomo"]["run_dir"]],
    )
    assert not result.valid
    assert "missing_controlled_run" in _codes(result)


def test_incompatible_ablation_family_rejected(analysis_fixture) -> None:
    ablation_dir = analysis_fixture["ablations"]["locomo"]["run_dir"]
    retamper_arm(
        ablation_dir / "graph",
        {
            "embedding": {
                "kind": "http",
                "provider": "deterministic_fake",
                "model_id": "bge-m3",
                "version": "v1",
                "endpoint": "http://fake",
            }
        },
    )
    result = validate_analysis_inputs(
        analysis_fixture["source_runs"],
        controlled_run=analysis_fixture["controlled_run"],
        ablation_runs=[ablation_dir],
    )
    assert not result.valid
    assert "incompatible_ablation_family" in _codes(result)


def test_controlled_run_loads(analysis_fixture) -> None:
    loaded = load_ablation_run(analysis_fixture["controlled_run"])
    assert loaded.dataset == "controlled"
    assert loaded.manifest.artifact_class is ArtifactClass.SMOKE
    assert "base" in loaded.arms
    assert loaded.deltas["arms"]["budget_384"]["delta_question_count"] > 0


def test_zero_delta_dataset_ablation_loads_as_family(analysis_fixture, tmp_path) -> None:
    family = build_ablation_run(
        tmp_path / "zero-delta",
        dataset="longmemeval",
        base_run_dir=analysis_fixture["longmemeval"]["run_dir"],
        controlled_run_dir=analysis_fixture["controlled_run"],
        zero_delta=True,
    )
    loaded = load_ablation_run(family["run_dir"])
    assert loaded.dataset == "longmemeval"
    assert all(
        loaded.deltas["arms"][arm]["delta_question_count"] == 0
        for arm in (
            "evidence",
            "temporal",
            "graph",
            "router",
            "weights",
            "budget_384",
            "budget_512",
        )
    )


def test_budget_arms_record_packing_bound(analysis_fixture) -> None:
    loaded = load_ablation_run(analysis_fixture["ablations"]["longmemeval"]["run_dir"])
    for arm_name in ("budget_384", "budget_512"):
        arm = loaded.arm(arm_name)
        assert any(row["packing_bound"] for row in arm.rows)
        assert any(not row["packing_bound"] for row in arm.rows)


def test_fixture_config_hash_is_stable(analysis_fixture) -> None:
    assert analysis_fixture["longmemeval"]["manifest"].config_hash == FIXED_CONFIG_HASH
    assert analysis_fixture["locomo"]["manifest"].config_hash == FIXED_CONFIG_HASH
