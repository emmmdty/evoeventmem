"""C3: content-addressed, immutable analysis outputs.

``analysis_id`` = sha256(sorted base/controlled/ablation FINALIZED hashes +
analysis config hash). Same inputs -> same ID; changed source/config ->
different ID; missing source finalization, hash drift, or legacy report input
fails. Source runs are snapshotted (hash + mtime) before generation and
verified unchanged afterwards; rerunning an identical analysis validates or
fails, never mutates; the analysis FINALIZED.json hashes every output.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmarks.analysis.finalization import (
    AnalysisInputError,
    analysis_id_for,
    analysis_output_dir,
    derive_analysis_id,
    ensure_analysis_artifact,
    finalize_analysis_artifact,
    load_analysis_finalization,
    load_config,
    output_hashes,
    snapshot_source_runs,
)
from tests.analysis.conftest import (
    build_synthetic_run,
)


@pytest.fixture
def analysis_config(tmp_path: Path):
    config_path = tmp_path / "configs" / "analysis" / "main.toml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        "\n".join(
            [
                "[analysis]",
                'schema_version = "analysis.config.v1"',
                'datasets = ["longmemeval", "locomo"]',
                'metrics = ["exact_match", "token_f1", "evidence_f1"]',
                'holm_family = "primary"',
                "",
                "[analysis.bootstrap]",
                "n_boot = 1000",
                "seed = 0",
                "alpha = 0.05",
                "",
                "[analysis.review]",
                "target_min_failures = 50",
                "stratified = true",
                "",
                "[[analysis.comparisons.longmemeval.primary]]",
                'id = "lme_qemr_vs_fixed_vector"',
                'left = "full"',
                'right = "vector_rag"',
                'metric = "exact_match"',
                "",
                "[[analysis.comparisons.longmemeval.primary]]",
                'id = "lme_etec_vs_raw_events"',
                'left = "full"',
                'right = "event_no_etec"',
                'metric = "exact_match"',
                "",
                "[[analysis.comparisons.locomo.primary]]",
                'id = "loc_qemr_vs_fixed_vector"',
                'left = "full"',
                'right = "vector_rag"',
                'metric = "exact_match"',
                "",
                "[[analysis.comparisons.locomo.primary]]",
                'id = "loc_session_summary_vs_full"',
                'left = "session_summary"',
                'right = "full"',
                'metric = "exact_match"',
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return config_path


def test_main_config_parses() -> None:
    loaded = load_config(Path("configs/analysis/main.toml"))
    assert loaded.config.schema_version == "analysis.config.v1"
    assert loaded.config.datasets == ["longmemeval", "locomo"]
    assert loaded.config.bootstrap.n_boot == 10000
    assert loaded.config.bootstrap.seed == 0
    assert loaded.config.bootstrap.alpha == 0.05
    assert loaded.config.holm_family == "primary"
    assert loaded.config.review.target_min_failures == 50
    assert loaded.config.review.stratified is True
    assert len(loaded.config.comparisons["longmemeval"]) == 2
    assert len(loaded.config.comparisons["locomo"]) == 3
    assert loaded.hash.startswith("sha256:")


def test_config_declares_no_result_values() -> None:
    text = Path("configs/analysis/main.toml").read_text(encoding="utf-8")
    for forbidden in ("estimate", "ci_low", "ci_high", "p_value", "left_value", "exact_match ="):
        assert forbidden not in text, f"config must not contain result values ({forbidden})"


def test_config_rejects_unknown_keys(tmp_path) -> None:
    config_path = tmp_path / "bad.toml"
    config_path.write_text(
        "[analysis]\n"
        'schema_version = "analysis.config.v1"\n'
        'datasets = ["longmemeval"]\n'
        'metrics = ["exact_match"]\n'
        'holm_family = "primary"\n'
        "exact_match = 0.75\n"
        "[analysis.bootstrap]\n"
        "n_boot = 1000\nseed = 0\nalpha = 0.05\n"
        "[analysis.review]\n"
        "target_min_failures = 50\n"
        "[analysis.comparisons.longmemeval]\n"
        "primary = []\n",
        encoding="utf-8",
    )
    with pytest.raises(Exception) as exc_info:
        load_config(config_path)
    assert exc_info.type.__name__ in (
        "AnalysisInputError",
        "ValidationError",
        "pydantic_core._pydantic_core.ValidationError",
    )


def test_same_inputs_same_analysis_id(analysis_config, analysis_fixture) -> None:
    config = load_config(analysis_config)
    first = derive_analysis_id(
        config,
        analysis_fixture["source_runs"],
        analysis_fixture["controlled_run"],
        analysis_fixture["ablation_runs"],
    )
    second = derive_analysis_id(
        config,
        analysis_fixture["source_runs"],
        analysis_fixture["controlled_run"],
        analysis_fixture["ablation_runs"],
    )
    assert first == second
    assert first.startswith("sha256:")


def test_analysis_id_is_sha256_of_sorted_hashes_plus_config(
    analysis_config, analysis_fixture
) -> None:
    from benchmarks.analysis.finalization import collect_finalization_hashes
    from benchmarks.common.artifacts import canonical_json_hash

    config = load_config(analysis_config)
    hashes = collect_finalization_hashes(
        analysis_fixture["source_runs"],
        analysis_fixture["controlled_run"],
        analysis_fixture["ablation_runs"],
    )
    assert len(hashes) > 10  # two sources + controlled family + arms + two ablation families + arms
    payload = {"config_hash": config.hash, "finalization_hashes": sorted(hashes.values())}
    assert analysis_id_for(config.hash, hashes) == canonical_json_hash(payload)
    assert analysis_id_for(config.hash, hashes).startswith("sha256:")


def test_input_order_does_not_matter(analysis_config, analysis_fixture) -> None:
    config = load_config(analysis_config)
    forward = derive_analysis_id(
        config,
        analysis_fixture["source_runs"],
        analysis_fixture["controlled_run"],
        analysis_fixture["ablation_runs"],
    )
    reversed_runs = list(reversed(analysis_fixture["source_runs"]))
    reversed_ablations = list(reversed(analysis_fixture["ablation_runs"]))
    backward = derive_analysis_id(
        config,
        reversed_runs,
        analysis_fixture["controlled_run"],
        reversed_ablations,
    )
    assert forward == backward


def test_changed_config_changes_analysis_id(analysis_config, analysis_fixture) -> None:
    config = load_config(analysis_config)
    first = derive_analysis_id(
        config,
        analysis_fixture["source_runs"],
        analysis_fixture["controlled_run"],
        analysis_fixture["ablation_runs"],
    )
    analysis_config.write_text(
        analysis_config.read_text(encoding="utf-8").replace("n_boot = 1000", "n_boot = 2000"),
        encoding="utf-8",
    )
    changed = load_config(analysis_config)
    second = derive_analysis_id(
        changed,
        analysis_fixture["source_runs"],
        analysis_fixture["controlled_run"],
        analysis_fixture["ablation_runs"],
    )
    assert first != second


def test_changed_source_changes_analysis_id(tmp_path, analysis_config) -> None:
    config = load_config(analysis_config)
    first_run = build_synthetic_run(tmp_path / "a", dataset="longmemeval")
    second_run = build_synthetic_run(
        tmp_path / "b",
        dataset="longmemeval",
        config_hash="sha256:config-b",
    )
    first = derive_analysis_id(config, [first_run["run_dir"]], None, [])
    second = derive_analysis_id(config, [second_run["run_dir"]], None, [])
    assert first != second


def test_missing_source_finalization_fails(analysis_fixture, tmp_path, analysis_config) -> None:
    import shutil

    config = load_config(analysis_config)
    broken = build_synthetic_run(tmp_path / "broken", dataset="locomo")
    shutil.rmtree(broken["run_dir"] / "finalized")
    with pytest.raises(AnalysisInputError) as exc_info:
        derive_analysis_id(
            config,
            [*analysis_fixture["source_runs"], broken["run_dir"]],
            analysis_fixture["controlled_run"],
            analysis_fixture["ablation_runs"],
        )
    assert exc_info.value.code == "missing_finalization"


def test_hash_drift_fails(analysis_fixture, tmp_path, analysis_config) -> None:
    config = load_config(analysis_config)
    broken = build_synthetic_run(tmp_path / "broken", dataset="locomo")
    snapshot = broken["run_dir"] / "extraction_snapshot.json"
    snapshot.write_text(json.dumps([{"tampered": True}]), encoding="utf-8")
    with pytest.raises(AnalysisInputError) as exc_info:
        derive_analysis_id(
            config,
            [*analysis_fixture["source_runs"], broken["run_dir"]],
            analysis_fixture["controlled_run"],
            analysis_fixture["ablation_runs"],
        )
    assert exc_info.value.code == "finalization_hash_drift"


def test_legacy_report_input_fails(analysis_fixture, tmp_path, analysis_config) -> None:
    config = load_config(analysis_config)
    legacy = tmp_path / "runs" / "main" / "report"
    legacy.mkdir(parents=True)
    (legacy / "report.md").write_text("# legacy\n", encoding="utf-8")
    with pytest.raises(AnalysisInputError) as exc_info:
        derive_analysis_id(
            config,
            [*analysis_fixture["source_runs"], legacy],
            analysis_fixture["controlled_run"],
            analysis_fixture["ablation_runs"],
        )
    assert exc_info.value.code == "legacy_report_input"


def test_source_snapshot_verifies_and_detects_change(analysis_fixture) -> None:
    snapshot = snapshot_source_runs(analysis_fixture["source_runs"])
    assert snapshot.verify()
    tampered = next(state for state in snapshot.files if state.path.endswith("samples.jsonl"))
    path = Path(tampered.path)
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    assert not snapshot.verify()


def test_source_files_unchanged_after_report_generation(
    analysis_fixture, tmp_path, analysis_config
) -> None:
    config = load_config(analysis_config)
    analysis_id = derive_analysis_id(
        config,
        analysis_fixture["source_runs"],
        analysis_fixture["controlled_run"],
        analysis_fixture["ablation_runs"],
    )
    snapshot = snapshot_source_runs(
        [
            *analysis_fixture["source_runs"],
            analysis_fixture["controlled_run"],
            *analysis_fixture["ablation_runs"],
        ]
    )

    def writer(analysis_dir: Path) -> None:
        (analysis_dir / "report.md").write_text(f"# report for {analysis_id}\n", encoding="utf-8")
        (analysis_dir / "tables").mkdir()
        (analysis_dir / "tables" / "overall.csv").write_text(
            "method,em\nfull,0.75\n", encoding="utf-8"
        )

    output_root = tmp_path / "artifacts" / "analysis"
    ensure_analysis_artifact(
        output_root,
        analysis_id=analysis_id,
        config=config,
        input_hashes={},
        writer=writer,
    )
    assert snapshot.verify(), "source runs must be unchanged after report generation"


def test_rerun_validates_without_mutation(analysis_fixture, tmp_path, analysis_config) -> None:
    config = load_config(analysis_config)
    analysis_id = derive_analysis_id(
        config,
        analysis_fixture["source_runs"],
        analysis_fixture["controlled_run"],
        analysis_fixture["ablation_runs"],
    )

    def writer(analysis_dir: Path) -> None:
        (analysis_dir / "report.md").write_text(f"# report for {analysis_id}\n", encoding="utf-8")

    output_root = tmp_path / "artifacts" / "analysis"
    first = ensure_analysis_artifact(
        output_root, analysis_id=analysis_id, config=config, input_hashes={}, writer=writer
    )
    before = {
        str(path): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in sorted(first.rglob("*"))
        if path.is_file()
    }
    second = ensure_analysis_artifact(
        output_root, analysis_id=analysis_id, config=config, input_hashes={}, writer=writer
    )
    after = {
        str(path): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in sorted(second.rglob("*"))
        if path.is_file()
    }
    assert first == second
    assert before == after
    assert (second / "report.md").read_text(encoding="utf-8") == f"# report for {analysis_id}\n"


def test_analysis_finalization_hashes_every_output(
    analysis_fixture, tmp_path, analysis_config
) -> None:
    config = load_config(analysis_config)
    analysis_id = derive_analysis_id(
        config,
        analysis_fixture["source_runs"],
        analysis_fixture["controlled_run"],
        analysis_fixture["ablation_runs"],
    )

    def writer(analysis_dir: Path) -> None:
        (analysis_dir / "report.md").write_text("# report\n", encoding="utf-8")
        (analysis_dir / "tables").mkdir()
        (analysis_dir / "tables" / "a.csv").write_text("x\n1\n", encoding="utf-8")
        (analysis_dir / "plots").mkdir()
        (analysis_dir / "plots" / "p.svg").write_text("<svg/>\n", encoding="utf-8")

    output_root = tmp_path / "artifacts" / "analysis"
    analysis_dir = ensure_analysis_artifact(
        output_root, analysis_id=analysis_id, config=config, input_hashes={}, writer=writer
    )
    seal = load_analysis_finalization(analysis_dir)
    assert seal.analysis_id == analysis_id
    expected = {
        "report.md",
        "tables/a.csv",
        "plots/p.svg",
    }
    assert set(seal.output_hashes) == expected
    assert output_hashes(analysis_dir) == seal.output_hashes


def test_analysis_drift_detected(analysis_fixture, tmp_path, analysis_config) -> None:
    config = load_config(analysis_config)
    analysis_id = derive_analysis_id(
        config,
        analysis_fixture["source_runs"],
        analysis_fixture["controlled_run"],
        analysis_fixture["ablation_runs"],
    )

    def writer(analysis_dir: Path) -> None:
        (analysis_dir / "report.md").write_text("# report\n", encoding="utf-8")

    output_root = tmp_path / "artifacts" / "analysis"
    analysis_dir = ensure_analysis_artifact(
        output_root, analysis_id=analysis_id, config=config, input_hashes={}, writer=writer
    )
    (analysis_dir / "report.md").write_text("# tampered\n", encoding="utf-8")
    with pytest.raises(AnalysisInputError) as exc_info:
        load_analysis_finalization(analysis_dir)
    assert exc_info.value.code == "analysis_output_drift"


def test_finalization_is_write_once(analysis_fixture, tmp_path, analysis_config) -> None:
    config = load_config(analysis_config)
    analysis_id = derive_analysis_id(
        config,
        analysis_fixture["source_runs"],
        analysis_fixture["controlled_run"],
        analysis_fixture["ablation_runs"],
    )

    def writer(analysis_dir: Path) -> None:
        (analysis_dir / "report.md").write_text("# report\n", encoding="utf-8")

    output_root = tmp_path / "artifacts" / "analysis"
    analysis_dir = ensure_analysis_artifact(
        output_root, analysis_id=analysis_id, config=config, input_hashes={}, writer=writer
    )
    with pytest.raises(FileExistsError):
        finalize_analysis_artifact(
            analysis_dir,
            analysis_id=analysis_id,
            config_hash=config.hash,
            input_hashes={},
        )


def test_writes_only_below_output_root(analysis_fixture, tmp_path, analysis_config) -> None:
    config = load_config(analysis_config)
    analysis_id = derive_analysis_id(
        config,
        analysis_fixture["source_runs"],
        analysis_fixture["controlled_run"],
        analysis_fixture["ablation_runs"],
    )

    def writer(analysis_dir: Path) -> None:
        (analysis_dir / "report.md").write_text("# report\n", encoding="utf-8")

    output_root = tmp_path / "artifacts" / "analysis"
    ensure_analysis_artifact(
        output_root, analysis_id=analysis_id, config=config, input_hashes={}, writer=writer
    )
    assert analysis_output_dir(output_root, analysis_id).is_dir()
    assert (output_root / analysis_id / "report.md").is_file()
    written = [path for path in output_root.rglob("*") if path.is_file()]
    assert written, "expected outputs below the output root"
    assert all(output_root in path.parents for path in written)


def test_analysis_id_mismatch_rejected(analysis_fixture, tmp_path, analysis_config) -> None:
    config = load_config(analysis_config)
    analysis_id = derive_analysis_id(
        config,
        analysis_fixture["source_runs"],
        analysis_fixture["controlled_run"],
        analysis_fixture["ablation_runs"],
    )
    output_root = tmp_path / "artifacts" / "analysis"
    other = build_synthetic_run(tmp_path / "other", dataset="locomo")
    other_id = derive_analysis_id(config, [other["run_dir"]], None, [])
    ensure_analysis_artifact(
        output_root,
        analysis_id=other_id,
        config=config,
        input_hashes={},
        writer=lambda analysis_dir: (analysis_dir / "report.md").write_text(
            "# other\n", encoding="utf-8"
        ),
    )
    (output_root / other_id).rename(output_root / analysis_id)
    with pytest.raises(AnalysisInputError) as exc_info:
        ensure_analysis_artifact(
            output_root,
            analysis_id=analysis_id,
            config=config,
            input_hashes={},
            writer=lambda analysis_dir: (analysis_dir / "report.md").write_text(
                "# wrong\n", encoding="utf-8"
            ),
        )
    assert exc_info.value.code == "analysis_id_mismatch"


def test_derive_analysis_id_uses_finalized_hashes_only(
    analysis_fixture, tmp_path, analysis_config
) -> None:
    """A changed derived file is caught by finalization drift before any ID."""
    config = load_config(analysis_config)
    broken = build_synthetic_run(tmp_path / "broken", dataset="locomo")
    derived = broken["run_dir"] / "full" / "predictions.jsonl"
    derived.write_text(derived.read_text(encoding="utf-8") + "garbage\n", encoding="utf-8")
    with pytest.raises(AnalysisInputError):
        derive_analysis_id(config, [broken["run_dir"]], None, [])
