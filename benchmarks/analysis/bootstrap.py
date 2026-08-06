"""Paired bootstrap confidence intervals (EVALUATION.md §6) and Holm correction.

Significance is only ever computed on per-question paired deltas between two
methods of the same run, never on unpaired aggregates. The bootstrap is
seeded for reproducibility and implemented with the standard library only.

C4 additions: ID-aligned pairing (missing/duplicate/unmatched question IDs are
rejected), deterministic Holm correction over a declared hypothesis family,
and a structured comparison result carrying raw p, adjusted p, CI, estimate,
IDs, seed, and bootstrap count.
"""

from __future__ import annotations

import math
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any, TypeVar

from benchmarks.analysis.finalization import LoadedConfig
from benchmarks.analysis.loaders import LoadedRun

Row = TypeVar("Row")


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


# --------------------------------------------------------------------------- #
# C4: paired alignment, Holm correction, structured comparison results.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Pairing:
    """ID-aligned paired deltas for one metric between two methods."""

    left_method: str
    right_method: str
    metric: str
    question_ids: tuple[str, ...]
    deltas: tuple[float, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "left_method": self.left_method,
            "right_method": self.right_method,
            "metric": self.metric,
            "n_questions": len(self.question_ids),
            "question_ids": list(self.question_ids),
        }


def _metric_value(row: Any, metric: str) -> float:
    value = row.get(metric) if isinstance(row, Mapping) else getattr(row, metric)
    if value is None:
        raise ValueError(f"metric {metric!r} is missing on a paired row")
    return float(value)


def _rows_by_id(rows: Sequence[Row], label: str) -> dict[str, Row]:
    by_id: dict[str, Row] = {}
    for row in rows:
        question_id = row["question_id"] if isinstance(row, Mapping) else row.question_id
        if question_id in by_id:
            raise ValueError(
                f"duplicate_question_ids: {label} contains duplicate question {question_id}"
            )
        by_id[str(question_id)] = row
    return by_id


def pair_deltas(
    left: Sequence[Row],
    right: Sequence[Row],
    *,
    left_method: str,
    right_method: str,
    metric: str,
) -> Pairing:
    """Align two per-question row sequences by question ID and compute deltas.

    Rejects duplicate question IDs within either side and unmatched question
    IDs across sides (missing on either side). The aligned order is the
    sorted intersection, which keeps pairing deterministic.
    """
    left_by_id = _rows_by_id(left, left_method)
    right_by_id = _rows_by_id(right, right_method)
    common = sorted(set(left_by_id) & set(right_by_id))
    if not common:
        raise ValueError(
            "unmatched_question_ids: no shared question IDs to pair "
            f"({left_method} and {right_method} are disjoint)"
        )
    missing_in_right = sorted(set(left_by_id) - set(right_by_id))
    missing_in_left = sorted(set(right_by_id) - set(left_by_id))
    if missing_in_right or missing_in_left:
        raise ValueError(
            "unmatched_question_ids: "
            f"{left_method} lacks {missing_in_left}, {right_method} lacks "
            f"{missing_in_right}"
        )
    deltas = tuple(
        _metric_value(left_by_id[question_id], metric)
        - _metric_value(right_by_id[question_id], metric)
        for question_id in common
    )
    return Pairing(
        left_method=left_method,
        right_method=right_method,
        metric=metric,
        question_ids=tuple(common),
        deltas=deltas,
    )


def holm_adjust(raw_p: Sequence[float]) -> list[float]:
    """Holm-Bonferroni adjusted p-values, returned in the input order.

    Deterministic: ties are broken by input index. Adjusted values are
    monotonically non-decreasing when viewed in ascending raw-p order and are
    bounded to [0, 1]. A one-hypothesis family leaves the raw p unchanged.
    """
    hypotheses = list(enumerate(float(value) for value in raw_p))
    ordered = sorted(hypotheses, key=lambda item: (item[1], item[0]))
    m = len(ordered)
    if m == 0:
        return []
    sorted_adjusted = [0.0] * m
    for rank, (_, p_value) in enumerate(ordered):
        sorted_adjusted[rank] = min(1.0, p_value * (m - rank))
    for rank in range(1, m):
        sorted_adjusted[rank] = max(sorted_adjusted[rank], sorted_adjusted[rank - 1])
    adjusted = [0.0] * m
    for rank, (original_index, _) in enumerate(ordered):
        adjusted[original_index] = sorted_adjusted[rank]
    return adjusted


