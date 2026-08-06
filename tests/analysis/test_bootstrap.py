from __future__ import annotations

import pytest

from benchmarks.analysis.bootstrap import (
    compare_methods,
    holm_adjust,
    holm_family_correction,
    pair_deltas,
    paired_bootstrap_ci,
    primary_comparison_results,
    resolve_primary_comparisons,
)
from benchmarks.analysis.finalization import load_config


def _row(question_id: str, value: float) -> dict:
    return {"question_id": question_id, "exact_match": value}


def test_constant_positive_deltas_give_degenerate_ci() -> None:
    ci = paired_bootstrap_ci([1.0] * 100, n_boot=200, seed=1)
    assert ci.estimate == pytest.approx(1.0)
    assert ci.ci_low == pytest.approx(1.0)
    assert ci.ci_high == pytest.approx(1.0)
    assert ci.p_value < 0.01


def test_zero_deltas_have_p_value_one() -> None:
    ci = paired_bootstrap_ci([0.0] * 100, n_boot=200, seed=1)
    assert ci.estimate == pytest.approx(0.0)
    assert ci.p_value == pytest.approx(1.0)


def test_seeded_bootstrap_is_deterministic() -> None:
    deltas = [0.2, -0.1, 0.4, 0.0, 0.3, -0.2, 0.5] * 10
    first = paired_bootstrap_ci(deltas, n_boot=500, seed=42)
    second = paired_bootstrap_ci(deltas, n_boot=500, seed=42)
    assert first.as_dict() == second.as_dict()


def test_ci_contains_estimate() -> None:
    deltas = [0.5, -0.3, 0.8, 0.1, -0.4, 0.6, 0.2, -0.1] * 25
    ci = paired_bootstrap_ci(deltas, n_boot=1000, seed=7)
    assert ci.ci_low <= ci.estimate <= ci.ci_high


def test_positive_mean_deltas_yield_small_p_value() -> None:
    deltas = [1.0 if index % 3 else 0.0 for index in range(300)]
    ci = paired_bootstrap_ci(deltas, n_boot=1000, seed=3)
    assert ci.estimate > 0.0
    assert ci.p_value < 0.05


def test_empty_deltas_raise() -> None:
    with pytest.raises(ValueError, match="at least one"):
        paired_bootstrap_ci([])


def test_invalid_arguments_raise() -> None:
    with pytest.raises(ValueError, match="n_boot"):
        paired_bootstrap_ci([0.1, 0.2], n_boot=1)
    with pytest.raises(ValueError, match="alpha"):
        paired_bootstrap_ci([0.1, 0.2], alpha=1.0)


# --------------------------------------------------------------------------- #
# C4: paired alignment, Holm correction, structured comparisons.
# --------------------------------------------------------------------------- #


def test_pair_deltas_aligns_by_question_id() -> None:
    left = [_row("q1", 1.0), _row("q2", 0.0), _row("q3", 1.0)]
    right = [_row("q3", 1.0), _row("q1", 0.0), _row("q2", 0.0)]
    pairing = pair_deltas(
        left, right, left_method="full", right_method="etec", metric="exact_match"
    )
    assert pairing.question_ids == ("q1", "q2", "q3")
    assert pairing.deltas == (1.0, 0.0, 0.0)
    assert pairing.left_method == "full"
    assert pairing.right_method == "etec"


def test_pair_deltas_is_deterministic() -> None:
    left = [_row(f"q{i}", 1.0 if i % 2 else 0.0) for i in range(10)]
    right = [_row(f"q{i}", 0.5) for i in range(10)]
    first = pair_deltas(left, right, left_method="a", right_method="b", metric="exact_match")
    second = pair_deltas(
        list(reversed(left)),
        list(reversed(right)),
        left_method="a",
        right_method="b",
        metric="exact_match",
    )
    assert first.deltas == second.deltas
    assert first.question_ids == second.question_ids


def test_pair_deltas_rejects_duplicate_ids() -> None:
    left = [_row("q1", 1.0), _row("q1", 1.0), _row("q2", 0.0)]
    right = [_row("q1", 0.5), _row("q2", 0.5)]
    with pytest.raises(ValueError, match="duplicate_question_ids"):
        pair_deltas(left, right, left_method="a", right_method="b", metric="exact_match")


def test_pair_deltas_rejects_unmatched_ids_both_directions() -> None:
    left = [_row("q1", 1.0), _row("q2", 0.0)]
    right = [_row("q1", 0.5)]
    with pytest.raises(ValueError, match="unmatched_question_ids"):
        pair_deltas(left, right, left_method="a", right_method="b", metric="exact_match")
    left = [_row("q1", 1.0)]
    right = [_row("q1", 0.5), _row("q2", 0.0)]
    with pytest.raises(ValueError, match="unmatched_question_ids"):
        pair_deltas(left, right, left_method="a", right_method="b", metric="exact_match")


def test_pair_deltas_rejects_empty_intersection() -> None:
    with pytest.raises(ValueError, match="no shared question"):
        pair_deltas(
            [_row("q1", 1.0)],
            [_row("q2", 0.0)],
            left_method="a",
            right_method="b",
            metric="exact_match",
        )


def test_holm_single_hypothesis_is_unchanged() -> None:
    assert holm_adjust([0.03]) == [0.03]
    assert holm_adjust([1.0]) == [1.0]


def test_holm_two_hypotheses() -> None:
    adjusted = holm_adjust([0.04, 0.01])
    assert adjusted == pytest.approx([0.04, 0.02])


def test_holm_is_deterministic_with_ties() -> None:
    first = holm_adjust([0.05, 0.05, 0.01])
    second = holm_adjust([0.05, 0.05, 0.01])
    assert first == second
    swapped = holm_adjust([0.05, 0.01, 0.05])
    # ties resolve by input index; values must be the same multiset
    assert sorted(first) == sorted(swapped)


