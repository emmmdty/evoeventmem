"""Dataset-neutral loaders over finalized B-ARTIFACT source runs (C2).

Consumes Workstream B's frozen producer schemas from
``benchmarks.common.artifacts`` (``RunManifest``, ``AblationRunManifest``,
``FinalizationRecord``, ...) and normalizes LongMemEval and LoCoMo finalized
runs into one per-question :class:`~benchmarks.analysis.models.AnalysisRow`
schema.

Rules enforced here (structural level):

- a source run must be a finalized publication run on a full, clean scope with
  a known schema; missing or hash-drifted finalization, dirty/diagnostic/
  subset input, legacy ``runs/*/report`` trees, missing derived artifacts,
  missing model caches, missing/duplicate IDs, dataset hash drift, and
  ``session_summary`` injected into LongMemEval are all rejected;
- LongMemEval and LoCoMo may use different resolved model stacks and method
  sets; the loaders never require the two datasets to be identical, never
  inject ``session_summary`` into LongMemEval, and never write below a source
  run directory.

Gold answers and dataset-native categories come from the dataset file declared
by the manifest; its on-disk hash must match ``manifest.dataset_hash``.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from benchmarks.analysis.models import AnalysisRow
from benchmarks.common.artifacts import (
    CONTRACT_SCHEMA_VERSION,
    FINALIZATION_FORMAT,
    AblationRunManifest,
    ArtifactClass,
    ConsolidationAction,
    ConsolidationRecord,
    EvidenceRecord,
    ExtractionSnapshot,
    FinalizationRecord,
    RunManifest,
    SourceFailure,
    load_finalized,
    require_manifest,
)
from benchmarks.common.normalization import (
    iter_locomo_records,
    iter_longmemeval_records,
)

KNOWN_DATASETS = ("longmemeval", "locomo")
CONTEXT_METHODS = frozenset({"no_memory", "full_context", "session_summary"})

LOCOMO_CATEGORY_BY_ID: dict[str, str] = {
    "1": "single-hop",
    "2": "multi-hop-reasoning",
    "3": "temporal-reasoning",
    "4": "open-domain-knowledge",
    "5": "adversarial",
}


class LoadError(RuntimeError):
    """Structural failure with a stable machine-readable ``code``."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class GoldQuestion:
    question_id: str
    answer: str | None
    category: str


@dataclass(frozen=True)
class LoadedRun:
    """A finalized, validated source run with normalized per-question rows."""

    run_dir: Path
    manifest: RunManifest
    finalization: FinalizationRecord
    rows: tuple[AnalysisRow, ...]
    gold: Mapping[str, GoldQuestion]

    @property
    def dataset(self) -> str:
        return self.manifest.dataset

    @property
    def run_id(self) -> str:
        return self.manifest.run_id


@dataclass(frozen=True)
class LoadedAblationArm:
    """One finalized ablation arm: manifest + raw retrieval payload rows."""

    run_dir: Path
    manifest: AblationRunManifest
    finalization: FinalizationRecord
    rows: tuple[dict[str, Any], ...]

    @property
    def name(self) -> str:
        return self.manifest.run_id

    @property
    def factor(self) -> str:
        return self.manifest.changed_factors[0]


@dataclass(frozen=True)
class LoadedAblationRun:
    """A finalized ablation family: family manifest + one arm per method."""

    run_dir: Path
    manifest: RunManifest
    finalization: FinalizationRecord
    arms: Mapping[str, LoadedAblationArm]
    deltas: dict[str, Any] = field(default_factory=dict)

    @property
    def dataset(self) -> str:
        return self.manifest.dataset

    def arm(self, name: str) -> LoadedAblationArm:
        return self.arms[name]


def is_legacy_report_dir(run_dir: Path) -> bool:
    """True for legacy ``runs/*/report`` trees or old summary/config run trees.

    Legacy report output is a historical diagnostic, never a valid analysis
    input.
    """
    if run_dir.name == "report" and "runs" in run_dir.parts:
        return True
    if run_dir.name == "report" and (run_dir / "report.md").is_file():
        return True
    return (
        (run_dir / "summary.json").is_file()
        and (run_dir / "config.json").is_file()
        and not (run_dir / "manifest.json").is_file()
    )


def normalize_category(dataset: str, raw: str | None) -> str:
    """Dataset-native category names; LoCoMo numeric IDs map to official names."""
    if dataset == "locomo":
        mapped = LOCOMO_CATEGORY_BY_ID.get(str(raw))
        return mapped if mapped is not None else (str(raw) if raw else "unmapped")
    return str(raw) if raw else "unmapped"