@dataclass(frozen=True)
class ComparisonResult:
    """Structured paired-comparison result for one primary hypothesis."""

    comparison_id: str
    dataset: str
    run_ids: tuple[str, ...]
    config_hashes: tuple[str, ...]
    left_method: str
    right_method: str
    metric: str
    estimate: float
    ci_low: float
    ci_high: float
    raw_p: float
    adjusted_p: float | None = None
    n_questions: int = 0
    n_boot: int = 0
    seed: int = 0
    holm_family: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.comparison_id,
            "dataset": self.dataset,
            "run_ids": list(self.run_ids),
            "config_hashes": list(self.config_hashes),
            "left_method": self.left_method,
            "right_method": self.right_method,
            "metric": self.metric,
            "estimate": self.estimate,
            "ci_low": self.ci_low,
            "ci_high": self.ci_high,
            "raw_p": self.raw_p,
            "adjusted_p": self.adjusted_p,
            "n_questions": self.n_questions,
            "n_boot": self.n_boot,
            "seed": self.seed,
            "holm_family": self.holm_family,
        }


def compare_methods(
    *,
    comparison_id: str,
    dataset: str,
    run_id: str,
    config_hash: str,
    left_method: str,
    right_method: str,
    metric: str,
    rows_by_method: Mapping[str, Sequence[Row]],
    n_boot: int,
    seed: int,
    alpha: float = 0.05,
) -> ComparisonResult:
    """Run one paired bootstrap comparison between two methods of one run."""
    if left_method not in rows_by_method:
        raise ValueError(f"missing_method: {left_method} has no rows in dataset {dataset}")
    if right_method not in rows_by_method:
        raise ValueError(f"missing_method: {right_method} has no rows in dataset {dataset}")
    pairing = pair_deltas(
        rows_by_method[left_method],
        rows_by_method[right_method],
        left_method=left_method,
        right_method=right_method,
        metric=metric,
    )
    ci = paired_bootstrap_ci(pairing.deltas, n_boot=n_boot, seed=seed, alpha=alpha)
    return ComparisonResult(
        comparison_id=comparison_id,
        dataset=dataset,
        run_ids=(run_id,),
        config_hashes=(config_hash,),
        left_method=left_method,
        right_method=right_method,
        metric=metric,
        estimate=ci.estimate,
        ci_low=ci.ci_low,
        ci_high=ci.ci_high,
        raw_p=ci.p_value,
        n_questions=len(pairing.question_ids),
        n_boot=ci.n_boot,
        seed=ci.seed,
    )


def holm_family_correction(
    results: Sequence[ComparisonResult],
    family: str,
) -> list[ComparisonResult]:
    """Apply Holm correction across one declared hypothesis family."""
    adjusted = holm_adjust([result.raw_p for result in results])
    return [
        replace(result, adjusted_p=value, holm_family=family)
        for result, value in zip(results, adjusted, strict=True)
    ]


def resolve_primary_comparisons(
    config: LoadedConfig,
    loaded: LoadedRun,
) -> list[tuple[str, str, str]]:
    """Resolve the config-declared primary comparisons for one dataset run.

    Returns ``(comparison_id, left, right)`` triples; raises when a declared
    comparison references a method absent from the run or a metric that is
    not enabled in the config. Declarations for other datasets are ignored.
    """
    declarations = config.config.comparisons.get(loaded.dataset, [])
    available = set(loaded.manifest.methods)
    resolved: list[tuple[str, str, str]] = []
    for declaration in declarations:
        if declaration.left not in available or declaration.right not in available:
            raise ValueError(
                f"undeclared_method: comparison {declaration.id!r} references "
                f"{declaration.left!r}/{declaration.right!r} which are not present "
                f"in dataset {loaded.dataset} ({sorted(available)})"
            )
        if declaration.metric not in config.config.metrics:
            raise ValueError(
                f"undeclared_metric: comparison {declaration.id!r} uses metric "
                f"{declaration.metric!r} which is not enabled in the config"
            )
        resolved.append((declaration.id, declaration.left, declaration.right))
    return resolved


def primary_comparison_results(
    config: LoadedConfig,
    loaded: LoadedRun,
) -> list[ComparisonResult]:
    """Run every declared primary comparison for one dataset run."""
    rows_by_method: dict[str, list[Row]] = {}
    for method in loaded.manifest.methods:
        rows_by_method[method] = [row for row in loaded.rows if row.method == method]
    results: list[ComparisonResult] = []
    for comparison_id, left, right in resolve_primary_comparisons(config, loaded):
        # The metric is fixed per declaration; metric values live on the rows.
        metric = next(
            declaration.metric
            for declaration in config.config.comparisons[loaded.dataset]
            if declaration.id == comparison_id
        )
        results.append(
            compare_methods(
                comparison_id=comparison_id,
                dataset=loaded.dataset,
                run_id=loaded.run_id,
                config_hash=loaded.manifest.config_hash,
                left_method=left,
                right_method=right,
                metric=metric,
                rows_by_method=rows_by_method,
                n_boot=config.config.bootstrap.n_boot,
                seed=config.config.bootstrap.seed,
                alpha=config.config.bootstrap.alpha,
            )
        )
    return holm_family_correction(results, config.config.holm_family)
