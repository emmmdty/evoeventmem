"""Paired bootstrap confidence intervals (EVALUATION.md §6).

Significance is only ever computed on per-question paired deltas between two
methods of the same run, never on unpaired aggregates. The bootstrap is
seeded for reproducibility and implemented with the standard library only.
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class BootstrapCI:
    """Mean of paired deltas with a two-sided percentile interval and p-value.

    ``p_value`` is the bootstrap p-value for the two-sided test of the null
    hypothesis that the population mean delta is zero: twice the fraction of
    resampled means on the far side of zero (with add-one smoothing so that a
    p-value of exactly 0.0 is never reported).
    """

    estimate: float
    ci_low: float
    ci_high: float
    p_value: float
    n_boot: int
    seed: int

    def as_dict(self) -> dict[str, float | int]:
        return {
            "estimate": self.estimate,
            "ci_low": self.ci_low,
            "ci_high": self.ci_high,
            "p_value": self.p_value,
            "n_boot": self.n_boot,
            "seed": self.seed,
        }


def paired_bootstrap_ci(
    deltas: Sequence[float],
    *,
    n_boot: int = 10_000,
    seed: int = 0,
    alpha: float = 0.05,
) -> BootstrapCI:
    """Compute a percentile-interval CI and p-value for the mean of paired deltas.

    ``deltas`` must be the per-question differences (method A minus method B)
    already aligned in question order. The resampling unit is the question,
    which preserves the pairing of the two methods.
    """
    if not deltas:
        raise ValueError("paired_bootstrap_ci requires at least one paired delta")
    if n_boot < 2:
        raise ValueError("n_boot must be at least 2")
    if not 0.0 < alpha < 1.0:
        raise ValueError("alpha must be strictly between 0 and 1")

    estimate = sum(deltas) / len(deltas)
    rng = random.Random(seed)
    sample_means: list[float] = []
    lower_tail = 0
    upper_tail = 0
    for _ in range(n_boot):
        resampled = [deltas[rng.randrange(len(deltas))] for _ in deltas]
        mean = sum(resampled) / len(resampled)
        sample_means.append(mean)
        if mean < 0.0:
            lower_tail += 1
        elif mean > 0.0:
            upper_tail += 1

    sample_means.sort()
    lower_index = math.floor((n_boot - 1) * alpha / 2)
    upper_index = math.ceil((n_boot - 1) * (1 - alpha / 2))
    ci_low = sample_means[lower_index]
    ci_high = sample_means[upper_index]

    if estimate > 0.0:
        p_value = 2.0 * (lower_tail + 1) / (n_boot + 1)
    elif estimate < 0.0:
        p_value = 2.0 * (upper_tail + 1) / (n_boot + 1)
    else:
        p_value = 1.0
    p_value = min(1.0, p_value)

    return BootstrapCI(
        estimate=estimate,
        ci_low=ci_low,
        ci_high=ci_high,
        p_value=p_value,
        n_boot=n_boot,
        seed=seed,
    )
