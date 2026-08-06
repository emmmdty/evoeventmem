"""C5: analyze finalized ablations without ever executing them.

Factor isolation is enforced per family (exactly the declared factor differs;
reader/extractor/embedding/dataset/caps/policies and budgets except the
tested one are fixed). Every required switch must be active on the controlled
fixture; a publication dataset with zero row delta is labeled
``no_observed_dataset_effect``; budget experiments need 2+ settings with
publication questions ``packing_bound=true``; offline proxies are never QA
gains; the module imports no method/extraction/consolidation internals.
"""

from __future__ import annotations

import json
from pathlib import Path

from benchmarks.analysis.ablation import (
    KNOWN_FACTORS,
    analyze_ablation_run,
    analyze_factors,
    budget_binding_issues,
    check_factor_isolation,
    controlled_activation_issues,
    decision_delta_count,
)
from benchmarks.analysis.loaders import load_ablation_run
from tests.analysis.conftest import (
    build_ablation_run,
    build_controlled_run,
    retamper_arm,
    retamper_arm_rows,
    retamper_family_manifest,
)


def _controlled(analysis_fixture) -> dict:
    return load_ablation_run(analysis_fixture["controlled_run"])


def _locomo(analysis_fixture) -> dict:
    return load_ablation_run(analysis_fixture["ablations"]["locomo"]["run_dir"])


def test_module_has_no_execution_imports() -> None:
    source = Path("benchmarks/analysis/ablation.py").read_text(encoding="utf-8")
    for forbidden in (
        "evoeventmem",
        "benchmarks.common.normalization",
        "benchmarks.locomo",
        "benchmarks.longmemeval",
        "benchmarks.experiments",
        "import subprocess",
        "RetrievalHarness",
        "RuleEventExtractor",
        "ETECConsolidator",
        "MemoryService",
    ):
        assert forbidden not in source, f"ablation.py must not import/execute {forbidden}"


def test_controlled_fixture_all_factors_active(analysis_fixture) -> None:
    controlled = _controlled(analysis_fixture)
    issues = controlled_activation_issues(controlled)
    assert issues == []
    for factor in KNOWN_FACTORS:
        result = next(
            item for item in analyze_factors(controlled, controlled=True) if item.factor == factor
        )
        assert result.controlled_active is True
        assert result.decision_delta_count > 0
        assert result.status == "active"


def test_controlled_inactive_switch_is_detected(tmp_path) -> None:
    controlled = build_controlled_run(tmp_path / "inactive-controlled", zero_delta=True)
    loaded = load_ablation_run(controlled["run_dir"])
    issues = controlled_activation_issues(loaded)
    assert any("controlled_switch_inactive" in issue for issue in issues)
    analysis = analyze_ablation_run(loaded, controlled=True)
    assert not analysis.valid
    for factor in analysis.factors:
        assert factor.controlled_active is False
        assert factor.status == "inactive"


def test_dataset_factor_isolation_passes(analysis_fixture) -> None:
    loaded = _locomo(analysis_fixture)
    assert check_factor_isolation(loaded) == []
    analysis = analyze_ablation_run(loaded, controlled=False)
    assert analysis.isolation_issues == ()
    assert analysis.valid


def test_factor_isolation_rejects_row_leak(analysis_fixture) -> None:
    loaded = _locomo(analysis_fixture)
    retamper_arm_rows(
        loaded.run_dir / "weights",
        lambda rows: rows[0].update({"intent": "temporal"}),
    )
    reloaded = load_ablation_run(loaded.run_dir)
    issues = check_factor_isolation(reloaded)
    assert any("factor_leak" in issue and "weights" in issue for issue in issues)


def test_factor_isolation_rejects_manifest_budget_leak(analysis_fixture) -> None:
    loaded = _locomo(analysis_fixture)
    retamper_arm(
        loaded.run_dir / "graph",
        {
            "budget": {
                "input_tokens": 64,
                "max_items_per_source": 8,
                "max_candidates_per_source": 128,
            }
        },
    )
    reloaded = load_ablation_run(loaded.run_dir)
    issues = check_factor_isolation(reloaded)
    assert any(
        "factor_leak" in issue and "graph" in issue and "budget" in issue for issue in issues
    )


def test_factor_isolation_rejects_unknown_factor(analysis_fixture, tmp_path) -> None:
    family = build_ablation_run(
        tmp_path / "bad-factor",
        dataset="locomo",
        base_run_dir=analysis_fixture["locomo"]["run_dir"],
        controlled_run_dir=analysis_fixture["controlled_run"],
    )
    retamper_arm(family["run_dir"] / "weights", {"changed_factors": ["quantum_tuning"]})
    loaded = load_ablation_run(family["run_dir"])
    issues = check_factor_isolation(loaded)
    assert any("unknown_factor" in issue for issue in issues)