def _find_repo_root(run_dir: Path) -> Path | None:
    """Walk up from a run directory to the repository root (``pyproject.toml``)."""
    for directory in Path(run_dir).resolve().parents:
        if (directory / "pyproject.toml").is_file():
            return directory
    return None


def resolve_dataset_path(run_dir: Path, manifest: RunManifest) -> Path:
    candidate = Path(manifest.dataset_path)
    if candidate.is_absolute():
        return candidate
    run_relative = run_dir / candidate
    if run_relative.is_file():
        return run_relative
    repo_root = _find_repo_root(run_dir)
    if repo_root is not None:
        root_relative = repo_root / candidate
        if root_relative.is_file():
            return root_relative
    return run_relative


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1 << 20)
            if not chunk:
                break
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def build_gold_mapping(run_dir: Path, manifest: RunManifest) -> dict[str, GoldQuestion]:
    """Load gold answers and dataset-native categories from the declared dataset.

    Raises :class:`LoadError` on a missing dataset file, a dataset hash drift,
    or an unparseable dataset.
    """
    dataset_path = resolve_dataset_path(run_dir, manifest)
    if not dataset_path.is_file():
        raise LoadError(
            "missing_dataset",
            f"dataset file declared by the manifest does not exist: {dataset_path}",
        )
    if file_sha256(dataset_path) != manifest.dataset_hash:
        raise LoadError(
            "dataset_drift",
            f"dataset hash mismatch for {dataset_path}: manifest declares "
            f"{manifest.dataset_hash}, on-disk file hashes differently",
        )
    if manifest.dataset not in KNOWN_DATASETS:
        return {}
    if manifest.dataset == "locomo":
        records = iter_locomo_records(dataset_path)
    else:
        records = iter_longmemeval_records(dataset_path)
    gold: dict[str, GoldQuestion] = {}
    try:
        for record in records:
            for question in record.questions:
                gold[question.question_id] = GoldQuestion(
                    question_id=question.question_id,
                    answer=question.answer,
                    category=normalize_category(manifest.dataset, question.category),
                )
    except ValueError as exc:
        raise LoadError("invalid_dataset", f"dataset normalization failed: {exc}") from exc
    return gold


def _parse_manifest(run_dir: Path) -> RunManifest:
    if not (run_dir / "manifest.json").is_file():
        raise LoadError("missing_manifest", f"no manifest.json in {run_dir}")
    try:
        manifest = require_manifest(run_dir)
    except ValidationError as exc:
        raise LoadError("invalid_manifest", f"manifest schema validation failed: {exc}") from exc
    if manifest.schema_version != CONTRACT_SCHEMA_VERSION:
        raise LoadError(
            "unknown_schema",
            f"manifest schema_version is {manifest.schema_version}, expected "
            f"{CONTRACT_SCHEMA_VERSION}",
        )
    return manifest


def _parse_finalization(run_dir: Path) -> FinalizationRecord:
    try:
        finalization = load_finalized(run_dir)
    except FileNotFoundError as exc:
        message = str(exc)
        if "missing required artifact" in message:
            raise LoadError("missing_required_artifact", message) from exc
        raise LoadError("missing_finalization", message) from exc
    except ValueError as exc:
        raise LoadError("finalization_hash_drift", str(exc)) from exc
    if finalization.format_version != FINALIZATION_FORMAT:
        raise LoadError(
            "unknown_schema",
            f"finalization format_version is {finalization.format_version}, "
            f"expected {FINALIZATION_FORMAT}",
        )
    return finalization


def _read_jsonl(path: Path, *, code: str) -> list[dict[str, Any]]:
    if not path.is_file():
        raise LoadError(code, f"missing artifact: {path}")
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise LoadError(code, f"malformed JSON line {line_number} in {path}: {exc}") from exc
    return rows


