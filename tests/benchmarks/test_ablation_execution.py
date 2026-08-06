from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmarks.common.artifacts import (
    AblationRunManifest,
    ArtifactClass,
    load_finalized,
)
from benchmarks.experiments.ablation import (
    REQUIRED_FACTORS,
    load_config,
    main,
    run_ablation,
)
from evoeventmem.retrieval import POLICY_NAME

CONTROLLED_CONFIG = Path("configs/ablations/controlled.toml")
LONGMEMEVAL_CONFIG = Path("configs/ablations/longmemeval.toml")
LOCOMO_CONFIG = Path("configs/ablations/locomo.toml")


def test_controlled_run_finalizes_family_and_all_arms(tmp_path: Path) -> None:
    config = load_config(CONTROLLED_CONFIG)
    run_dir = tmp_path / "run"
    summary = run_ablation(config, run_dir)

    assert summary.dataset == "controlled"
    assert summary.expected_retrieval_policy == POLICY_NAME
    assert set(summary.arms) == {arm.name for arm in [config.base, *config.arms]}
    family = load_finalized(run_dir)
    assert family.artifact_class is ArtifactClass.SMOKE
    assert summary.controlled_run_hash == family.finalization_hash()
    for arm in [config.base, *config.arms]:
        arm_dir = run_dir / arm.name
        assert (arm_dir / "manifest.json").exists()
        assert (arm_dir / "retrieval.jsonl").exists()
        manifest = AblationRunManifest.model_validate_json(
            (arm_dir / "manifest.json").read_text(encoding="utf-8")
        )
        assert manifest.changed_factors == [arm.factor]
        load_finalized(arm_dir)


def test_required_factors_have_decision_deltas(tmp_path: Path) -> None:
    config = load_config(CONTROLLED_CONFIG)
    run_dir = tmp_path / "run"
    run_ablation(config, run_dir)

    deltas = json.loads((run_dir / "deltas.json").read_text(encoding="utf-8"))
    assert deltas["required_factors"] == list(REQUIRED_FACTORS)
    for factor in REQUIRED_FACTORS:
        arm = next(arm for arm in config.arms if arm.factor == factor)
        arm_deltas = deltas["arms"][arm.name]
        assert arm_deltas["delta_question_count"] >= 1, factor
        changed_fields = {
            field
            for question in arm_deltas["questions"]
            if question["delta"]
            for field in question["fields_changed"]
        }
        assert changed_fields, factor
        if factor == "evidence_policy":
            # The evidence switch must change a real selection/packing decision,
            # not only the recorded policy field.
            assert "selected" in changed_fields or "exclusions" in changed_fields


def test_factor_isolation_exactly_one_difference(tmp_path: Path) -> None:
    config = load_config(CONTROLLED_CONFIG)
    run_dir = tmp_path / "run"
    run_ablation(config, run_dir)

    from benchmarks.experiments.ablation import FACTOR_FIELDS

    base_controls = config.base.controls.model_dump()
    for arm in config.arms:
        arm_controls = arm.controls.model_dump()
        differing = {
            field for field in arm_controls if arm_controls[field] != base_controls[field]
        }
        assert differing, (arm.name, "no difference at all")
        assert differing <= set(FACTOR_FIELDS[arm.factor]), (arm.name, differing)


def test_stable_base_hashes_across_all_arms(tmp_path: Path) -> None:
    config = load_config(CONTROLLED_CONFIG)
    run_dir = tmp_path / "run"
    run_ablation(config, run_dir)

    manifests = {
        arm.name: AblationRunManifest.model_validate_json(
            (run_dir / arm.name / "manifest.json").read_text(encoding="utf-8")
        )
        for arm in [config.base, *config.arms]
    }
    base_hash = manifests["base"].manifest_hash()
    base_run_hash = manifests["base"].base_run_hash
    controlled_hash = manifests["base"].controlled_run_hash
    for arm in config.arms:
        manifest = manifests[arm.name]
        assert manifest.base_run_hash == base_run_hash
        assert manifest.controlled_run_hash == controlled_hash
        assert manifest.manifest_hash() != base_hash  # arm differs from base
    assert base_hash == manifests["base"].manifest_hash()


def test_evidence_nonempty_in_both_evidence_modes(tmp_path: Path) -> None:
    config = load_config(CONTROLLED_CONFIG)
    run_dir = tmp_path / "run"
    run_ablation(config, run_dir)

    for arm_name in ("base", "evidence"):
        rows = _read_jsonl(run_dir / arm_name / "retrieval.jsonl")
        assert rows
        for row in rows:
            for item in row["packed_items"]:
                assert item["evidence_refs"]
                assert any(
                    ref["raw_turn_id"] for ref in item["evidence_refs"]
                )