def test_holm_adjusted_values_are_monotonic_in_raw_order() -> None:
    raw = [0.20, 0.01, 0.05, 0.10, 0.02, 0.15]
    adjusted = holm_adjust(raw)
    order = sorted(range(len(raw)), key=lambda index: (raw[index], index))
    sorted_adjusted = [adjusted[index] for index in order]
    assert sorted_adjusted == sorted(sorted_adjusted)


def test_holm_values_stay_in_unit_interval() -> None:
    raw = [0.001, 0.002, 0.9, 0.4, 0.6, 0.5, 0.3]
    for value in holm_adjust(raw):
        assert 0.0 <= value <= 1.0


def test_compare_methods_returns_structured_result() -> None:
    rows = {
        "full": [_row(f"q{i}", 1.0 if i % 3 else 0.0) for i in range(30)],
        "etec": [_row(f"q{i}", 0.5 if i % 2 else 0.0) for i in range(30)],
    }
    result = compare_methods(
        comparison_id="c1",
        dataset="locomo",
        run_id="run-1",
        config_hash="sha256:c",
        left_method="full",
        right_method="etec",
        metric="exact_match",
        rows_by_method=rows,
        n_boot=500,
        seed=7,
    )
    assert result.comparison_id == "c1"
    assert result.run_ids == ("run-1",)
    assert result.config_hashes == ("sha256:c",)
    assert result.n_questions == 30
    assert result.n_boot == 500
    assert result.seed == 7
    assert result.ci_low <= result.estimate <= result.ci_high
    assert 0.0 <= result.raw_p <= 1.0
    assert result.adjusted_p is None


def test_compare_methods_rejects_missing_method() -> None:
    rows = {"full": [_row("q1", 1.0)]}
    with pytest.raises(ValueError, match="missing_method"):
        compare_methods(
            comparison_id="c1",
            dataset="locomo",
            run_id="run-1",
            config_hash="sha256:c",
            left_method="full",
            right_method="etec",
            metric="exact_match",
            rows_by_method=rows,
            n_boot=100,
            seed=0,
        )


def test_holm_family_correction_assigns_adjusted_p_and_family() -> None:
    results = [
        compare_methods(
            comparison_id=f"c{i}",
            dataset="locomo",
            run_id="run-1",
            config_hash="sha256:c",
            left_method="full",
            right_method="etec",
            metric="exact_match",
            rows_by_method={
                "full": [_row(f"q{j}", 1.0) for j in range(20)],
                "etec": [_row(f"q{j}", 0.5) for j in range(20)],
            },
            n_boot=200,
            seed=i,
        )
        for i in range(2)
    ]
    corrected = holm_family_correction(results, "primary")
    assert len(corrected) == 2
    for result, raw in zip(corrected, [item.raw_p for item in results], strict=True):
        assert result.holm_family == "primary"
        assert result.adjusted_p is not None
        assert result.adjusted_p >= raw - 1e-12
    # one-hypothesis family: adjusted == raw
    single = holm_family_correction([results[0]], "primary")
    assert single[0].adjusted_p == pytest.approx(results[0].raw_p)


def test_primary_comparisons_resolved_from_config(analysis_fixture) -> None:
    config = load_config("configs/analysis/main.toml")
    loaded = analysis_fixture["longmemeval"]
    from benchmarks.analysis.loaders import load_base_run

    run = load_base_run(loaded["run_dir"])
    resolved = resolve_primary_comparisons(config, run)
    assert any(comparison_id == "lme_qemr_vs_fixed_vector" for comparison_id, _, _ in resolved)
    assert any(left == "full" and right == "vector_rag" for _, left, right in resolved)


def test_primary_comparison_rejects_undeclared_method(analysis_fixture) -> None:
    from benchmarks.analysis.loaders import load_base_run

    run = load_base_run(analysis_fixture["longmemeval"]["run_dir"])
    rows = {
        method: [row for row in run.rows if row.method == method] for method in run.manifest.methods
    }
    del rows["full"]
    from benchmarks.analysis.bootstrap import compare_methods

    with pytest.raises(ValueError, match="missing_method"):
        compare_methods(
            comparison_id="lme_qemr_vs_fixed_vector",
            dataset="longmemeval",
            run_id=run.run_id,
            config_hash=run.manifest.config_hash,
            left_method="full",
            right_method="vector_rag",
            metric="exact_match",
            rows_by_method=rows,
            n_boot=100,
            seed=0,
        )


def test_primary_comparison_results_run_for_both_datasets(analysis_fixture) -> None:
    config = load_config("configs/analysis/main.toml")
    from benchmarks.analysis.loaders import load_base_run

    for info in (analysis_fixture["longmemeval"], analysis_fixture["locomo"]):
        run = load_base_run(info["run_dir"])
        results = primary_comparison_results(config, run)
        assert results
        for result in results:
            assert result.dataset == run.dataset
            assert result.adjusted_p is not None
            assert 0.0 <= result.adjusted_p <= 1.0
            assert result.run_ids == (run.run_id,)
            assert result.config_hashes == (run.manifest.config_hash,)


def test_primary_comparisons_are_seeded_and_deterministic(analysis_fixture) -> None:
    config = load_config("configs/analysis/main.toml")
    from benchmarks.analysis.loaders import load_base_run

    run = load_base_run(analysis_fixture["locomo"]["run_dir"])
    first = primary_comparison_results(config, run)
    second = primary_comparison_results(config, run)
    assert [result.as_dict() for result in first] == [result.as_dict() for result in second]
