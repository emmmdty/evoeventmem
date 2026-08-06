"""Content-addressed, immutable analysis outputs (C3).

``analysis_id`` is derived from the sorted FINALIZED hashes of every base,
controlled, and ablation run plus the canonical analysis-config hash:

    analysis_id = sha256(sorted(FINALIZED hashes) + config hash)

Same inputs produce the same ID; a changed source or config changes it;
missing source finalization, hash drift, or legacy report input fail the
derivation. Source runs are snapshotted (path, content hash, mtime) before
report generation and verified unchanged afterwards. Analysis outputs are
written only below ``<output_root>/<analysis_id>/`` and sealed with a
write-once ``FINALIZED.json`` that hashes every required output; rerunning an
identical analysis validates or fails and never mutates either the analysis
artifact or its source runs.
"""

from __future__ import annotations

import hashlib
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from benchmarks.analysis.validate_report import validate_analysis_inputs
from benchmarks.common.artifacts import canonical_json_hash, write_json_write_once

ANALYSIS_FINALIZATION_FILENAME = "FINALIZED.json"
ANALYSIS_FINALIZATION_FORMAT = 1
CONFIG_SCHEMA_VERSION = "analysis.config.v1"


class AnalysisInputError(RuntimeError):
    """Raised when the declared analysis inputs cannot produce an artifact."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class BootstrapParams(BaseModel):
    n_boot: int = Field(gt=0)
    seed: int
    alpha: float = Field(gt=0, lt=1)


class ReviewParams(BaseModel):
    target_min_failures: int = Field(ge=1)
    stratified: bool = True


class ComparisonDeclaration(BaseModel):
    """One primary comparison: two methods of one dataset on one metric."""

    id: str = Field(min_length=1)
    left: str = Field(min_length=1)
    right: str = Field(min_length=1)
    metric: str = Field(min_length=1)


class AnalysisConfig(BaseModel):
    """Declared analysis parameters; contains no result values."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["analysis.config.v1"] = CONFIG_SCHEMA_VERSION
    datasets: list[str] = Field(min_length=1)
    metrics: list[str] = Field(min_length=1)
    bootstrap: BootstrapParams
    holm_family: str = Field(min_length=1)
    review: ReviewParams
    comparisons: dict[str, list[ComparisonDeclaration]]


@dataclass(frozen=True)
class LoadedConfig:
    config: AnalysisConfig
    path: Path
    hash: str


class AnalysisFinalization(BaseModel):
    """Write-once seal for one analysis artifact directory."""

    schema_version: int = 1
    format_version: int = ANALYSIS_FINALIZATION_FORMAT
    analysis_id: str = Field(min_length=1)
    config_hash: str = Field(min_length=1)
    input_finalization_hashes: dict[str, str] = Field(default_factory=dict)
    output_hashes: dict[str, str] = Field(default_factory=dict)
    finalized_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("finalized_at")
    @classmethod
    def require_aware_finalized_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("finalized_at must be timezone-aware")
        return value

    def finalization_hash(self) -> str:
        """Content hash of the seal (excluding the timestamp)."""
        return canonical_json_hash(
            {
                "schema_version": self.schema_version,
                "format_version": self.format_version,
                "analysis_id": self.analysis_id,
                "config_hash": self.config_hash,
                "input_finalization_hashes": dict(sorted(self.input_finalization_hashes.items())),
                "output_hashes": dict(sorted(self.output_hashes.items())),
            }
        )


def load_config(path: Path) -> LoadedConfig:
    """Parse and hash the analysis config.

    The hash covers the canonical JSON form of the parsed TOML, so comments
    and formatting do not affect it while any declared value does.
    """
    config_path = Path(path)
    payload = tomllib.loads(config_path.read_text(encoding="utf-8"))
    section = payload.get("analysis")
    if not isinstance(section, dict):
        raise AnalysisInputError("invalid_config", "config must contain an [analysis] table")
    comparisons = section.get("comparisons")
    if isinstance(comparisons, dict):
        normalized: dict[str, Any] = {}
        for dataset, families in comparisons.items():
            if not isinstance(families, dict) or "primary" not in families:
                raise AnalysisInputError(
                    "invalid_config",
                    f"comparisons for dataset {dataset!r} must declare a 'primary' family",
                )
            normalized[str(dataset)] = families["primary"]
        section = {**section, "comparisons": normalized}
    config = AnalysisConfig.model_validate(section)
    return LoadedConfig(config=config, path=config_path, hash=canonical_json_hash(section))


