"""Run-report validation (M15 acceptance: report validation catches incompatible run configs).

``validate_report <runs_dir>`` inspects every run directory that contains a
``summary.json`` and ``config.json`` pair:

1. Internal integrity: the summary's ``config_hash`` must equal the hash of the
   on-disk ``config.json``, sample/question validation must pass, and the
   derived per-method artifacts must be present.
2. Pairwise compatibility: any two runs that are compared in a report must
   share the same reader, embedding model, dataset, budgets, caps, and method
   set, and both must be full-scope runs (no ``sample_limit``). Incompatible
   pairs are flagged as errors; subset-scope runs are flagged as warnings.

Exit status is 0 only when every run is valid and every pair is compatible.
"""

from __future__ import annotations

import hashlib
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

SUMMARY_SCHEMA = "locomo.summary.v1"
CONFIG_SCHEMA = "locomo.config.v1"


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
                message=(
                    f"method sets differ: {sorted(methods_a)} vs {sorted(methods_b)}"
                ),
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
            issue.code == "config_hash_mismatch" for issue in report.run_issues
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
                        "run excluded from pairwise compatibility because "
                        "its config is untrusted"
                    ),
                )
            )
    for left in infos:
        for right in infos:
            if str(left.run_dir) >= str(right.run_dir):
                continue
            report.pair_issues.extend(pair_issues(left, right))

    report.valid = not any(
        issue.severity == "error"
        for issue in [*report.run_issues, *report.pair_issues]
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
    if len(argv or ()) != 1:
        print(
            "usage: uv run python -m benchmarks.analysis.validate_report <runs_dir>",
            file=sys.stderr,
        )
        return 2
    runs_root = Path(argv[0])
    if not runs_root.is_dir():
        print(f"runs root does not exist: {runs_root}", file=sys.stderr)
        return 2
    report = validate_runs(runs_root)
    write_validation_artifact(report, runs_root)
    print(
        json.dumps(report.as_dict(), indent=2, sort_keys=True)
    )
    return 0 if report.valid else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