def test_dataset_zero_delta_labeled_no_observed_effect(analysis_fixture, tmp_path) -> None:
    family = build_ablation_run(
        tmp_path / "zero-delta",
        dataset="longmemeval",
        base_run_dir=analysis_fixture["longmemeval"]["run_dir"],
        controlled_run_dir=analysis_fixture["controlled_run"],
        zero_delta=True,
    )
    loaded = load_ablation_run(family["run_dir"])
    analysis = analyze_ablation_run(loaded, controlled=False)
    assert analysis.isolation_issues == ()
    assert analysis.controlled_activation_issues == ()
    for factor in analysis.factors:
        # The budget factor always changes budget_tokens in the payload, so a
        # fully-identical fixture cannot keep it at zero delta; every other
        # factor must be labeled a no-observed-dataset effect.
        if factor.factor == "budget":
            continue
        assert factor.status == "no_observed_dataset_effect"
        assert factor.decision_delta_count == 0
        assert factor.controlled_active is None


def test_dataset_factors_active_when_delta_present(analysis_fixture) -> None:
    loaded = _locomo(analysis_fixture)
    analysis = analyze_ablation_run(loaded, controlled=False)
    for factor in analysis.factors:
        assert factor.status == "active"
        assert factor.decision_delta_count > 0


def test_decision_delta_count_derived_from_artifacts(analysis_fixture) -> None:
    loaded = _locomo(analysis_fixture)
    weights_delta = decision_delta_count(loaded, "weights")
    assert weights_delta == len(loaded.arms["weights"].rows)
    assert decision_delta_count(loaded, "base") == 0


def test_budget_binding_validation_passes(analysis_fixture) -> None:
    loaded = _locomo(analysis_fixture)
    assert budget_binding_issues(loaded) == []
    result = next(
        item for item in analyze_factors(loaded, controlled=False) if item.factor == "budget"
    )
    assert result.budget_settings == (384, 512)
    assert len(result.budget_settings) == 2
    for _arm_name, bound in result.packing_bound_questions.items():
        assert bound > 0


def test_budget_requires_two_settings(analysis_fixture, tmp_path) -> None:
    family = build_ablation_run(
        tmp_path / "single-budget",
        dataset="locomo",
        base_run_dir=analysis_fixture["locomo"]["run_dir"],
        controlled_run_dir=analysis_fixture["controlled_run"],
    )
    (family["run_dir"] / "budget_512").rename(family["run_dir"] / "budget_256")
    retamper_arm_rows(
        family["run_dir"] / "budget_256",
        lambda rows: [row.update({"budget_tokens": 384}) for row in rows],
    )
    methods = [name for name in family["manifest"].methods if name != "budget_512"] + ["budget_256"]
    retamper_family_manifest(family["run_dir"], {"methods": methods})
    loaded = load_ablation_run(family["run_dir"])
    issues = budget_binding_issues(loaded)
    assert any("budget_requires_two_settings" in issue for issue in issues)


def test_budget_not_binding_is_detected(analysis_fixture, tmp_path) -> None:
    family = build_ablation_run(
        tmp_path / "non-binding",
        dataset="locomo",
        base_run_dir=analysis_fixture["locomo"]["run_dir"],
        controlled_run_dir=analysis_fixture["controlled_run"],
    )
    for arm_name in ("budget_384", "budget_512"):
        retamper_arm_rows(
            family["run_dir"] / arm_name,
            lambda rows: [row.update({"packing_bound": False}) for row in rows],
        )
    loaded = load_ablation_run(family["run_dir"])
    issues = budget_binding_issues(loaded)
    assert any("budget_not_binding" in issue for issue in issues)


def test_offline_proxies_are_never_qa_gains(analysis_fixture) -> None:
    loaded = _locomo(analysis_fixture)
    analysis = analyze_ablation_run(loaded, controlled=False)
    for factor in analysis.factors:
        assert factor.metric_kind == "retrieval_proxy"
        assert "exact_match" not in factor.as_dict()["metric_kind"]
    payload = json.dumps(analysis.as_dict())
    assert "exact_match" not in payload
    assert "token_f1" not in payload


def test_analyze_ablation_run_aggregates(analysis_fixture) -> None:
    controlled = _controlled(analysis_fixture)
    analysis = analyze_ablation_run(controlled, controlled=True)
    assert analysis.dataset == "controlled"
    assert analysis.controlled is True
    assert {factor.factor for factor in analysis.factors} == set(KNOWN_FACTORS)
    assert analysis.valid


def test_fixture_budget_arms_bind_publication_questions(analysis_fixture) -> None:
    for info in (
        analysis_fixture["ablations"]["longmemeval"],
        analysis_fixture["ablations"]["locomo"],
    ):
        loaded = load_ablation_run(info["run_dir"])
        issues = budget_binding_issues(loaded)
        assert issues == []
        for arm_name in ("budget_384", "budget_512"):
            arm = loaded.arm(arm_name)
            assert any(row["packing_bound"] for row in arm.rows)