def _validate_publication_support_files(run_dir: Path, manifest: RunManifest) -> None:
    if not (run_dir / "model_cache").is_dir():
        raise LoadError("missing_model_cache", f"publication run has no model_cache: {run_dir}")
    snapshot_path = run_dir / "extraction_snapshot.json"
    try:
        payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise LoadError("invalid_snapshot", f"unreadable extraction_snapshot.json: {exc}") from exc
    if not isinstance(payload, list):
        raise LoadError("invalid_snapshot", "extraction_snapshot.json must be a JSON array")
    for index, item in enumerate(payload):
        try:
            snapshot = ExtractionSnapshot.model_validate(item)
        except ValidationError as exc:
            raise LoadError(
                "invalid_snapshot", f"snapshot {index} failed validation: {exc}"
            ) from exc
        if snapshot.event_count <= 0:
            raise LoadError(
                "empty_extraction_snapshot",
                f"snapshot {snapshot.snapshot_id} carries no events",
            )
    for path, model, code in (
        (run_dir / "evidence.jsonl", EvidenceRecord, "invalid_evidence_row"),
        (run_dir / "consolidation.jsonl", ConsolidationRecord, "invalid_consolidation_row"),
    ):
        for row in _read_jsonl(path, code="missing_required_artifact"):
            try:
                model.model_validate(row)
            except ValidationError as exc:
                raise LoadError(code, f"row in {path} failed validation: {exc}") from exc
    _read_jsonl(run_dir / "retrieval.jsonl", code="missing_required_artifact")


def _extraction_rejections(run_dir: Path) -> dict[str, list[str]]:
    """Map conversation/sample ID to the extraction rejection reasons recorded
    in the run's immutable extraction snapshot (normalized trace data)."""
    snapshot_path = run_dir / "extraction_snapshot.json"
    if not snapshot_path.is_file():
        return {}
    rejections: dict[str, list[str]] = {}
    try:
        payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, list):
        return {}
    for item in payload:
        try:
            snapshot = ExtractionSnapshot.model_validate(item)
        except ValidationError:
            continue
        rejections[snapshot.conversation_id] = [
            rejection.reason for rejection in snapshot.rejections
        ]
    return rejections


def _consolidation_actions(run_dir: Path) -> dict[str, list[ConsolidationAction]]:
    actions: dict[str, list[ConsolidationAction]] = {}
    path = run_dir / "consolidation.jsonl"
    if not path.is_file():
        return actions
    for row in _read_jsonl(path, code="missing_required_artifact"):
        try:
            record = ConsolidationRecord.model_validate(row)
        except ValidationError:
            continue
        actions.setdefault(record.sample_id, []).append(record.action)
    return actions