def _require_validated_inputs(
    source_runs: Sequence[Path],
    controlled_run: Path | None,
    ablation_runs: Sequence[Path],
):
    """Load and validate the input set, raising on the first error."""
    result = validate_analysis_inputs(source_runs, controlled_run, ablation_runs)
    if not result.valid:
        error = next((issue for issue in result.issues if issue.severity == "error"), None)
        code = error.code if error is not None else "invalid_inputs"
        message = error.message if error is not None else "analysis inputs are invalid"
        raise AnalysisInputError(code, message)
    return result


def collect_finalization_hashes(
    source_runs: Sequence[Path],
    controlled_run: Path | None,
    ablation_runs: Sequence[Path],
) -> dict[str, str]:
    """Map every input role to its FINALIZED seal hash.

    Base runs contribute their own seal; ablation families contribute the
    family seal plus one seal per arm.
    """
    result = _require_validated_inputs(source_runs, controlled_run, ablation_runs)
    hashes: dict[str, str] = {}
    for source in result.sources:
        hashes[f"source:{source.dataset}:{source.run_id}"] = source.finalization.finalization_hash()
    if result.controlled is not None:
        controlled_id = result.controlled.manifest.run_id
        hashes[f"controlled:{controlled_id}"] = result.controlled.finalization.finalization_hash()
        for arm in result.controlled.arms.values():
            hashes[f"controlled:{controlled_id}:arm:{arm.name}"] = (
                arm.finalization.finalization_hash()
            )
    for ablation in result.ablations:
        ablation_id = ablation.manifest.run_id
        hashes[f"ablation:{ablation.dataset}:{ablation_id}"] = (
            ablation.finalization.finalization_hash()
        )
        for arm in ablation.arms.values():
            hashes[f"ablation:{ablation.dataset}:{ablation_id}:arm:{arm.name}"] = (
                arm.finalization.finalization_hash()
            )
    return hashes


def analysis_id_for(
    config_hash: str,
    finalization_hashes: Mapping[str, str],
) -> str:
    """Content-addressed analysis ID.

    ``sha256(sorted FINALIZED hashes + config hash)``; identical inputs
    produce the identical ID regardless of ordering or role labels.
    """
    payload = {
        "config_hash": config_hash,
        "finalization_hashes": sorted(finalization_hashes.values()),
    }
    return canonical_json_hash(payload)


def derive_analysis_id(
    config: LoadedConfig,
    source_runs: Sequence[Path],
    controlled_run: Path | None,
    ablation_runs: Sequence[Path],
) -> str:
    """Load, validate, and derive the analysis ID for the given inputs."""
    hashes = collect_finalization_hashes(source_runs, controlled_run, ablation_runs)
    return analysis_id_for(config.hash, hashes)


def analysis_output_dir(output_root: Path, analysis_id: str) -> Path:
    return Path(output_root) / analysis_id


# --------------------------------------------------------------------------- #
# Source immutability snapshots.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class SourceFileState:
    path: str
    sha256: str
    mtime_ns: int


@dataclass(frozen=True)
class SourceSnapshot:
    files: tuple[SourceFileState, ...]

    def verify(self) -> bool:
        """Recompute every file's hash and mtime; False on any change."""
        for state in self.files:
            path = Path(state.path)
            try:
                changed = (
                    not path.is_file()
                    or file_sha256(path) != state.sha256
                    or path.stat().st_mtime_ns != state.mtime_ns
                )
            except OSError:
                changed = True
            if changed:
                return False
        return True


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1 << 20)
            if not chunk:
                break
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def snapshot_source_runs(run_dirs: Sequence[Path]) -> SourceSnapshot:
    """Snapshot every file (path, content hash, mtime) under the input runs."""
    states: list[SourceFileState] = []
    for run_dir in run_dirs:
        for path in sorted(Path(run_dir).rglob("*")):
            if not path.is_file():
                continue
            stat = path.stat()
            states.append(
                SourceFileState(
                    path=str(path),
                    sha256=file_sha256(path),
                    mtime_ns=stat.st_mtime_ns,
                )
            )
    return SourceSnapshot(files=tuple(states))


# --------------------------------------------------------------------------- #
# Write-once analysis artifacts.
# --------------------------------------------------------------------------- #


