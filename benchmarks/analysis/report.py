"""Dynamic two-dataset analysis report (C7).

Every claim, table, plot, and narrative value derives from validated,
finalized artifacts through structured result objects; no run-specific metric
is hard-coded in this module and nothing is ever read from legacy
``runs/main/report`` output.

Claim provenance: every claim carries its dataset, comparison ID, run IDs,
config hashes, metric, estimate/CI/p-values where applicable, and an explicit
status/caveat. Statements are distinguished as ``descriptive``,
``significant``, ``no_observed_effect``, or ``retrieval_diagnostic``. The
two-dataset headline is only produced when both declared datasets are
finalized and validated; otherwise it is rejected and reported as blocked.

Methods and categories are manifest-driven: the report never hard-codes the
method set or the category list; narrative templates express conditions only.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from benchmarks.analysis.ablation import FactorResult, analyze_ablation_run
from benchmarks.analysis.bootstrap import (
    ComparisonResult,
    primary_comparison_results,
)
from benchmarks.analysis.finalization import (
    AnalysisInputError,
    LoadedConfig,
    collect_finalization_hashes,
    derive_analysis_id,
    ensure_analysis_artifact,
    load_config,
)
from benchmarks.analysis.loaders import LoadedAblationRun, LoadedRun
from benchmarks.analysis.svg import bar_chart, heatmap, write_csv, write_figure
from benchmarks.analysis.taxonomy import (
    FailureType,
    build_review_sheet_rows,
    review_coverage,
    stratified_failure_sample,
    write_review_sheet,
)
from benchmarks.analysis.validate_report import validate_analysis_inputs

DEFAULT_TARGET_MIN_FAILURES = 50


@dataclass(frozen=True)
class Claim:
    """One structured, fully-provenanced claim rendered from results."""

    dataset: str
    comparison_id: str
    run_ids: tuple[str, ...]
    config_hashes: tuple[str, ...]
    metric: str | None
    statement_kind: str
    status: str
    caveat: str | None
    left_method: str | None = None
    right_method: str | None = None
    estimate: float | None = None
    ci_low: float | None = None
    ci_high: float | None = None
    raw_p: float | None = None
    adjusted_p: float | None = None
    n_questions: int | None = None
    narrative: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "dataset": self.dataset,
            "comparison_id": self.comparison_id,
            "run_ids": list(self.run_ids),
            "config_hashes": list(self.config_hashes),
            "metric": self.metric,
            "statement_kind": self.statement_kind,
            "status": self.status,
            "caveat": self.caveat,
            "left_method": self.left_method,
            "right_method": self.right_method,
            "estimate": self.estimate,
            "ci_low": self.ci_low,
            "ci_high": self.ci_high,
            "raw_p": self.raw_p,
            "adjusted_p": self.adjusted_p,
            "n_questions": self.n_questions,
            "narrative": self.narrative,
        }


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def method_overview(loaded: LoadedRun) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for method in loaded.manifest.methods:
        method_rows = [row for row in loaded.rows if row.method == method]
        rows.append(
            {
                "method": method,
                "questions": len(method_rows),
                "exact_match": _mean([row.exact_match for row in method_rows]),
                "token_f1": _mean([row.token_f1 for row in method_rows]),
                "evidence_f1": _mean([row.evidence_f1 for row in method_rows]),
                "tokens_per_query": (
                    _mean([float(row.total_input_tokens) for row in method_rows])
                    if method_rows
                    else None
                ),
            }
        )
    return rows


def category_overview(loaded: LoadedRun) -> dict[str, dict[str, dict[str, float]]]:
    categories = sorted({row.category for row in loaded.rows})
    by_method: dict[str, dict[str, dict[str, float]]] = defaultdict(dict)
    for method in loaded.manifest.methods:
        method_rows = [row for row in loaded.rows if row.method == method]
        for category in categories:
            values = [row.exact_match for row in method_rows if row.category == category]
            by_method[method][category] = {
                "questions": len(values),
                "exact_match": _mean(values),
            }
    return dict(by_method)


def taxonomy_payload(loaded: LoadedRun, *, target_min: int) -> dict[str, Any]:
    failures = build_review_sheet_rows(loaded.rows)
    counts = Counter(row["failure_type"] for row in failures)
    sample, sample_summary = stratified_failure_sample(failures, target_min=target_min)
    coverage = review_coverage(sample, failures)
    return {
        "failure_counts": {
            failure_type.value: counts.get(failure_type.value, 0) for failure_type in FailureType
        },
        "failure_total": len(failures),
        "sample": sample,
        "sampling": sample_summary,
        "coverage": coverage,
    }


def comparison_claim(result: ComparisonResult, *, alpha: float) -> Claim:
    """One primary-comparison claim; statement kind decided by Holm p-value."""
    significant = result.adjusted_p is not None and result.adjusted_p < alpha
    if significant:
        statement_kind = "significant"
        status = "significant"
        narrative = (
            f"On {result.dataset}, {result.left_method} vs {result.right_method} "
            f"on {result.metric} showed an estimated difference of "
            f"{result.estimate:+.4f} (95% CI [{result.ci_low:+.4f}, "
            f"{result.ci_high:+.4f}], raw p={result.raw_p:.4f}, "
            f"Holm-adjusted p={result.adjusted_p:.4f}, n={result.n_questions})."
        )
    else:
        statement_kind = "descriptive"
        status = "not_significant"
        narrative = (
            f"On {result.dataset}, {result.left_method} vs {result.right_method} "
            f"on {result.metric} showed an estimated difference of "
            f"{result.estimate:+.4f} (95% CI [{result.ci_low:+.4f}, "
            f"{result.ci_high:+.4f}], raw p={result.raw_p:.4f}, "
            f"Holm-adjusted p={result.adjusted_p:.4f}, n={result.n_questions}); "
            "this is a descriptive association, not a significant effect."
        )
    return Claim(
        dataset=result.dataset,
        comparison_id=result.comparison_id,
        run_ids=result.run_ids,
        config_hashes=result.config_hashes,
        metric=result.metric,
        statement_kind=statement_kind,
        status=status,
        caveat=(
            "Paired per-question bootstrap; Holm family 'primary'; raw and "
            "adjusted p-values are both reported."
        ),
        left_method=result.left_method,
        right_method=result.right_method,
        estimate=result.estimate,
        ci_low=result.ci_low,
        ci_high=result.ci_high,
        raw_p=result.raw_p,
        adjusted_p=result.adjusted_p,
        n_questions=result.n_questions,
        narrative=narrative,
    )


def ablation_factor_claim(
    factor: FactorResult,
    *,
    dataset: str,
    run_id: str,
    config_hash: str,
) -> Claim:
    """One retrieval-diagnostic ablation claim (never an end-to-end QA gain)."""
    arms = ", ".join(factor.arm_names)
    if factor.status == "no_observed_dataset_effect":
        narrative = (
            f"On {dataset}, ablation factor {factor.factor} (arms {arms}) changed "
            f"retrieval decisions on 0 of the publication questions; this is a "
            "no-observed-dataset-effect result and is not evidence that the "
            "switch is inert (the controlled fixture is the activity gate)."
        )
        status = "no_observed_dataset_effect"
    else:
        narrative = (
            f"On {dataset}, ablation factor {factor.factor} (arms {arms}) changed "
            f"retrieval decisions on {factor.decision_delta_count} question(s); "
            "retrieval-trace diagnostic only, not an end-to-end QA gain."
        )
        status = "active"
    if factor.factor == "budget":
        settings = ", ".join(str(setting) for setting in factor.budget_settings)
        bound = ", ".join(
            f"{name}={count}" for name, count in factor.packing_bound_questions.items()
        )
        narrative += (
            f" Budget arms run at settings ({settings}); packing binds at ({bound}) questions."
        )
    return Claim(
        dataset=dataset,
        comparison_id=f"{dataset}:ablation:{factor.factor}",
        run_ids=(run_id,),
        config_hashes=(config_hash,),
        metric=None,
        statement_kind="retrieval_diagnostic",
        status=status,
        caveat=(
            "Offline retrieval traces from finalized ablation arms; never "
            "presented as an end-to-end QA gain."
        ),
        n_questions=factor.decision_delta_count,
        narrative=narrative,
    )


def headline_claim(
    present_datasets: Sequence[str],
    declared_datasets: Sequence[str],
) -> tuple[Claim | None, list[str]]:
    """Two-dataset headline, produced only when both datasets are present."""
    missing = sorted(set(declared_datasets) - set(present_datasets))
    if missing:
        return None, missing
    sorted_datasets = sorted(present_datasets)
    return (
        Claim(
            dataset="both",
            comparison_id="two_dataset_headline",
            run_ids=(),
            config_hashes=(),
            metric=None,
            statement_kind="descriptive",
            status="two_dataset_headline",
            caveat=(
                "Each dataset's results derive from its own validated, "
                "finalized source run; no cross-dataset paired test is run "
                "because the datasets may use different model stacks."
            ),
            narrative=(
                f"The two-dataset report covers {sorted_datasets[0]} and "
                f"{sorted_datasets[1]}, each analyzed from validated finalized "
                "runs with dataset-specific methods and categories; all "
                "comparisons are paired within a dataset."
            ),
        ),
        [],
    )


def _dataset_report(
    loaded: LoadedRun,
    ablation: LoadedAblationRun | None,
    config: LoadedConfig,
) -> dict[str, Any]:
    comparisons = primary_comparison_results(config, loaded)
    claims = [
        comparison_claim(result, alpha=config.config.bootstrap.alpha) for result in comparisons
    ]
    ablation_payload: dict[str, Any] | None = None
    if ablation is not None:
        ablation_analysis = analyze_ablation_run(ablation, controlled=False)
        ablation_payload = ablation_analysis.as_dict()
        for factor in ablation_analysis.factors:
            claims.append(
                ablation_factor_claim(
                    factor,
                    dataset=loaded.dataset,
                    run_id=ablation.manifest.run_id,
                    config_hash=ablation.manifest.config_hash,
                )
            )
    return {
        "dataset": loaded.dataset,
        "run_id": loaded.run_id,
        "config_hash": loaded.manifest.config_hash,
        "git_commit": loaded.manifest.git.commit,
        "methods": list(loaded.manifest.methods),
        "categories": sorted({row.category for row in loaded.rows}),
        "overall": method_overview(loaded),
        "categories_table": category_overview(loaded),
        "comparisons": [result.as_dict() for result in comparisons],
        "ablation": ablation_payload,
        "taxonomy": taxonomy_payload(loaded, target_min=config.config.review.target_min_failures),
        "claims": [claim.as_dict() for claim in claims],
    }


def build_report_data(
    *,
    analysis_id: str,
    config: LoadedConfig,
    sources: Sequence[LoadedRun],
    controlled: LoadedAblationRun | None,
    ablations: Sequence[LoadedAblationRun],
) -> dict[str, Any]:
    """Assemble the full structured report; raises on invalid inputs."""
    if not sources:
        raise AnalysisInputError("zero_source_runs", "no validated source runs to report on")
    by_dataset: dict[str, LoadedRun] = {}
    for source in sources:
        by_dataset.setdefault(source.dataset, source)
    ablation_by_dataset: dict[str, LoadedAblationRun] = {}
    if controlled is not None:
        for ablation in ablations:
            ablation_by_dataset.setdefault(ablation.dataset, ablation)
    dataset_reports: dict[str, dict[str, Any]] = {}
    for dataset in by_dataset:
        dataset_reports[dataset] = _dataset_report(
            by_dataset[dataset], ablation_by_dataset.get(dataset), config
        )
    headline, blocked = headline_claim(list(dataset_reports), config.config.datasets)
    return {
        "analysis_id": analysis_id,
        "config_hash": config.hash,
        "datasets": dataset_reports,
        "headline": headline.as_dict() if headline is not None else None,
        "headline_blocked_by_missing_datasets": blocked,
        "generated_from": "validated finalized artifacts only",
    }


# --------------------------------------------------------------------------- #
# Rendering.
# --------------------------------------------------------------------------- #


def render_markdown(data: Mapping[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# EvoEventMem M15 analysis report (content-addressed)")
    lines.append("")
    lines.append(f"- analysis_id: `{data['analysis_id']}`")
    lines.append(f"- analysis config hash: `{data['config_hash']}`")
    lines.append("")
    headline = data.get("headline")
    if headline is not None:
        lines.append("## Two-dataset headline")
        lines.append("")
        lines.append(headline["narrative"])
        lines.append("")
        lines.append(f"- caveat: {headline['caveat']}")
        lines.append("")
    else:
        lines.append("## Two-dataset headline")
        lines.append("")
        lines.append(
            "Blocked: missing finalized dataset(s) "
            + ", ".join(data["headline_blocked_by_missing_datasets"])
            + "."
        )
        lines.append("")
    for dataset, payload in sorted(data["datasets"].items()):
        lines.append(f"## Dataset: {dataset}")
        lines.append("")
        lines.append(
            f"- run_id: `{payload['run_id']}`; config_hash: "
            f"`{payload['config_hash']}`; git_commit: `{payload['git_commit']}`"
        )
        lines.append(f"- methods: {', '.join(payload['methods'])}")
        lines.append(f"- categories: {', '.join(payload['categories'])}")
        lines.append("")
        lines.append("### Overall metrics")
        lines.append("")
        lines.append("| method | questions | exact_match | token_f1 | evidence_f1 | tokens/query |")
        lines.append("|---|---|---|---|---|---|")
        for row in payload["overall"]:
            tokens = "" if row["tokens_per_query"] is None else f"{row['tokens_per_query']:.1f}"
            lines.append(
                f"| {row['method']} | {row['questions']} | {row['exact_match']:.4f} "
                f"| {row['token_f1']:.4f} | {row['evidence_f1']:.4f} | {tokens} |"
            )
        lines.append("")
        lines.append("### Claims (paired bootstrap, Holm-adjusted)")
        lines.append("")
        lines.append("| id | kind | claim |")
        lines.append("|---|---|---|")
        for claim in payload["claims"]:
            lines.append(
                f"| {claim['comparison_id']} | {claim['statement_kind']} | {claim['narrative']} |"
            )
        lines.append("")
        lines.append("### Ablation factors (retrieval diagnostics)")
        lines.append("")
        ablation = payload["ablation"]
        if ablation is None:
            lines.append("No finalized ablation family for this dataset.")
        else:
            lines.append("| factor | arms | delta questions | status |")
            lines.append("|---|---|---|---|")
            for factor in ablation["factors"]:
                lines.append(
                    f"| {factor['factor']} | {', '.join(factor['arm_names'])} "
                    f"| {factor['decision_delta_count']} | {factor['status']} |"
                )
            if ablation["isolation_issues"] or ablation["budget_issues"]:
                lines.append("")
                lines.append("Validation issues:")
                for issue in [*ablation["isolation_issues"], *ablation["budget_issues"]]:
                    lines.append(f"- {issue}")
        lines.append("")
        lines.append("### Failure taxonomy and review coverage")
        lines.append("")
        taxonomy = payload["taxonomy"]
        lines.append("| failure type | count |")
        lines.append("|---|---|")
        for failure_type, count in taxonomy["failure_counts"].items():
            lines.append(f"| {failure_type} | {count} |")
        lines.append("")
        coverage = taxonomy["coverage"]
        lines.append(
            f"Review handoff: {coverage['sample_size']} of {coverage['failure_total']} "
            f"failures sampled (fraction {coverage['sampled_fraction']:.3f}); "
            f"{coverage['reviewed_count']} reviewed "
            f"(fraction {coverage['reviewed_fraction']:.3f}); automatic labels are "
            "hypotheses until reviewed."
        )
        lines.append("")
    lines.append("## Provenance")
    lines.append("")
    lines.append(
        "Every claim links to validated run IDs and config hashes; all numbers "
        "recompute from per-question rows; source runs are hash-verified and "
        "read-only; the report writes only to its content-addressed artifact "
        "directory."
    )
    lines.append("")
    return "\n".join(lines)


def _cell_exact_match(payload: Mapping[str, Any], method: str, category: str) -> str:
    value = (
        payload["categories_table"][method].get(category, {}).get("exact_match", 0.0)
    )
    return f"{value:.4f}"


def write_report_files(analysis_dir: Path, data: Mapping[str, Any]) -> None:
    """Write every report output below one content-addressed analysis dir."""
    out_dir = Path(analysis_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    tables = out_dir / "tables"
    plots = out_dir / "plots"
    tables.mkdir(exist_ok=True)
    plots.mkdir(exist_ok=True)

    (out_dir / "report.json").write_text(
        json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    claims_rows: list[list[object]] = [
        [
            "dataset",
            "comparison_id",
            "run_ids",
            "config_hashes",
            "metric",
            "statement_kind",
            "status",
            "estimate",
            "ci_low",
            "ci_high",
            "raw_p",
            "adjusted_p",
            "n_questions",
        ]
    ]
    for _dataset, payload in sorted(data["datasets"].items()):
        for claim in payload["claims"]:
            claims_rows.append(
                [
                    claim["dataset"],
                    claim["comparison_id"],
                    "|".join(claim["run_ids"]),
                    "|".join(claim["config_hashes"]),
                    claim["metric"] or "",
                    claim["statement_kind"],
                    claim["status"],
                    "" if claim["estimate"] is None else f"{claim['estimate']:+.4f}",
                    "" if claim["ci_low"] is None else f"{claim['ci_low']:+.4f}",
                    "" if claim["ci_high"] is None else f"{claim['ci_high']:+.4f}",
                    "" if claim["raw_p"] is None else f"{claim['raw_p']:.4f}",
                    "" if claim["adjusted_p"] is None else f"{claim['adjusted_p']:.4f}",
                    "" if claim["n_questions"] is None else claim["n_questions"],
                ]
            )
    write_csv(tables / "claims.csv", claims_rows)

    for dataset, payload in sorted(data["datasets"].items()):
        safe_dataset = dataset.replace("/", "_")
        write_csv(
            tables / f"overall_{safe_dataset}.csv",
            [
                [
                    "method",
                    "questions",
                    "exact_match",
                    "token_f1",
                    "evidence_f1",
                    "tokens_per_query",
                ],
                *[
                    [
                        row["method"],
                        row["questions"],
                        f"{row['exact_match']:.4f}",
                        f"{row['token_f1']:.4f}",
                        f"{row['evidence_f1']:.4f}",
                        "" if row["tokens_per_query"] is None else f"{row['tokens_per_query']:.1f}",
                    ]
                    for row in payload["overall"]
                ],
            ],
        )
        category_columns = payload["categories"]
        write_csv(
            tables / f"categories_{safe_dataset}.csv",
            [["method", *category_columns]]
            + [
                [
                    method,
                    *[
                        _cell_exact_match(payload, method, category)
                        for category in category_columns
                    ],
                ]
                for method in payload["methods"]
            ],
        )
        overview = {row["method"]: row for row in payload["overall"]}
        write_figure(
            plots / f"overall_em_{safe_dataset}.svg",
            bar_chart(
                title=f"Exact match by method ({payload['run_id']})",
                categories=payload["methods"],
                values=[overview[method]["exact_match"] for method in payload["methods"]],
                value_labels=[
                    f"{overview[method]['exact_match']:.3f}" for method in payload["methods"]
                ],
            ),
        )
        write_figure(
            plots / f"category_em_{safe_dataset}.svg",
            heatmap(
                title="Exact match by method and category",
                row_labels=payload["methods"],
                column_labels=category_columns,
                values=[
                    [
                        payload["categories_table"][method]
                        .get(category, {})
                        .get("exact_match", 0.0)
                        for category in category_columns
                    ]
                    for method in payload["methods"]
                ],
            ),
        )
        write_review_sheet(payload["taxonomy"]["sample"], out_dir / f"review_{safe_dataset}.jsonl")

    markdown = render_markdown(data)
    (out_dir / "report.md").write_text(markdown, encoding="utf-8")


def generate_report(
    *,
    config_path: Path,
    source_runs: Sequence[Path],
    controlled_run: Path | None,
    ablation_runs: Sequence[Path],
    output_root: Path,
) -> str:
    """Validate inputs, derive the content-addressed ID, render, and seal.

    Source runs are never written to; the report writes only below
    ``<output_root>/<analysis_id>/``.
    """
    config = load_config(config_path)
    analysis_id = derive_analysis_id(config, source_runs, controlled_run, ablation_runs)
    input_hashes = collect_finalization_hashes(source_runs, controlled_run, ablation_runs)
    validation = validate_analysis_inputs(source_runs, controlled_run, ablation_runs)
    if not validation.valid:
        error = next((issue for issue in validation.issues if issue.severity == "error"), None)
        raise AnalysisInputError(
            error.code if error is not None else "invalid_inputs",
            error.message if error is not None else "analysis inputs are invalid",
        )
    data = build_report_data(
        analysis_id=analysis_id,
        config=config,
        sources=validation.sources,
        controlled=validation.controlled,
        ablations=validation.ablations,
    )
    ensure_analysis_artifact(
        output_root,
        analysis_id=analysis_id,
        config=config,
        input_hashes=input_hashes,
        writer=lambda analysis_dir: write_report_files(analysis_dir, data),
    )
    return analysis_id