def _build_rows(
    run_dir: Path,
    manifest: RunManifest,
    gold: Mapping[str, GoldQuestion],
) -> list[AnalysisRow]:
    methods = manifest.methods
    if manifest.dataset == "longmemeval" and "session_summary" in methods:
        raise LoadError(
            "injected_session_summary",
            "session_summary is a LoCoMo-only method and must never appear in a LongMemEval run",
        )
    sample_actions = _consolidation_actions(run_dir)
    rejection_reasons = _extraction_rejections(run_dir)
    rows: list[AnalysisRow] = []
    for method in methods:
        method_dir = run_dir / method
        if not method_dir.is_dir():
            raise LoadError(
                "missing_derived_artifact",
                f"method directory {method} is missing in {run_dir}",
            )
        predictions = _read_jsonl(method_dir / "predictions.jsonl", code="missing_derived_artifact")
        samples = _read_jsonl(method_dir / "samples.jsonl", code="missing_derived_artifact")
        for label, records in (("predictions", predictions), ("samples", samples)):
            seen = [record["question_id"] for record in records if "question_id" in record]
            duplicates = [item for item, count in Counter(seen).items() if count > 1]
            if duplicates:
                raise LoadError(
                    "duplicate_question_ids",
                    f"method {method} {label} contains duplicate question IDs: {duplicates}",
                )
        samples_by_id = {record["question_id"]: record for record in samples}
        predictions_by_id = {record["question_id"]: record for record in predictions}
        is_memory = method not in CONTEXT_METHODS
        retrieval_by_id: dict[str, dict[str, Any]] = {}
        if is_memory:
            retrieval_rows = _read_jsonl(
                method_dir / "retrieval.jsonl", code="missing_derived_artifact"
            )
            if not retrieval_rows:
                raise LoadError(
                    "missing_derived_artifact",
                    f"memory method {method} has an empty retrieval.jsonl",
                )
            retrieval_by_id = {record["question_id"]: record for record in retrieval_rows}
        for question_id in manifest.expected_question_ids:
            sample = samples_by_id.get(question_id)
            if sample is None:
                raise LoadError(
                    "missing_question_ids",
                    f"method {method} is missing question {question_id} in samples.jsonl",
                )
            prediction = predictions_by_id.get(question_id) or {}
            retrieval = retrieval_by_id.get(question_id)
            metadata = prediction.get("metadata") or {}
            gold_question = gold.get(question_id)
            category = (
                metadata.get("category")
                or (gold_question.category if gold_question is not None else None)
                or "unmapped"
            )
            if retrieval is not None:
                content_tokens = int(retrieval.get("content_tokens") or 0)
                prompt_overhead_tokens = int(retrieval.get("prompt_overhead_tokens") or 0)
                estimate = retrieval.get("total_input_tokens_estimate")
                total_input_tokens = (
                    int(estimate) if estimate is not None else int(sample.get("input_tokens") or 0)
                )
                packed_items = retrieval.get("packed_items") or []
                packed_item_count = len(packed_items)
                packing_bound = bool(retrieval.get("packing_bound", False))
                context_text = " ".join(
                    str(item.get("content") or "") for item in packed_items
                )
                intent = retrieval.get("intent")
                candidate_count = retrieval.get("candidate_count")
                exclusion_reasons = [
                    str(item.get("reason") or "")
                    for item in retrieval.get("exclusions") or []
                    if item.get("reason")
                ]
                try:
                    failures = [
                        SourceFailure.model_validate(failure)
                        for failure in retrieval.get("source_failures") or []
                    ]
                except ValidationError as exc:
                    raise LoadError(
                        "invalid_retrieval_row",
                        f"invalid source_failures for {question_id}: {exc}",
                    ) from exc
            else:
                content_tokens = 0
                prompt_overhead_tokens = 0
                total_input_tokens = int(sample.get("input_tokens") or 0)
                packed_item_count = 0
                packing_bound = False
                failures = []
                context_text = ""
                intent = None
                candidate_count = None
                exclusion_reasons = []
            rows.append(
                AnalysisRow(
                    dataset=manifest.dataset,
                    sample_id=str(sample.get("sample_id") or ""),
                    question_id=question_id,
                    run_id=manifest.run_id,
                    method=method,
                    category=category,
                    prediction=str(prediction.get("prediction") or ""),
                    gold_answer=gold_question.answer if gold_question is not None else None,
                    exact_match=float(sample.get("exact_match", 0.0)),
                    token_f1=float(sample.get("token_f1", 0.0)),
                    evidence_precision=float(sample.get("evidence_precision", 0.0)),
                    evidence_recall=float(sample.get("evidence_recall", 0.0)),
                    evidence_f1=float(sample.get("evidence_f1", 0.0)),
                    content_tokens=content_tokens,
                    prompt_overhead_tokens=prompt_overhead_tokens,
                    total_input_tokens=total_input_tokens,
                    packing_bound=packing_bound,
                    source_failures=failures,
                    packed_item_count=packed_item_count,
                    context_text=context_text,
                    intent=intent,
                    candidate_count=candidate_count,
                    exclusion_reasons=exclusion_reasons,
                    extraction_rejection_reasons=rejection_reasons.get(
                        str(sample.get("sample_id") or ""), []
                    ),
                    consolidation_actions=sample_actions.get(
                        str(sample.get("sample_id") or ""), []
                    ),
                    reader_model=manifest.reader.model_id,
                    extractor_model=manifest.extractor.model_id,
                    embedding_model=manifest.embedding.model_id,
                    tokenizer=manifest.tokenizer.name,
                    policy_versions=manifest.policies,
                    config_hash=manifest.config_hash,
                    git_commit=manifest.git.commit,
                    manifest_hash=manifest.manifest_hash(),
                    predictions_path=str(method_dir / "predictions.jsonl"),
                    samples_path=str(method_dir / "samples.jsonl"),
                )
            )
    return rows


def _validate_id_sets(run_dir: Path, manifest: RunManifest, rows: Sequence[AnalysisRow]) -> None:
    if not manifest.expected_sample_ids or not manifest.expected_question_ids:
        raise LoadError(
            "missing_expected_ids",
            f"manifest {run_dir} declares no expected sample/question IDs",
        )
    for label, ids in (
        ("sample", manifest.expected_sample_ids),
        ("question", manifest.expected_question_ids),
    ):
        duplicates = [item for item, count in Counter(ids).items() if count > 1]
        if duplicates:
            raise LoadError("duplicate_ids", f"duplicate expected {label} IDs: {duplicates}")
    actual_questions = {row.question_id for row in rows}
    missing_questions = sorted(set(manifest.expected_question_ids) - actual_questions)
    if missing_questions:
        raise LoadError(
            "missing_question_ids",
            f"missing expected question IDs in {run_dir}: {missing_questions}",
        )
    unexpected = sorted(actual_questions - set(manifest.expected_question_ids))
    if unexpected:
        raise LoadError(
            "unexpected_question_ids",
            f"rows contain question IDs absent from the manifest: {unexpected}",
        )
    actual_samples = {row.sample_id for row in rows}
    missing_samples = sorted(set(manifest.expected_sample_ids) - actual_samples)
    if missing_samples:
        raise LoadError(
            "missing_sample_ids",
            f"missing expected sample IDs in {run_dir}: {missing_samples}",
        )


