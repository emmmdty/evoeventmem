from __future__ import annotations

import pytest

from benchmarks.analysis.bootstrap import paired_bootstrap_ci


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