def output_hashes(analysis_dir: Path) -> dict[str, str]:
    """Hash every output file below the analysis directory (excluding the seal)."""
    hashes: dict[str, str] = {}
    for path in sorted(Path(analysis_dir).rglob("*")):
        if not path.is_file():
            continue
        if path.name == ANALYSIS_FINALIZATION_FILENAME and path.parent == analysis_dir:
            continue
        relative = str(path.relative_to(analysis_dir))
        hashes[relative] = canonical_json_hash(path.read_text(encoding="utf-8"))
    return hashes


def finalize_analysis_artifact(
    analysis_dir: Path,
    *,
    analysis_id: str,
    config_hash: str,
    input_hashes: Mapping[str, str],
    finalized_at: datetime | None = None,
) -> AnalysisFinalization:
    """Seal a freshly written analysis directory with a write-once FINALIZED.json.

    The seal hashes every required output. Refuses to overwrite an existing
    seal and refuses to seal an empty artifact directory.
    """
    analysis_dir = Path(analysis_dir)
    seal_path = analysis_dir / ANALYSIS_FINALIZATION_FILENAME
    if seal_path.exists():
        raise FileExistsError(f"analysis artifact already finalized: {seal_path}")
    hashes = output_hashes(analysis_dir)
    if not hashes:
        raise ValueError(f"refusing to finalize an empty analysis directory: {analysis_dir}")
    record = AnalysisFinalization(
        analysis_id=analysis_id,
        config_hash=config_hash,
        input_finalization_hashes=dict(input_hashes),
        output_hashes=hashes,
        finalized_at=finalized_at or datetime.now(UTC),
    )
    write_json_write_once(seal_path, record)
    return record


def load_analysis_finalization(analysis_dir: Path) -> AnalysisFinalization:
    """Load and verify an analysis seal; raises on drift or tampering."""
    analysis_dir = Path(analysis_dir)
    seal_path = analysis_dir / ANALYSIS_FINALIZATION_FILENAME
    if not seal_path.is_file():
        raise AnalysisInputError(
            "missing_analysis_finalization",
            f"no {ANALYSIS_FINALIZATION_FILENAME} in {analysis_dir}",
        )
    try:
        record = AnalysisFinalization.model_validate_json(seal_path.read_text(encoding="utf-8"))
    except ValidationError as exc:
        raise AnalysisInputError(
            "invalid_analysis_finalization",
            f"unreadable analysis seal in {analysis_dir}: {exc}",
        ) from exc
    if record.format_version != ANALYSIS_FINALIZATION_FORMAT:
        raise AnalysisInputError(
            "invalid_analysis_finalization",
            f"analysis seal format_version is {record.format_version}",
        )
    current = output_hashes(analysis_dir)
    if set(current) != set(record.output_hashes):
        missing = sorted(set(record.output_hashes) - set(current))
        unexpected = sorted(set(current) - set(record.output_hashes))
        raise AnalysisInputError(
            "analysis_output_drift",
            f"analysis output set changed: missing={missing} unexpected={unexpected}",
        )
    drifted = sorted(name for name in current if current[name] != record.output_hashes[name])
    if drifted:
        raise AnalysisInputError(
            "analysis_output_drift",
            f"analysis output hash drift on: {drifted}",
        )
    return record


def ensure_analysis_artifact(
    output_root: Path,
    *,
    analysis_id: str,
    config: LoadedConfig,
    input_hashes: Mapping[str, str],
    writer: Any,
) -> Path:
    """Write or validate one content-addressed analysis artifact.

    ``writer`` is a callable ``(analysis_dir: Path) -> None`` that renders the
    report below its directory. An existing, intact artifact is validated and
    returned without mutation; a drifted one fails; a fresh one is written
    once and sealed.
    """
    analysis_dir = analysis_output_dir(output_root, analysis_id)
    if analysis_dir.exists():
        if not analysis_dir.is_dir():
            raise AnalysisInputError(
                "invalid_analysis_artifact",
                f"{analysis_dir} exists but is not a directory",
            )
        record = load_analysis_finalization(analysis_dir)
        if record.analysis_id != analysis_id:
            raise AnalysisInputError(
                "analysis_id_mismatch",
                f"{analysis_dir} was sealed for analysis {record.analysis_id}, "
                f"expected {analysis_id}",
            )
        return analysis_dir
    analysis_dir.mkdir(parents=True, exist_ok=False)
    writer(analysis_dir)
    finalize_analysis_artifact(
        analysis_dir,
        analysis_id=analysis_id,
        config_hash=config.hash,
        input_hashes=input_hashes,
    )
    return analysis_dir