def load_base_run(run_dir: Path) -> LoadedRun:
    """Load and structurally validate one finalized publication source run."""
    run_dir = Path(run_dir)
    if not run_dir.is_dir():
        raise LoadError("missing_run_dir", f"run directory does not exist: {run_dir}")
    if is_legacy_report_dir(run_dir):
        raise LoadError(
            "legacy_report_input",
            f"{run_dir} is a legacy runs/*/report tree and cannot be an analysis input",
        )
    manifest = _parse_manifest(run_dir)
    finalization = _parse_finalization(run_dir)
    if finalization.manifest_hash != manifest.manifest_hash():
        raise LoadError(
            "manifest_hash_mismatch",
            "FINALIZED.json manifest hash does not match the on-disk manifest",
        )
    if manifest.artifact_class is not ArtifactClass.PUBLICATION:
        raise LoadError(
            "non_publication_class",
            f"source run artifact_class is {manifest.artifact_class.value}; "
            "publication claims require publication-class runs",
        )
    if manifest.git.dirty:
        raise LoadError(
            "dirty_publication_run",
            "publication source run was executed on a dirty Git tree",
        )
    if manifest.scope != "full":
        raise LoadError("subset_scope", f"source run scope is {manifest.scope!r}, not 'full'")
    _validate_publication_support_files(run_dir, manifest)
    gold = build_gold_mapping(run_dir, manifest)
    rows = _build_rows(run_dir, manifest, gold)
    _validate_id_sets(run_dir, manifest, rows)
    return LoadedRun(
        run_dir=run_dir,
        manifest=manifest,
        finalization=finalization,
        rows=tuple(rows),
        gold=gold,
    )


def load_ablation_run(run_dir: Path) -> LoadedAblationRun:
    """Load and structurally validate one finalized ablation family."""
    run_dir = Path(run_dir)
    if not run_dir.is_dir():
        raise LoadError("missing_run_dir", f"run directory does not exist: {run_dir}")
    if is_legacy_report_dir(run_dir):
        raise LoadError("legacy_report_input", f"{run_dir} is a legacy report tree")
    manifest = _parse_manifest(run_dir)
    finalization = _parse_finalization(run_dir)
    if finalization.manifest_hash != manifest.manifest_hash():
        raise LoadError(
            "manifest_hash_mismatch",
            "FINALIZED.json manifest hash does not match the on-disk manifest",
        )
    arms: dict[str, LoadedAblationArm] = {}
    for arm_name in manifest.methods:
        arm_dir = run_dir / arm_name
        if not (arm_dir / "manifest.json").is_file():
            raise LoadError(
                "missing_arm_manifest",
                f"ablation family {run_dir} is missing arm {arm_name}",
            )
        try:
            arm_manifest = AblationRunManifest.model_validate_json(
                (arm_dir / "manifest.json").read_text(encoding="utf-8")
            )
        except ValidationError as exc:
            raise LoadError("invalid_ablation", f"arm {arm_name} manifest invalid: {exc}") from exc
        if arm_manifest.schema_version != CONTRACT_SCHEMA_VERSION:
            raise LoadError(
                "unknown_schema",
                f"arm {arm_name} schema_version is {arm_manifest.schema_version}",
            )
        arm_finalization = _parse_finalization(arm_dir)
        if arm_finalization.manifest_hash != arm_manifest.manifest_hash():
            raise LoadError(
                "manifest_hash_mismatch",
                f"arm {arm_name} FINALIZED.json manifest hash mismatch",
            )
        rows = _read_jsonl(arm_dir / "retrieval.jsonl", code="missing_derived_artifact")
        if not rows:
            raise LoadError(
                "missing_derived_artifact",
                f"arm {arm_name} has an empty retrieval.jsonl",
            )
        arms[arm_name] = LoadedAblationArm(
            run_dir=arm_dir,
            manifest=arm_manifest,
            finalization=arm_finalization,
            rows=tuple(rows),
        )
    deltas: dict[str, Any] = {}
    deltas_path = run_dir / "deltas.json"
    if deltas_path.is_file():
        try:
            deltas = json.loads(deltas_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            deltas = {}
    return LoadedAblationRun(
        run_dir=run_dir,
        manifest=manifest,
        finalization=finalization,
        arms=arms,
        deltas=deltas,
    )