def test_budget_settings_bind_packing_on_controlled_data(tmp_path: Path) -> None:
    config = load_config(CONTROLLED_CONFIG)
    run_dir = tmp_path / "run"
    run_ablation(config, run_dir)

    budget_arms = [arm for arm in config.arms if arm.factor == "budget"]
    assert len(budget_arms) >= 2
    binding_settings = 0
    for arm in [config.base, *budget_arms]:
        rows = _read_jsonl(run_dir / arm.name / "retrieval.jsonl")
        assert rows
        if any(row["packing_bound"] for row in rows):
            binding_settings += 1
    assert binding_settings >= 2


def test_question_level_packing_bound_is_recorded_not_inferred(tmp_path: Path) -> None:
    config = load_config(CONTROLLED_CONFIG)
    run_dir = tmp_path / "run"
    run_ablation(config, run_dir)

    budget_arm = next(arm for arm in config.arms if arm.factor == "budget")
    rows = _read_jsonl(run_dir / budget_arm.name / "retrieval.jsonl")
    assert any(row["packing_bound"] for row in rows)
    for row in rows:
        assert isinstance(row["packing_bound"], bool)
    summary = json.loads(
        (run_dir / budget_arm.name / "summary.json").read_text(encoding="utf-8")
    )
    assert summary["packing_bound_questions"] == sum(
        1 for row in rows if row["packing_bound"]
    )


def test_controlled_resume_is_idempotent(tmp_path: Path) -> None:
    config = load_config(CONTROLLED_CONFIG)
    run_dir = tmp_path / "run"
    first = run_ablation(config, run_dir)
    manifest_bytes = (run_dir / "base" / "manifest.json").read_bytes()

    second = run_ablation(config, run_dir)

    assert (run_dir / "base" / "manifest.json").read_bytes() == manifest_bytes
    assert second.arms == first.arms
    assert second.controlled_run_hash == first.controlled_run_hash


def test_dataset_executor_requires_controlled_run(tmp_path: Path) -> None:
    config = load_config(LOCOMO_CONFIG)
    with pytest.raises(ValueError, match="--controlled-run"):
        run_ablation(config, tmp_path / "run", controlled_run_dir=None)


def test_dataset_executor_refuses_missing_controlled_run(tmp_path: Path) -> None:
    config = load_config(LOCOMO_CONFIG)
    missing = tmp_path / "missing-controlled"
    with pytest.raises(FileNotFoundError):
        run_ablation(config, tmp_path / "run", controlled_run_dir=missing)


def test_dataset_executor_refuses_inactive_controlled_run(tmp_path: Path) -> None:
    config = load_config(LOCOMO_CONFIG)
    inactive = tmp_path / "inactive"
    inactive.mkdir()
    (inactive / "manifest.json").write_text("{}")
    with pytest.raises(FileNotFoundError, match="not finalized"):
        run_ablation(config, tmp_path / "run", controlled_run_dir=inactive)


def test_dataset_executor_refuses_hash_drifted_controlled_run(tmp_path: Path) -> None:
    config = load_config(CONTROLLED_CONFIG)
    controlled_dir = tmp_path / "controlled"
    run_ablation(config, controlled_dir)

    drifted_config = load_config(LOCOMO_CONFIG)
    (controlled_dir / "manifest.json").write_text('{"tampered": true}')
    with pytest.raises(ValueError, match="hash drift|manifest"):
        run_ablation(
            drifted_config,
            tmp_path / "run",
            controlled_run_dir=controlled_dir,
        )


def test_dataset_validate_config_is_static(capsys) -> None:  # noqa: ANN001
    for config_path in (LONGMEMEVAL_CONFIG, LOCOMO_CONFIG):
        exit_code = main(["--config", str(config_path), "--validate-config"])
        assert exit_code == 0
        report = json.loads(capsys.readouterr().out)
        assert report["expected_retrieval_policy"] == POLICY_NAME
        assert report["required_factors"] == list(REQUIRED_FACTORS)
        assert report["controlled_run_required"] is True
        assert report["base_run_dir"]
        assert report["config_hash"].startswith("sha256:")


def test_controlled_validate_config_is_static(capsys) -> None:  # noqa: ANN001
    exit_code = main(["--config", str(CONTROLLED_CONFIG), "--validate-config"])
    assert exit_code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["expected_retrieval_policy"] == POLICY_NAME
    assert report["controlled_run_required"] is False


def test_config_rejects_wrong_policy_version() -> None:
    config = load_config(CONTROLLED_CONFIG)
    drifted = config.model_copy(update={"expected_retrieval_policy": "not-the-frozen-policy"})
    with pytest.raises(ValueError, match="froze"):
        run_ablation(drifted, Path("/tmp/opencode/never-run"))


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
