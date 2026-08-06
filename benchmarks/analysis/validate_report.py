"""Run-report validation (M15 acceptance: report validation catches incompatible run configs).

Two layers coexist:

1. **Legacy layer** (``validate_runs``): inspects old ``summary.json`` /
   ``config.json`` run trees. It remains available only for historical
   diagnostic trees; its output is never treated as the analysis contract.

2. **Dataset-neutral layer** (``validate_analysis_inputs``): validates
   finalized B-schema source runs (see :mod:`benchmarks.analysis.loaders`)
   against the C2 contract. It rejects zero source runs, unknown schemas,
   missing or hash-drifted finalization, dirty/diagnostic/subset publication
   input, missing predictions/samples/retrieval/caches, missing/duplicate IDs,
   config/dataset hash drift, and incompatible reader/extractor/embedding/
   tokenizer/budget/policy settings. Compatibility is enforced within each
   dataset and within each paired ablation family; LongMemEval and LoCoMo may
   use different model stacks. Validation returns structured issues and never
   writes below a source run.

Exit status is 0 only when every run is valid and every pair is compatible.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from benchmarks.analysis.loaders import (
    LoadedAblationRun,
    LoadedRun,
    LoadError,
    load_ablation_run,
    load_base_run,
)

SUMMARY_SCHEMA = "locomo.summary.v1"
CONFIG_SCHEMA = "locomo.config.v1"

# Fields that must match between source runs of the same dataset.
DATASET_COMPATIBILITY_FIELDS: tuple[tuple[str, str], ...] = (
    ("reader", "model_id"),
    ("extractor", "model_id"),
    ("embedding", "model_id"),
    ("tokenizer", "name"),
    ("tokenizer", "version"),
    ("policies", "extraction"),
    ("policies", "router"),
    ("policies", "retrieval"),
    ("policies", "consolidation"),
    ("budget", "input_tokens"),
    ("budget", "max_items_per_source"),
    ("budget", "max_candidates_per_source"),
)
# Fields that must match across arms of one ablation family.
FAMILY_COMPATIBILITY_FIELDS: tuple[tuple[str, str], ...] = (
    ("reader", "model_id"),
    ("extractor", "model_id"),
    ("embedding", "model_id"),
    ("tokenizer", "name"),
    ("tokenizer", "version"),
    ("policies", "extraction"),
    ("policies", "router"),
    ("policies", "retrieval"),
    ("policies", "consolidation"),
    ("budget", "max_items_per_source"),
    ("budget", "max_candidates_per_source"),
)


class RunSnapshot(BaseModel):
    run_id: str = Field(min_length=1)
    run_dir: str = Field(min_length=1)
    summary: dict[str, Any]
    config: dict[str, Any]
    config_hash: str = Field(min_length=1)


class RunIssue(BaseModel):
    run_id: str = Field(min_length=1)
    severity: str = Field(min_length=1)
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)


class PairIssue(BaseModel):
    run_a: str = Field(min_length=1)
    run_b: str = Field(min_length=1)
    severity: str = Field(min_length=1)
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)


class ValidationReport(BaseModel):
    runs_root: str = Field(min_length=1)
    run_issues: list[RunIssue] = Field(default_factory=list)
    pair_issues: list[PairIssue] = Field(default_factory=list)
    valid: bool = True

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


@dataclass(frozen=True)
class RunInfo:
    run_id: str
    run_dir: Path
    summary: dict[str, Any]
    config: dict[str, Any]


COMPATIBILITY_FIELDS: tuple[tuple[str, str], ...] = (
    ("chat_model_id", "summary"),
    ("embedding_model_id", "summary"),
    ("embedding_provider", "summary"),
    ("reader_thinking", "summary"),
    ("reader_format_directive", "summary"),
    ("max_input_tokens", "summary"),
    ("max_candidates_per_source", "summary"),
    ("max_items_per_source", "summary"),
    ("dataset_hash", "summary"),
    ("dataset_path", "config"),
)


def hash_json(payload: dict[str, Any]) -> str:
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def load_run(run_dir: Path) -> RunSnapshot | None:
    summary_path = run_dir / "summary.json"
    config_path = run_dir / "config.json"
    if not summary_path.is_file() or not config_path.is_file():
        return None
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    config = json.loads(config_path.read_text(encoding="utf-8"))
    return RunSnapshot(
        run_id=str(summary.get("run_id") or run_dir.name),
        run_dir=str(run_dir),
        summary=summary,
        config=config,
        config_hash=str(summary.get("config_hash") or ""),
    )


def run_issues(snapshot: RunSnapshot) -> list[RunIssue]:
    issues: list[RunIssue] = []
    run_id = snapshot.run_id

    if snapshot.config_hash != hash_json(snapshot.config):
        issues.append(
            RunIssue(
                run_id=run_id,
                severity="error",
                code="config_hash_mismatch",
                message="summary.config_hash does not match the on-disk config.json",
            )
        )
    if snapshot.summary.get("schema_version") != SUMMARY_SCHEMA:
        issues.append(
            RunIssue(
                run_id=run_id,
                severity="error",
                code="unexpected_summary_schema",
                message=f"summary.schema_version is {snapshot.summary.get('schema_version')!r}",
            )
        )
    if snapshot.config.get("schema_version") != CONFIG_SCHEMA:
        issues.append(
            RunIssue(
                run_id=run_id,
                severity="error",
                code="unexpected_config_schema",
                message=f"config.schema_version is {snapshot.config.get('schema_version')!r}",
            )
        )

    sample_validation = snapshot.summary.get("sample_validation") or {}
    question_validation = snapshot.summary.get("question_validation") or {}
    if not sample_validation.get("valid", False):
        issues.append(
            RunIssue(
                run_id=run_id,
                severity="error",
                code="incomplete_samples",
                message=(
                    f"sample_validation invalid: "
                    f"{sample_validation.get('completed_sample_count')} of "
                    f"{sample_validation.get('expected_sample_count')} samples completed"
                ),
            )
        )
    if not question_validation.get("valid", False):
        issues.append(
            RunIssue(
                run_id=run_id,
                severity="error",
                code="incomplete_questions",
                message=(
                    f"question_validation invalid: "
                    f"{question_validation.get('completed_question_count')} of "
                    f"{question_validation.get('expected_question_count')} questions completed"
                ),
            )
        )

    for method in snapshot.config.get("methods", []):
        method_dir = Path(snapshot.run_dir) / method
        for derived in ("predictions.jsonl", "samples.jsonl", "retrieval.jsonl"):
            if method in ("no_memory", "full_context", "session_summary"):
                if derived == "retrieval.jsonl" and not (method_dir / derived).is_file():
                    issues.append(
                        RunIssue(
                            run_id=run_id,
                            severity="warning",
                            code="missing_derived_artifact",
                            message=f"method {method} has no {derived}",
                        )
                    )
            elif not (method_dir / derived).is_file():
                issues.append(
                    RunIssue(
                        run_id=run_id,
                        severity="error",
                        code="missing_derived_artifact",
                        message=f"method {method} has no {derived}",
                    )
                )

    if snapshot.summary.get("git_dirty") is True:
        issues.append(
            RunIssue(
                run_id=run_id,
                severity="warning",
                code="git_dirty",
                message="run was executed on a dirty working tree",
            )
        )
    return issues


def pair_issues(left: RunInfo, right: RunInfo) -> list[PairIssue]:
    issues: list[PairIssue] = []
    for field_name, scope in COMPATIBILITY_FIELDS:
        source = left.summary if scope == "summary" else left.config
        other = right.summary if scope == "summary" else right.config
        if source.get(field_name) != other.get(field_name):
            issues.append(
                PairIssue(
                    run_a=left.run_id,
                    run_b=right.run_id,
                    severity="error",
                    code="incompatible_config",
                    message=(
                        f"{field_name} differs: {source.get(field_name)!r} vs "
                        f"{other.get(field_name)!r}"
                    ),
                )
            )
    methods_a = set(left.config.get("methods", []))
    methods_b = set(right.config.get("methods", []))
    if methods_a != methods_b:
        issues.append(
            PairIssue(
                run_a=left.run_id,
                run_b=right.run_id,
                severity="error",
                code="incompatible_methods",
                message=(f"method sets differ: {sorted(methods_a)} vs {sorted(methods_b)}"),
            )
        )
    for run in (left, right):
        if run.config.get("sample_limit") is not None:
            issues.append(
                PairIssue(
                    run_a=left.run_id,
                    run_b=right.run_id,
                    severity="warning",
                    code="subset_scope",
                    message=f"run {run.run_id} is a subset run (sample_limit set)",
                )
            )
    return issues


def validate_runs(runs_root: Path) -> ValidationReport:
    report = ValidationReport(runs_root=str(runs_root))
    snapshots: list[RunSnapshot] = []
    run_dirs: list[Path] = []
    for entry in sorted(runs_root.iterdir()):
        if entry.is_dir():
            snapshot = load_run(entry)
            if snapshot is None:
                continue
            snapshots.append(snapshot)
            run_dirs.append(entry)

    infos: list[RunInfo] = []
    for snapshot in snapshots:
        report.run_issues.extend(run_issues(snapshot))
        if not any(
            issue.code == "config_hash_mismatch"
            for issue in report.run_issues
            if issue.run_id == snapshot.run_id
        ):
            infos.append(
                RunInfo(
                    run_id=snapshot.run_id,
                    run_dir=Path(snapshot.run_dir),
                    summary=snapshot.summary,
                    config=snapshot.config,
                )
            )
        else:
            report.run_issues.append(
                RunIssue(
                    run_id=snapshot.run_id,
                    severity="error",
                    code="excluded_from_pairwise",
                    message=(
                        "run excluded from pairwise compatibility because its config is untrusted"
                    ),
                )
            )
    for left in infos:
        for right in infos:
            if str(left.run_dir) >= str(right.run_dir):
                continue
            report.pair_issues.extend(pair_issues(left, right))

    report.valid = not any(
        issue.severity == "error" for issue in [*report.run_issues, *report.pair_issues]
    )
    return report


def write_validation_artifact(report: ValidationReport, runs_root: Path) -> Path:
    out_dir = runs_root / "report"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "validation.json"
    temporary = path.with_name(f".{path.name}.{__name__}.tmp")
    temporary.write_text(
        json.dumps(report.as_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
    return path


def main(argv: Sequence[str] | None = None) -> int:
    """C8 CLI: locate and verify exactly the content-addressed analysis artifact.

    Derives the analysis ID from the same inputs as the report generator,
    locates ``<artifact-root>/<analysis_id>/``, and verifies its
    ``FINALIZED.json``. Missing or legacy sources, drifted artifacts, and
    missing artifacts return nonzero with stable error codes. Never writes.
    """
    parser = argparse.ArgumentParser(
        description="Validate the content-addressed M15 analysis report."
    )
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--source-run", type=Path, action="append", required=True)
    parser.add_argument("--controlled-run", type=Path, default=None)
    parser.add_argument("--ablation-run", type=Path, action="append", default=[])
    parser.add_argument("--artifact-root", type=Path, required=True)
    args = parser.parse_args(argv)

    # Local imports avoid a cycle: finalization imports validate_analysis_inputs
    # from this module.
    from benchmarks.analysis.finalization import (
        AnalysisInputError,
        derive_analysis_id,
        error_exit_code,
        load_analysis_finalization,
        load_config,
    )

    if not Path(args.config).is_file():
        print(
            f"error[missing_config]: analysis config does not exist: {args.config}",
            file=sys.stderr,
        )
        return error_exit_code("missing_config", usage=True)
    try:
        config = load_config(args.config)
        analysis_id = derive_analysis_id(
            config,
            args.source_run,
            args.controlled_run,
            args.ablation_run,
        )
    except AnalysisInputError as exc:
        print(f"error[{exc.code}]: {exc}", file=sys.stderr)
        return error_exit_code(exc.code)

    artifact_dir = Path(args.artifact_root) / analysis_id
    if not artifact_dir.is_dir():
        print(
            f"error[missing_analysis_finalization]: no artifact for analysis "
            f"{analysis_id} under {args.artifact_root}",
            file=sys.stderr,
        )
        return error_exit_code("missing_analysis_finalization")
    try:
        seal = load_analysis_finalization(artifact_dir)
    except AnalysisInputError as exc:
        print(f"error[{exc.code}]: {exc}", file=sys.stderr)
        return error_exit_code(exc.code)
    if seal.analysis_id != analysis_id:
        print(
            f"error[analysis_id_mismatch]: {artifact_dir} is sealed for "
            f"{seal.analysis_id}, expected {analysis_id}",
            file=sys.stderr,
        )
        return error_exit_code("analysis_id_mismatch")
    print(
        json.dumps(
            {
                "analysis_id": analysis_id,
                "artifact_dir": str(artifact_dir),
                "valid": True,
                "output_files": len(seal.output_hashes),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


# --------------------------------------------------------------------------- #
# C2 dataset-neutral validation layer.
# --------------------------------------------------------------------------- #


class AnalysisIssue(BaseModel):
    """One structured validation issue with a stable machine-readable code."""

    code: str = Field(min_length=1)
    severity: str = Field(pattern="^(error|warning)$")
    run_dir: str | None = None
    message: str = Field(min_length=1)


@dataclass
class ValidationResult:
    """Structured outcome of :func:`validate_analysis_inputs`.

    Never writes below a source run; loaded artifacts are exposed read-only.
    """

    valid: bool
    issues: list[AnalysisIssue]
    sources: list[LoadedRun] = field(default_factory=list)
    controlled: LoadedAblationRun | None = None
    ablations: list[LoadedAblationRun] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "issues": [issue.model_dump(mode="json") for issue in self.issues],
            "source_runs": [str(source.run_dir) for source in self.sources],
            "controlled_run": str(self.controlled.run_dir) if self.controlled else None,
            "ablation_runs": [str(run.run_dir) for run in self.ablations],
        }

    def error_codes(self) -> list[str]:
        return [issue.code for issue in self.issues if issue.severity == "error"]


def _issue(
    code: str,
    message: str,
    run_dir: Path | str | None = None,
    *,
    severity: str = "error",
) -> AnalysisIssue:
    return AnalysisIssue(
        code=code,
        severity=severity,
        run_dir=str(run_dir) if run_dir is not None else None,
        message=message,
    )


def _manifest_field(manifest: Any, field_name: str) -> Any:
    section, key = field_name
    value = getattr(manifest, section, None)
    return getattr(value, key, None)


def _check_dataset_compatibility(sources: Sequence[LoadedRun], issues: list[AnalysisIssue]) -> None:
    by_dataset: dict[str, list[LoadedRun]] = defaultdict(list)
    for source in sources:
        by_dataset[source.dataset].append(source)
    for dataset, group in sorted(by_dataset.items()):
        if len(group) < 2:
            continue
        first = group[0]
        for other in group[1:]:
            for field_name in DATASET_COMPATIBILITY_FIELDS:
                left = _manifest_field(first.manifest, field_name)
                right = _manifest_field(other.manifest, field_name)
                if left != right:
                    issues.append(
                        _issue(
                            "incompatible_within_dataset",
                            f"within dataset {dataset}: {'.'.join(field_name)} differs: "
                            f"{left!r} vs {right!r}",
                            run_dir=other.run_dir,
                        )
                    )
            if set(first.manifest.methods) != set(other.manifest.methods):
                issues.append(
                    _issue(
                        "incompatible_methods",
                        f"within dataset {dataset}: method sets differ: "
                        f"{sorted(first.manifest.methods)} vs {sorted(other.manifest.methods)}",
                        run_dir=other.run_dir,
                    )
                )
            if first.manifest.dataset_hash != other.manifest.dataset_hash:
                issues.append(
                    _issue(
                        "dataset_drift",
                        f"within dataset {dataset}: dataset hashes differ between source runs",
                        run_dir=other.run_dir,
                    )
                )


def _check_ablation_links(
    ablation: LoadedAblationRun,
    controlled: LoadedAblationRun | None,
    sources: Sequence[LoadedRun],
    issues: list[AnalysisIssue],
) -> None:
    source_by_dataset = {source.dataset: source for source in sources}
    base_source = source_by_dataset.get(ablation.dataset)
    for arm in ablation.arms.values():
        if controlled is not None and (
            arm.manifest.controlled_run_hash != controlled.finalization.finalization_hash()
        ):
            issues.append(
                _issue(
                    "ablation_controlled_hash_mismatch",
                    f"arm {arm.name} embeds controlled_run_hash "
                    f"{arm.manifest.controlled_run_hash} which does not match the "
                    "controlled run finalization hash",
                    run_dir=arm.run_dir,
                )
            )
        if base_source is not None and (
            arm.manifest.base_run_hash != base_source.finalization.finalization_hash()
        ):
            issues.append(
                _issue(
                    "ablation_base_run_mismatch",
                    f"arm {arm.name} embeds base_run_hash "
                    f"{arm.manifest.base_run_hash} which does not match the finalized "
                    f"{ablation.dataset} source run hash",
                    run_dir=arm.run_dir,
                )
            )


def _check_ablation_family(ablation: LoadedAblationRun, issues: list[AnalysisIssue]) -> None:
    arms = list(ablation.arms.values())
    if len(arms) < 2:
        issues.append(
            _issue(
                "missing_ablation_base",
                f"ablation family {ablation.run_dir} has fewer than two arms",
                run_dir=ablation.run_dir,
            )
        )
    if not arms:
        return
    reference = arms[0].manifest
    for arm in arms[1:]:
        for field_name in FAMILY_COMPATIBILITY_FIELDS:
            left = _manifest_field(reference, field_name)
            right = _manifest_field(arm.manifest, field_name)
            if left != right:
                issues.append(
                    _issue(
                        "incompatible_ablation_family",
                        f"arm {arm.name} differs from arm {reference.run_id} on "
                        f"{'.'.join(field_name)}: {left!r} vs {right!r}",
                        run_dir=arm.run_dir,
                    )
                )
        if arm.manifest.config_hash != reference.config_hash:
            issues.append(
                _issue(
                    "incompatible_ablation_family",
                    f"arm {arm.name} config hash differs from the family config",
                    run_dir=arm.run_dir,
                )
            )
        if arm.manifest.dataset_hash != reference.dataset_hash:
            issues.append(
                _issue(
                    "incompatible_ablation_family",
                    f"arm {arm.name} dataset hash differs from the family dataset",
                    run_dir=arm.run_dir,
                )
            )


def validate_analysis_inputs(
    source_runs: Sequence[Path],
    controlled_run: Path | None = None,
    ablation_runs: Sequence[Path] = (),
) -> ValidationResult:
    """Validate a full analysis input set and return structured issues.

    Rejects zero source runs, structural failures, cross-referenced hash
    mismatches, and incompatible settings. Never writes below a source run.
    """
    issues: list[AnalysisIssue] = []
    sources: list[LoadedRun] = []

    if not source_runs:
        issues.append(_issue("zero_source_runs", "at least one source run is required"))
    for path in source_runs:
        try:
            sources.append(load_base_run(Path(path)))
        except LoadError as exc:
            issues.append(_issue(exc.code, str(exc), run_dir=path))

    controlled: LoadedAblationRun | None = None
    if ablation_runs and controlled_run is None:
        issues.append(
            _issue(
                "missing_controlled_run",
                "ablation runs require the paired controlled run (--controlled-run)",
            )
        )
    if controlled_run is not None:
        try:
            controlled = load_ablation_run(Path(controlled_run))
        except LoadError as exc:
            issues.append(_issue(exc.code, str(exc), run_dir=controlled_run))

    ablations: list[LoadedAblationRun] = []
    for path in ablation_runs:
        try:
            ablations.append(load_ablation_run(Path(path)))
        except LoadError as exc:
            issues.append(_issue(exc.code, str(exc), run_dir=path))

    _check_dataset_compatibility(sources, issues)
    for ablation in ablations:
        _check_ablation_family(ablation, issues)
        _check_ablation_links(ablation, controlled, sources, issues)

    valid = not any(issue.severity == "error" for issue in issues)
    return ValidationResult(
        valid=valid,
        issues=issues,
        sources=sources,
        controlled=controlled,
        ablations=ablations,
    )


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
