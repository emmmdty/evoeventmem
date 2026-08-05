from __future__ import annotations

import hashlib
import json
import os
import subprocess
from collections.abc import Iterable
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

ARTIFACT_SCHEMA_VERSION = 1
CONTRACT_SCHEMA_VERSION = 1
FINALIZATION_FORMAT = 1
FINALIZED_FILENAME = "FINALIZED.json"


class EvidencePrediction(BaseModel):
    source_type: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    locator: str | None = None
    quote: str | None = None


class PredictionRecord(BaseModel):
    schema_version: int = ARTIFACT_SCHEMA_VERSION
    dataset: str = Field(min_length=1)
    sample_id: str = Field(min_length=1)
    question_id: str = Field(min_length=1)
    prediction: str
    evidence: list[EvidencePrediction] = Field(default_factory=list)
    latency_ms: float = Field(ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RunMetadata(BaseModel):
    schema_version: int = ARTIFACT_SCHEMA_VERSION
    run_id: str = Field(min_length=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    model_id: str = Field(min_length=1)
    config_hash: str = Field(min_length=1)
    git_commit: str = Field(min_length=1)
    dataset_fingerprint: str = Field(min_length=1)
    metrics_version: str = "deterministic-v1"
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("created_at")
    @classmethod
    def require_aware_created_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("created_at must be timezone-aware")
        return value


class SampleEvaluation(BaseModel):
    schema_version: int = ARTIFACT_SCHEMA_VERSION
    dataset: str = Field(min_length=1)
    sample_id: str = Field(min_length=1)
    question_id: str = Field(min_length=1)
    exact_match: float = Field(ge=0, le=1)
    token_f1: float = Field(ge=0, le=1)
    evidence_precision: float = Field(ge=0, le=1)
    evidence_recall: float = Field(ge=0, le=1)
    evidence_f1: float = Field(ge=0, le=1)
    latency_ms: float = Field(ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)


class RunSummary(BaseModel):
    schema_version: int = ARTIFACT_SCHEMA_VERSION
    metadata: RunMetadata
    sample_count: int = Field(ge=0)
    exact_match: float = Field(ge=0, le=1)
    token_f1: float = Field(ge=0, le=1)
    evidence_precision: float = Field(ge=0, le=1)
    evidence_recall: float = Field(ge=0, le=1)
    evidence_f1: float = Field(ge=0, le=1)
    total_latency_ms: float = Field(ge=0)
    total_input_tokens: int | None = Field(default=None, ge=0)
    total_output_tokens: int | None = Field(default=None, ge=0)
    predictions_path: str
    samples_path: str


def write_json_write_once(path: Path, payload: BaseModel | dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serializable = payload.model_dump(mode="json") if isinstance(payload, BaseModel) else payload
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            json.dump(serializable, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.link(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def write_jsonl_write_once(path: Path, records: Iterable[BaseModel | dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            for record in records:
                serializable = (
                    record.model_dump(mode="json") if isinstance(record, BaseModel) else record
                )
                handle.write(json.dumps(serializable, sort_keys=True))
                handle.write("\n")
        os.link(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def current_git_commit() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return "unknown"
    return result.stdout.strip() or "unknown"


# --------------------------------------------------------------------------- #
# B-ARTIFACT contract models.
# --------------------------------------------------------------------------- #


class ArtifactClass(StrEnum):
    """Distinct artifact classes. Only clean, complete `publication` runs can
    finalize as publication evidence."""

    SMOKE = "smoke"
    DIAGNOSTIC = "diagnostic"
    PUBLICATION = "publication"


def canonical_json(value: Any) -> str:
    """Serialize a value to a deterministic canonical JSON string.

    Keys are sorted, separators are compact, and no trailing whitespace is
    emitted. This is the single canonical form used for all contract hashes.
    """
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def canonical_json_hash(value: Any, *, encoding: str = "utf-8") -> str:
    """Return a content-addressed sha256 hash of the canonical JSON form."""
    digest = hashlib.sha256(canonical_json(value).encode(encoding)).hexdigest()
    return f"sha256:{digest}"


def required_hash(path: Path) -> str:
    """Compute the content hash of a required artifact path."""
    return canonical_json_hash(path.read_bytes().decode("utf-8"))


class ProviderIdentity(BaseModel):
    kind: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    version: str = Field(default="")
    endpoint: str = Field(min_length=1)


class TokenizerIdentity(BaseModel):
    name: str = Field(min_length=1)
    version: str = Field(default="")


class PolicyVersions(BaseModel):
    extraction: str = Field(min_length=1)
    router: str = Field(default="")
    retrieval: str = Field(min_length=1)
    consolidation: str = Field(min_length=1)


class BudgetSpec(BaseModel):
    input_tokens: int = Field(gt=0)
    max_items_per_source: int | None = Field(default=None, gt=0)
    max_candidates: int | None = Field(default=None, gt=0)


class GitState(BaseModel):
    commit: str = Field(min_length=1)
    dirty: bool
    dirty_diff_hash: str | None = Field(default=None, min_length=1)


class RunManifest(BaseModel):
    schema_version: int = CONTRACT_SCHEMA_VERSION
    run_id: str = Field(min_length=1)
    artifact_class: ArtifactClass
    dataset: str = Field(min_length=1)
    dataset_path: str = Field(min_length=1)
    dataset_hash: str = Field(min_length=1)
    scope: str = Field(min_length=1)
    methods: list[str] = Field(min_length=1)
    reader: ProviderIdentity
    extractor: ProviderIdentity
    embedding: ProviderIdentity
    tokenizer: TokenizerIdentity
    policies: PolicyVersions
    budget: BudgetSpec
    git: GitState
    config_hash: str = Field(min_length=1)
    expected_sample_ids: list[str] = Field(default_factory=list)
    expected_question_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def manifest_hash(self) -> str:
        return canonical_json_hash(_manifest_canonical(self))

    def _duplicate_ids(self) -> list[str]:
        return [
            item
            for item, count in _id_counts(self.expected_sample_ids).items()
            if count > 1
        ]


class AblationRunManifest(RunManifest):
    ablation: str = Field(min_length=1)
    controlled_run_hash: str = Field(min_length=1)
    base_run_hash: str = Field(min_length=1)
    changed_factors: list[str] = Field(min_length=1)


def _manifest_canonical(manifest: RunManifest) -> dict[str, Any]:
    """Canonical form of a manifest used for hashing."""
    return {
        "schema_version": manifest.schema_version,
        "run_id": manifest.run_id,
        "artifact_class": manifest.artifact_class.value,
        "dataset": manifest.dataset,
        "dataset_path": manifest.dataset_path,
        "dataset_hash": manifest.dataset_hash,
        "scope": manifest.scope,
        "methods": list(manifest.methods),
        "reader": manifest.reader.model_dump(),
        "extractor": manifest.extractor.model_dump(),
        "embedding": manifest.embedding.model_dump(),
        "tokenizer": manifest.tokenizer.model_dump(),
        "policies": manifest.policies.model_dump(),
        "budget": manifest.budget.model_dump(),
        "git": manifest.git.model_dump(),
        "config_hash": manifest.config_hash,
        "expected_sample_ids": list(manifest.expected_sample_ids),
        "expected_question_ids": list(manifest.expected_question_ids),
    }


def _id_counts(ids: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in ids:
        counts[item] = counts.get(item, 0) + 1
    return counts


class ExtractionRejection(BaseModel):
    schema_version: int = CONTRACT_SCHEMA_VERSION
    raw_turn_id: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    span: str | None = None


class ExtractionSnapshot(BaseModel):
    schema_version: int = CONTRACT_SCHEMA_VERSION
    snapshot_id: str = Field(min_length=1)
    conversation_id: str = Field(min_length=1)
    extractor: ProviderIdentity
    raw_turn_count: int = Field(ge=0)
    event_count: int = Field(ge=0)
    rejections: list[ExtractionRejection] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("created_at")
    @classmethod
    def require_aware_created_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("created_at must be timezone-aware")
        return value

    def snapshot_hash(self) -> str:
        return canonical_json_hash(_snapshot_canonical(self))

    def provenance_ok(self) -> bool:
        return self.event_count > 0


def _snapshot_canonical(snapshot: ExtractionSnapshot) -> dict[str, Any]:
    return {
        "schema_version": snapshot.schema_version,
        "snapshot_id": snapshot.snapshot_id,
        "conversation_id": snapshot.conversation_id,
        "extractor": snapshot.extractor.model_dump(),
        "raw_turn_count": snapshot.raw_turn_count,
        "event_count": snapshot.event_count,
        "rejections": [r.model_dump() for r in snapshot.rejections],
    }


class PackedItem(BaseModel):
    item_id: str = Field(min_length=1)
    evidence_refs: list[str] = Field(min_length=1)
    content_tokens: int = Field(ge=0)


class SourceFailure(BaseModel):
    source: str = Field(min_length=1)
    reason_code: str = Field(min_length=1)
    degraded_policy: bool
    duration_ms: float = Field(default=0.0, ge=0)


class RetrievalRecord(BaseModel):
    schema_version: int = CONTRACT_SCHEMA_VERSION
    question_id: str = Field(min_length=1)
    evidence_policy: Literal["constrained", "provenance_only"]
    packed_items: list[PackedItem] = Field(default_factory=list)
    total_input_tokens: int = Field(ge=0)
    content_tokens: int = Field(ge=0)
    prompt_overhead_tokens: int = Field(ge=0)
    packing_bound: bool = False
    source_failures: list[SourceFailure] = Field(default_factory=list)


class EvidenceRecord(BaseModel):
    schema_version: int = CONTRACT_SCHEMA_VERSION
    question_id: str = Field(min_length=1)
    raw_turn_id: str = Field(min_length=1)
    span: str = Field(min_length=1)
    exact: bool = True


class ConsolidationAction(StrEnum):
    MERGE = "merge"
    SUPERSEDE = "supersede"
    KEEP = "keep"
    REJECT = "reject"


class ConsolidationRecord(BaseModel):
    schema_version: int = CONTRACT_SCHEMA_VERSION
    sample_id: str = Field(min_length=1)
    evidence: list[EvidenceRecord] = Field(default_factory=list)
    action: ConsolidationAction
    resolved_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("resolved_at")
    @classmethod
    def require_aware_resolved_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("resolved_at must be timezone-aware")
        return value


class FinalizationRecord(BaseModel):
    schema_version: int = CONTRACT_SCHEMA_VERSION
    format_version: int = FINALIZATION_FORMAT
    artifact_class: ArtifactClass
    manifest_hash: str = Field(min_length=1)
    required_hashes: dict[str, str] = Field(default_factory=dict)
    completion_counts: dict[str, int] = Field(default_factory=dict)
    finalized_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("finalized_at")
    @classmethod
    def require_aware_finalized_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("finalized_at must be timezone-aware")
        return value

    def finalization_hash(self) -> str:
        return canonical_json_hash(_finalization_canonical(self))


def _finalization_canonical(record: FinalizationRecord) -> dict[str, Any]:
    return {
        "schema_version": record.schema_version,
        "format_version": record.format_version,
        "artifact_class": record.artifact_class.value,
        "manifest_hash": record.manifest_hash,
        "required_hashes": dict(sorted(record.required_hashes.items())),
        "completion_counts": dict(sorted(record.completion_counts.items())),
        "finalized_at": record.finalized_at.isoformat(),
    }


# --------------------------------------------------------------------------- #
# Working / finalized state machine.
# --------------------------------------------------------------------------- #

WORKING_REQUIRED_FILES = ("manifest.json",)
DERIVED_FILES = ("summary.json", "predictions.jsonl", "samples.jsonl")

PUBLICATION_REQUIRED_FILES = (
    "manifest.json",
    "extraction_snapshot.json",
    "retrieval.jsonl",
    "evidence.jsonl",
    "consolidation.jsonl",
)


def required_file_paths(run_dir: Path, artifact_class: ArtifactClass) -> list[Path]:
    """Return the required artifact paths for a run of a given class."""
    if artifact_class is ArtifactClass.PUBLICATION:
        return [run_dir / name for name in PUBLICATION_REQUIRED_FILES]
    return [run_dir / name for name in WORKING_REQUIRED_FILES]


def require_manifest(run_dir: Path) -> RunManifest:
    manifest_path = run_dir / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"missing manifest: {manifest_path}")
    return RunManifest.model_validate_json(manifest_path.read_text())


def validate_manifest_ids(manifest: RunManifest) -> None:
    """Reject missing or duplicate expected sample/question IDs."""
    if not manifest.expected_sample_ids or not manifest.expected_question_ids:
        raise ValueError("manifest must declare expected sample and question IDs")
    for label, ids in (
        ("sample", manifest.expected_sample_ids),
        ("question", manifest.expected_question_ids),
    ):
        duplicates = [item for item, count in _id_counts(ids).items() if count > 1]
        if duplicates:
            raise ValueError(f"duplicate {label} IDs: {duplicates}")


def _validate_required_files(run_dir: Path, record: FinalizationRecord) -> None:
    """Verify every required path exists and has recorded a hash."""
    required = required_file_paths(run_dir, record.artifact_class)
    for path in required:
        if path.name not in record.required_hashes:
            raise ValueError(f"missing required hash for {path.name}")
        if not path.exists():
            raise FileNotFoundError(f"missing required artifact: {path}")


def _validate_publication(run_dir: Path, manifest: RunManifest) -> None:
    if manifest.artifact_class is not ArtifactClass.PUBLICATION:
        return
    if manifest.git.dirty:
        raise ValueError("publication run requires a clean Git tree")
    validate_manifest_ids(manifest)


def check_working_state(run_dir: Path) -> RunManifest:
    """Validate a working run's manifest and required files before work begins."""
    manifest = require_manifest(run_dir)
    validate_manifest_ids(manifest)
    for path in required_file_paths(run_dir, manifest.artifact_class):
        if not path.exists():
            raise FileNotFoundError(f"missing required artifact: {path}")
    return manifest


def write_manifest(run_dir: Path, manifest: RunManifest) -> None:
    """Write the immutable manifest for a run of the given class."""
    final_dir = run_dir / "finalized"
    if final_dir.exists():
        raise ValueError("run already finalized; cannot write manifest")
    write_json_write_once(run_dir / "manifest.json", manifest)


def regenerate_derived(run_dir: Path, content: dict[str, Any], name: str) -> None:
    """Regenerate a derived file (allowed only on a working, non-finalized run)."""
    if name not in DERIVED_FILES:
        raise ValueError(f"{name} is not a derived file")
    if _is_finalized(run_dir):
        raise ValueError("cannot mutate a finalized run")
    target = run_dir / name
    write_json_write_once(target, content)


def write_per_sample(run_dir: Path, filename: str, record: BaseModel) -> None:
    """Write a write-once per-sample artifact (allowed while working)."""
    if _is_finalized(run_dir):
        raise ValueError("cannot add files to a finalized run")
    write_json_write_once(run_dir / filename, record)


def _is_finalized(run_dir: Path) -> bool:
    return (run_dir / "finalized" / FINALIZED_FILENAME).exists()


def check_resume(run_dir: Path, manifest: RunManifest) -> None:
    """Refuse to resume a run whose manifest has drifted."""
    existing = require_manifest(run_dir)
    if existing.manifest_hash() != manifest.manifest_hash():
        raise ValueError("resume refused: manifest drift")


def finalize_run(
    run_dir: Path,
    manifest: RunManifest,
    *,
    completion_counts: dict[str, int] | None = None,
) -> FinalizationRecord:
    """Seal a run with a write-once FINALIZED.json.

    Refuses: dirty publication runs, incomplete publication runs, hash drift,
    and any overwrite of an already-finalized run. Produces an immutable record
    containing a hash for every required artifact.
    """
    validate_manifest_ids(manifest)
    _validate_publication(run_dir, manifest)

    finalized_dir = run_dir / "finalized"
    finalized_path = finalized_dir / FINALIZED_FILENAME
    if finalized_path.exists():
        raise FileExistsError(f"run already finalized: {finalized_path}")

    required_paths = required_file_paths(run_dir, manifest.artifact_class)
    missing = [path for path in required_paths if not path.exists()]
    if missing:
        raise FileNotFoundError(f"missing required artifacts: {missing}")

    record = FinalizationRecord(
        artifact_class=manifest.artifact_class,
        manifest_hash=manifest.manifest_hash(),
        required_hashes={
            path.name: required_hash(path) for path in required_paths
        },
        completion_counts=completion_counts or {},
    )

    finalized_dir.mkdir(parents=True, exist_ok=True)
    write_json_write_once(finalized_path, record)
    return record


def load_finalized(run_dir: Path) -> FinalizationRecord:
    """Load and validate a run's FINALIZED.json, refusing mutation or drift."""
    finalized_path = run_dir / "finalized" / FINALIZED_FILENAME
    if not finalized_path.exists():
        raise FileNotFoundError(f"not finalized: {run_dir}")
    record = FinalizationRecord.model_validate_json(finalized_path.read_text())
    _validate_required_files(run_dir, record)
    for path in required_file_paths(run_dir, record.artifact_class):
        if required_hash(path) != record.required_hashes[path.name]:
            raise ValueError(f"hash drift on {path.name}")
    return record
