"""Execute A-owned retrieval controls as paired ablation runs.

Workstream B executes Workstream A's public ``RetrievalControls`` (strategy,
routing mode / forced intent, weight profile, evidence policy, temporal/graph
source switches, token budget, reference time) against a declared store and
emits paired, finalized raw artifacts. B does NOT compute claims, bootstrap
statistics, or failure taxonomy (that is Workstream C).

Factor isolation: every arm differs from the base arm in exactly ONE declared
factor (verified from the resolved controls). The controlled fixture run
requires at least one decision delta per required factor (evidence, temporal,
graph, router, weights, budget); a non-active switch fails the controlled run
(Gate D). Dataset executors record deltas but never substitute them for the
controlled proof.

Binding budget: item caps are high enough that the token budget becomes the
limiting factor; question-level ``packing_bound`` is recorded from observable
``budget_exceeded`` exclusions, never inferred from item count.

Controlled run requirement: dataset executors require ``--controlled-run``;
every dataset ``AblationRunManifest`` embeds the controlled run's
``FINALIZED.json`` hash and refuses a missing, inactive, or hash-drifted
controlled run. ``base_run_dir`` embeds the finalized dataset base run hash.

Stores: the controlled run builds its ETEC store from the deterministic fake
extractor and deterministic fake embeddings. Dataset executors reuse the base
run's immutable extraction snapshot and its model cache in strict offline mode:
any embedding lookup not already cached raises instead of contacting a network
endpoint. No reader calls are ever made.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tomllib
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from benchmarks.common.artifacts import (
    AblationRunManifest,
    ArtifactClass,
    BudgetSpec,
    EvidenceRecord,
    ExtractionSnapshot,
    GitState,
    PolicyVersions,
    RunManifest,
    TokenizerIdentity,
    check_resume,
    current_git_commit,
    finalize_run,
    load_finalized,
    require_manifest,
    write_json_write_once,
    write_jsonl_write_once,
    write_manifest,
)
from benchmarks.common.memory_inputs import (
    FakeEventExtractor,
    extract_event_snapshot,
    materialize_event_store,
    provider_identity,
)
from benchmarks.common.normalization import (
    NormalizedRecord,
    iter_locomo_records,
    iter_longmemeval_records,
)
from benchmarks.common.providers import (
    ProviderKind,
    ResolvedModelConfig,
)
from evoeventmem.core.ports import EmbeddingModel, EmbeddingResponse
from evoeventmem.domain.models import EntityRef, MemoryKind
from evoeventmem.extraction import ExtractionInput, ExtractionResult
from evoeventmem.models.cache import CachedEmbeddingModel, FileModelCache
from evoeventmem.models.fakes import DeterministicFakeEmbeddingModel
from evoeventmem.retrieval import (
    POLICY_NAME,
    QEMRRetrievalResult,
    RetrievalControls,
    RetrievalHarness,
)
from evoeventmem.router import POLICY_NAME as ROUTER_POLICY_NAME
from evoeventmem.tokenization import DEFAULT_TOKEN_ESTIMATOR

ABLATION_SCHEMA_VERSION = "ablation.config.v1"
ABLATION_ARTIFACT_SCHEMA = "ablation.arm.v1"
REQUIRED_FACTORS = (
    "evidence_policy",
    "temporal_source",
    "graph_source",
    "routing",
    "weights",
    "budget",
)
FACTOR_FIELDS: dict[str, tuple[str, ...]] = {
    "evidence_policy": ("evidence_policy",),
    "temporal_source": ("enable_temporal_source",),
    "graph_source": ("enable_graph_source",),
    "routing": ("routing_mode", "forced_intent"),
    "weights": ("weight_profile",),
    "budget": ("budget_tokens",),
}



class OfflineCacheMiss(RuntimeError):
    """Raised when an embedding lookup is not covered by the base run's cache."""


class _OfflineOnlyEmbedding(EmbeddingModel):
    def __init__(self, model_id: str) -> None:
        self.model_id = model_id

    def embed_texts(self, texts: Sequence[str]) -> list[EmbeddingResponse]:
        raise OfflineCacheMiss(f"offline cache miss for model {self.model_id}: {texts[0]!r}")


class ControlledFixtureExtractor(FakeEventExtractor):
    """Deterministic fake extractor for the controlled fixture.

    Turns marked with the ``PARAPHRASE:`` prefix additionally emit one
    paraphrase event referencing the SAME raw-turn evidence span. The shared
    evidence lets the evidence-policy packing bonus change real selection and
    ranking decisions on the controlled fixture (Gate D).
    """

    PARAPHRASE_PREFIX = "PARAPHRASE: "

    def extract(self, request: ExtractionInput) -> ExtractionResult:
        from evoeventmem.domain.models import MemoryRecord
        from evoeventmem.extraction import ExtractedEventCandidate, _turn_evidence

        result = super().extract(request)
        candidates = list(result.candidates)
        for turn in request.turns:
            if not turn.content.startswith(self.PARAPHRASE_PREFIX):
                continue
            content = turn.content.removeprefix(self.PARAPHRASE_PREFIX)
            candidates.append(
                ExtractedEventCandidate(
                    memory=MemoryRecord(
                        user_id=request.user_id,
                        session_id=turn.session_id,
                        memory_kind=MemoryKind.EVENT,
                        content=f"Paraphrased: {content}",
                        entities=[EntityRef(name=turn.speaker, role="speaker")],
                        evidence_refs=[_turn_evidence(request, turn)],
                        event_time=turn.timestamp,
                    ),
                    prompt_version=self.PROMPT_VERSION,
                )
            )
        return ExtractionResult(prompt_version=self.PROMPT_VERSION, candidates=candidates)


class AblationArmConfig(BaseModel):
    name: str = Field(min_length=1)
    factor: str = Field(min_length=1)
    controls: RetrievalControls


class AblationConfig(BaseModel):
    schema_version: Literal["ablation.config.v1"] = ABLATION_SCHEMA_VERSION
    ablation_id: str = Field(min_length=1)
    dataset: Literal["controlled", "longmemeval", "locomo"]
    dataset_path: Path
    expected_retrieval_policy: str = Field(min_length=1)
    max_items_per_source: int = Field(ge=1)
    max_candidates_per_source: int = Field(ge=1)
    sample_limit: int | None = Field(default=None, ge=1)
    base_run_dir: Path | None = None
    base: AblationArmConfig
    arms: list[AblationArmConfig] = Field(default_factory=list)


class ArmSummary(BaseModel):
    schema_version: Literal["ablation.arm.v1"] = ABLATION_ARTIFACT_SCHEMA
    run_id: str = Field(min_length=1)
    arm: str = Field(min_length=1)
    factor: str = Field(min_length=1)
    artifact_class: ArtifactClass
    question_count: int = Field(ge=0)
    packing_bound_questions: int = Field(ge=0)
    delta_question_count: int | None = None
    manifest_hash: str = Field(min_length=1)
    finalization_hash: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AblationFamilySummary(BaseModel):
    schema_version: Literal["ablation.family.v1"] = "ablation.family.v1"
    run_id: str = Field(min_length=1)
    ablation_id: str = Field(min_length=1)
    dataset: str = Field(min_length=1)
    config_hash: str = Field(min_length=1)
    git_commit: str = Field(min_length=1)
    git_dirty: bool
    expected_retrieval_policy: str = Field(min_length=1)
    required_factors: list[str] = Field(default_factory=list)
    controlled_run_hash: str = Field(min_length=1)
    base_run_hash: str = Field(min_length=1)
    arms: dict[str, ArmSummary] = Field(default_factory=dict)


def load_config(path: Path) -> AblationConfig:
    payload = tomllib.loads(path.read_text(encoding="utf-8"))
    return AblationConfig.model_validate(payload)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Execute declared retrieval-control ablations.")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, default=None)
    parser.add_argument("--controlled-run", type=Path, default=None)
    parser.add_argument("--validate-config", action="store_true")
    args = parser.parse_args(argv)

    config = load_config(args.config)
    if args.validate_config:
        print(json.dumps(validate_config(config, args.config), indent=2, sort_keys=True))
        return 0

    if config.dataset != "controlled" and args.controlled_run is None:
        raise ValueError(
            "dataset ablation executors require --controlled-run pointing at the "
            "finalized controlled run directory"
        )
    if config.dataset == "controlled" and args.controlled_run is not None:
        raise ValueError("the controlled config does not take --controlled-run")
    if args.run_dir is None:
        raise ValueError("--run-dir is required for ablation execution")

    summary = run_ablation(config, args.run_dir, controlled_run_dir=args.controlled_run)
    print(json.dumps(summary.model_dump(mode="json"), indent=2, sort_keys=True))
    return 0


def validate_config(config: AblationConfig, config_path: Path) -> dict[str, Any]:
    """Static validation: schema, factor matrix, A policy version, controlled-run gate."""
    if config.expected_retrieval_policy != POLICY_NAME:
        raise ValueError(
            f"config declares retrieval policy {config.expected_retrieval_policy!r} "
            f"but Workstream A froze {POLICY_NAME!r}"
        )
    factors = [arm.factor for arm in config.arms]
    missing = [factor for factor in REQUIRED_FACTORS if factor not in factors]
    if missing:
        raise ValueError(f"ablation factor matrix is missing required factors: {missing}")
    _validate_factor_isolation(config)
    if config.dataset != "controlled" and config.base_run_dir is None:
        raise ValueError(f"dataset executor {config.dataset} must declare base_run_dir")
    return {
        "config_path": str(config_path),
        "config_hash": _hash_json(config.model_dump(mode="json")),
        "ablation_id": config.ablation_id,
        "dataset": config.dataset,
        "expected_retrieval_policy": POLICY_NAME,
        "required_factors": list(REQUIRED_FACTORS),
        "declared_factors": factors,
        "controlled_run_required": config.dataset != "controlled",
        "base_run_dir": str(config.base_run_dir) if config.base_run_dir else None,
        "git_commit": current_git_commit(),
    }


def _validate_factor_isolation(config: AblationConfig) -> None:
    """Every arm must differ from the base arm in exactly the declared factor."""
    base_controls = config.base.controls.model_dump()
    for arm in config.arms:
        if arm.factor == "base":
            raise ValueError("comparison arms must not declare factor 'base'")
        if arm.factor not in FACTOR_FIELDS:
            raise ValueError(f"unknown ablation factor: {arm.factor}")
        arm_controls = arm.controls.model_dump()
        differing = {
            field
            for field, value in arm_controls.items()
            if value != base_controls[field]
        }
        allowed = set(FACTOR_FIELDS[arm.factor])
        unexpected = differing - allowed
        if unexpected:
            raise ValueError(
                f"arm {arm.name} changes fields {sorted(unexpected)} outside "
                f"factor {arm.factor!r}"
            )
        if not differing:
            raise ValueError(
                f"arm {arm.name} does not change factor {arm.factor!r} at all"
            )


def run_ablation(
    config: AblationConfig,
    run_dir: Path,
    *,
    controlled_run_dir: Path | None = None,
) -> AblationFamilySummary:
    """Execute every arm and finalize the family.

    ``--run-dir`` is the stable family directory; each arm lives in a
    subdirectory named after the arm. Identical resumes address the same
    directories; finalized arms are never mutated.
    """
    if config.expected_retrieval_policy != POLICY_NAME:
        raise ValueError(
            f"config declares retrieval policy {config.expected_retrieval_policy!r} "
            f"but Workstream A froze {POLICY_NAME!r}"
        )
    _validate_factor_isolation(config)
    run_dir.mkdir(parents=True, exist_ok=True)
    records = _load_records(config)
    expected_sample_ids = [record.sample_id for record in records]
    expected_question_ids = [
        question.question_id for record in records for question in record.questions
    ]

    if config.dataset == "controlled":
        controlled_hash, identities, base_run_manifest, embedding = _prepare_controlled(
            config,
            run_dir,
            expected_sample_ids=expected_sample_ids,
            expected_question_ids=expected_question_ids,
        )
    else:
        if controlled_run_dir is None:
            raise ValueError("dataset ablation executors require --controlled-run")
        controlled_hash, identities, base_run_manifest, embedding = _prepare_dataset(
            config, controlled_run_dir
        )

    base_run_hash = (
        controlled_hash
        if config.dataset == "controlled"
        else _finalization_hash_of(_require_dir(config.base_run_dir))
    )

    stores = _build_stores(config, records, identities, base_run_manifest)

    arm_summaries: dict[str, ArmSummary] = {}
    for arm in [config.base, *config.arms]:
        arm_dir = run_dir / arm.name
        arm_dir.mkdir(parents=True, exist_ok=True)
        manifest = _arm_manifest(
            config,
            arm,
            arm_dir,
            identities,
            base_run_manifest,
            expected_sample_ids=expected_sample_ids,
            expected_question_ids=expected_question_ids,
            controlled_run_hash=controlled_hash,
            base_run_hash=base_run_hash,
        )
        if _is_finalized(arm_dir):
            check_resume(arm_dir, manifest)
            summary = _load_arm_summary(arm_dir)
            summary = summary.model_copy(
                update={"finalization_hash": _finalization_hash_of(arm_dir)}
            )
            arm_summaries[arm.name] = summary
            continue
        summary = _run_arm(
            config,
            arm,
            arm_dir,
            manifest,
            stores,
            records,
            embedding,
        )
        summary = summary.model_copy(
            update={"finalization_hash": _finalization_hash_of(arm_dir)}
        )
        arm_summaries[arm.name] = summary

    base_arm_dir = run_dir / config.base.name
    deltas = {
        arm.name: _arm_deltas(arm, run_dir, base_arm_dir)
        for arm in config.arms
    }
    _write_deltas(run_dir, config, deltas)
    if config.dataset == "controlled":
        _require_controlled_deltas(config, deltas)

    if _is_finalized(run_dir):
        check_resume(
            run_dir,
            _family_manifest(
                config,
                run_dir,
                expected_sample_ids=expected_sample_ids,
                expected_question_ids=expected_question_ids,
            ),
        )
    else:
        _write_run_root_artifacts(run_dir, arm_summaries)
        family_manifest = _family_manifest(
            config,
            run_dir,
            expected_sample_ids=expected_sample_ids,
            expected_question_ids=expected_question_ids,
        )
        if not (run_dir / "manifest.json").exists():
            write_manifest(run_dir, family_manifest)
        else:
            check_resume(run_dir, family_manifest)
        finalize_run(
            run_dir,
            family_manifest,
            completion_counts={"arms": len([config.base, *config.arms])},
        )

    family_summary = AblationFamilySummary(
        run_id=run_dir.name,
        ablation_id=config.ablation_id,
        dataset=config.dataset,
        config_hash=_hash_json(config.model_dump(mode="json")),
        git_commit=current_git_commit(),
        git_dirty=_git_is_dirty(),
        expected_retrieval_policy=POLICY_NAME,
        required_factors=list(REQUIRED_FACTORS),
        controlled_run_hash=controlled_hash,
        base_run_hash=base_run_hash,
        arms=arm_summaries,
    )
    summary_path = run_dir / "summary.json"
    if not _is_finalized(run_dir) or not summary_path.exists():
        summary_path.unlink(missing_ok=True)
        write_json_write_once(summary_path, family_summary)
    return family_summary


def _prepare_controlled(
    config: AblationConfig,
    run_dir: Path,
    *,
    expected_sample_ids: Sequence[str],
    expected_question_ids: Sequence[str],
) -> tuple[str, dict[str, ResolvedModelConfig], None, DeterministicFakeEmbeddingModel]:
    """Finalize the controlled family first so arms can embed its hash."""
    identities = {
        "reader": _fake_resolved("reader"),
        "extractor": _fake_resolved("extractor"),
        "embedding": _fake_resolved("embedding"),
    }
    family_manifest = _family_manifest(
        config,
        run_dir,
        expected_sample_ids=expected_sample_ids,
        expected_question_ids=expected_question_ids,
    )
    if not (run_dir / "manifest.json").exists():
        write_manifest(run_dir, family_manifest)
    else:
        check_resume(run_dir, family_manifest)
    if not _is_finalized(run_dir):
        finalize_run(run_dir, family_manifest, completion_counts={"arms": 1})
    return _finalization_hash_of(run_dir), identities, None, DeterministicFakeEmbeddingModel()


def _prepare_dataset(
    config: AblationConfig,
    controlled_run_dir: Path,
) -> tuple[str, dict[str, ResolvedModelConfig], RunManifest, EmbeddingModel]:
    """Validate the controlled and base runs; resolve identities from the base run."""
    load_finalized(controlled_run_dir)
    controlled_hash = _finalization_hash_of(controlled_run_dir)
    base_run_dir = _require_dir(config.base_run_dir)
    load_finalized(base_run_dir)
    base_manifest = require_manifest(base_run_dir)
    identities = {
        "reader": _resolved_from_provider(base_manifest.reader, "reader"),
        "extractor": _resolved_from_provider(base_manifest.extractor, "extractor"),
        "embedding": _resolved_from_provider(base_manifest.embedding, "embedding"),
    }
    cache_root = base_run_dir / "model_cache"
    if not cache_root.exists():
        raise FileNotFoundError(f"base run model cache missing: {cache_root}")
    embedding: EmbeddingModel = CachedEmbeddingModel(
        _OfflineOnlyEmbedding(base_manifest.embedding.model_id),
        FileModelCache(cache_root),
    )
    return controlled_hash, identities, base_manifest, embedding


def _resolved_from_provider(identity: Any, role: str) -> ResolvedModelConfig:
    try:
        kind = ProviderKind(identity.kind)
    except ValueError:
        kind = ProviderKind.DETERMINISTIC_FAKE
    api_key_env = _api_key_env_for(role)
    return ResolvedModelConfig(
        role=role,
        kind=kind,
        provider=identity.provider,
        model_id=identity.model_id,
        base_url=None if identity.endpoint == "n/a" else identity.endpoint,
        api_key_env=api_key_env,
    )


def _api_key_env_for(role: str) -> str | None:
    """Resolve the API-key environment variable for a live base-run role.

    Base-run manifests record provider identity without ``api_key_env``; the
    executor resolves it from the same env overrides as
    :func:`benchmarks.common.providers.resolve_provider_config` (role-specific
    ``EEM_{ROLE}_API_KEY_ENV`` first, then the shared ``EEM_LLM_API_KEY_ENV``).
    """
    import os

    role_prefix = role.upper()
    role_env = os.environ.get(f"EEM_{role_prefix}_API_KEY_ENV")
    if role_env:
        return role_env
    return os.environ.get("EEM_LLM_API_KEY_ENV")


def _fake_resolved(role: str) -> ResolvedModelConfig:
    return ResolvedModelConfig(
        role=role,
        kind=ProviderKind.DETERMINISTIC_FAKE,
        model_id=f"deterministic-local-fake-{role}",
    )


def _store_kind(config: AblationConfig) -> str:
    # The controlled fixture uses the raw store so each retrieval switch can be
    # isolated without consolidation confounds; dataset executors use the ETEC
    # store to mirror the base run's ``full`` method.
    return "raw" if config.dataset == "controlled" else "etec"


def _build_stores(
    config: AblationConfig,
    records: Sequence[NormalizedRecord],
    identities: dict[str, ResolvedModelConfig],
    base_run_manifest: RunManifest | None,
) -> dict[str, Any]:
    """Build one store per sample.

    Controlled runs extract deterministically with the controlled fixture
    extractor and use the raw store. Dataset executors reuse the base run's
    immutable extraction snapshot, build the ETEC store, and read the base
    run's model cache in strict offline mode (cache misses raise; no network).
    """
    if config.dataset == "controlled":
        extractor: Any = ControlledFixtureExtractor()
        base_snapshots: dict[str, ExtractionSnapshot] = {}
    else:
        if base_run_manifest is None:
            raise ValueError("dataset executors require a base run manifest")
        extractor = None
        base_snapshots = _load_base_snapshots(_require_dir(config.base_run_dir))

    apply_etec = config.dataset != "controlled"
    stores: dict[str, Any] = {}
    for record in records:
        user_id = record.sample_id
        if config.dataset == "controlled":
            snapshot = extract_event_snapshot(
                record,
                extractor,
                user_id=user_id,
                extractor_identity=provider_identity(identities["extractor"]),
            )
        else:
            snapshot = base_snapshots.get(record.sample_id)
            if snapshot is None:
                raise ValueError(
                    f"base run has no extraction snapshot for {record.sample_id}"
                )
        store, _ = materialize_event_store(
            snapshot,
            apply_etec=apply_etec,
            embedding_model=_embedding_for(config, base_run_manifest),
            user_id=user_id,
        )
        stores[user_id] = store
    return stores


def _embedding_for(
    config: AblationConfig,
    base_run_manifest: RunManifest | None,
) -> EmbeddingModel:
    if base_run_manifest is None:
        return DeterministicFakeEmbeddingModel()
    cache_root = _require_dir(config.base_run_dir) / "model_cache"
    return CachedEmbeddingModel(
        _OfflineOnlyEmbedding(base_run_manifest.embedding.model_id),
        FileModelCache(cache_root),
    )


def _load_base_snapshots(base_run_dir: Path) -> dict[str, ExtractionSnapshot]:
    snapshot_path = base_run_dir / "extraction_snapshot.json"
    if not snapshot_path.exists():
        raise FileNotFoundError(f"base run has no extraction_snapshot.json: {snapshot_path}")
    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("extraction_snapshot.json must be a JSON array of snapshots")
    return {
        snapshot.conversation_id: snapshot
        for snapshot in (ExtractionSnapshot.model_validate(item) for item in payload)
    }


def _run_arm(
    config: AblationConfig,
    arm: AblationArmConfig,
    arm_dir: Path,
    manifest: AblationRunManifest,
    stores: dict[str, Any],
    records: Sequence[NormalizedRecord],
    embedding: EmbeddingModel,
) -> ArmSummary:
    rows: list[dict[str, Any]] = []
    packing_bound_questions = 0
    for record in records:
        user_id = record.sample_id
        harness = RetrievalHarness(
            stores[user_id],
            embedding,
            max_items_per_source=config.max_items_per_source,
            max_candidates_per_source=config.max_candidates_per_source,
        )
        reference_time = _reference_time_for(config.dataset, record)
        for question in record.questions:
            result = harness.retrieve(
                question.question,
                user_id=user_id,
                budget_tokens=arm.controls.budget_tokens,
                reference_time=reference_time,
                controls=arm.controls,
            )
            payload = _retrieval_payload(question.question_id, result)
            rows.append(
                {
                    "dataset": config.dataset,
                    "sample_id": user_id,
                    "question_id": question.question_id,
                    "arm": arm.name,
                    **payload,
                }
            )
            if payload["packing_bound"]:
                packing_bound_questions += 1

    _write_arm_artifacts(arm_dir, rows)
    if not (arm_dir / "manifest.json").exists():
        write_manifest(arm_dir, manifest)
    else:
        check_resume(arm_dir, manifest)
    finalize_run(arm_dir, manifest, completion_counts={"questions": len(rows)})
    summary = ArmSummary(
        run_id=arm_dir.name,
        arm=arm.name,
        factor=arm.factor,
        artifact_class=_artifact_class(config),
        question_count=len(rows),
        packing_bound_questions=packing_bound_questions,
        manifest_hash=manifest.manifest_hash(),
        metadata={
            "controls": arm.controls.model_dump(mode="json"),
            "store": _store_kind(config),
        },
    )
    summary_path = arm_dir / "summary.json"
    summary_path.unlink(missing_ok=True)
    write_json_write_once(summary_path, summary)
    return summary


def _write_arm_artifacts(
    arm_dir: Path,
    rows: Sequence[dict[str, Any]],
) -> None:
    _rewrite_jsonl(arm_dir / "retrieval.jsonl", rows)
    evidence_rows = [
        EvidenceRecord(
            question_id=row["question_id"],
            raw_turn_id=str(ref["raw_turn_id"]),
            span=str(ref.get("locator") or ""),
            exact=True,
        )
        for row in rows
        for item in row["packed_items"]
        for ref in item["evidence_refs"]
        if ref.get("raw_turn_id") is not None
    ]
    _rewrite_jsonl(arm_dir / "evidence.jsonl", evidence_rows)
    _rewrite_jsonl(arm_dir / "consolidation.jsonl", [])
    _rewrite_json(arm_dir / "extraction_snapshot.json", [])


def _arm_deltas(
    arm: AblationArmConfig,
    run_dir: Path,
    base_arm_dir: Path,
) -> dict[str, Any]:
    """Compare the arm's persisted retrieval rows to the base arm's rows.

    The comparison runs over the written ``retrieval.jsonl`` artifacts, so the
    deltas are reproducible from the finalized artifacts themselves.
    """
    arm_rows = _rows_by_question(run_dir / arm.name / "retrieval.jsonl")
    base_rows = _rows_by_question(base_arm_dir / "retrieval.jsonl")
    questions: list[dict[str, Any]] = []
    delta_count = 0
    for question_id in sorted(arm_rows):
        base_row = base_rows.get(question_id)
        if base_row is None:
            continue
        base_fp = _row_fingerprint(base_row)
        arm_fp = _row_fingerprint(arm_rows[question_id])
        fields_changed = sorted(
            field for field in base_fp if base_fp[field] != arm_fp[field]
        )
        delta = bool(fields_changed)
        if delta:
            delta_count += 1
        questions.append(
            {
                "question_id": question_id,
                "delta": delta,
                "fields_changed": fields_changed,
                "packing_bound": bool(arm_rows[question_id]["packing_bound"]),
            }
        )
    return {
        "factor": arm.factor,
        "delta_question_count": delta_count,
        "questions": questions,
    }


def _rows_by_question(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"missing retrieval artifact: {path}")
    return {
        row["question_id"]: row
        for row in (json.loads(line) for line in path.read_text().splitlines())
    }


def _row_fingerprint(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "intent": row.get("intent"),
        "strategy": row.get("strategy"),
        "evidence_policy": row.get("evidence_policy"),
        "budget_tokens": row.get("budget_tokens"),
        "packing_bound": bool(row.get("packing_bound")),
        "selected": [
            {
                "memory_id": str(item["memory_id"]),
                "final_score": item.get("final_score"),
                "reason": item.get("reason"),
            }
            for item in row.get("packed_items", [])
        ],
        "exclusions": [
            {"memory_id": str(item["memory_id"]), "reason": item.get("reason")}
            for item in row.get("exclusions", [])
        ],
    }


def _require_controlled_deltas(config: AblationConfig, deltas: dict[str, Any]) -> None:
    """Gate D: every required factor must change at least one decision on the fixture."""
    for factor in REQUIRED_FACTORS:
        arm = next(arm for arm in config.arms if arm.factor == factor)
        if arm.name not in deltas:
            continue
        if deltas[arm.name]["delta_question_count"] == 0:
            raise ValueError(
                f"controlled run: factor {factor!r} (arm {arm.name}) produced no "
                "decision delta; Gate D requires at least one controlled delta per factor"
            )


def _write_deltas(run_dir: Path, config: AblationConfig, deltas: dict[str, Any]) -> None:
    payload: dict[str, Any] = {
        "ablation_id": config.ablation_id,
        "dataset": config.dataset,
        "required_factors": list(REQUIRED_FACTORS),
        "arms": deltas,
    }
    _rewrite_json(run_dir / "deltas.json", payload)


def _family_manifest(
    config: AblationConfig,
    run_dir: Path,
    *,
    expected_sample_ids: Sequence[str],
    expected_question_ids: Sequence[str],
) -> RunManifest:
    return RunManifest(
        run_id=run_dir.name,
        artifact_class=_artifact_class(config),
        dataset=config.dataset,
        dataset_path=str(config.dataset_path),
        dataset_hash=_dataset_hash(config.dataset_path),
        scope=_scope(config),
        methods=[arm.name for arm in [config.base, *config.arms]],
        reader=provider_identity(_fake_resolved("reader")),
        extractor=provider_identity(_fake_resolved("extractor")),
        embedding=provider_identity(_fake_resolved("embedding")),
        tokenizer=TokenizerIdentity(
            name=DEFAULT_TOKEN_ESTIMATOR.name,
            version=DEFAULT_TOKEN_ESTIMATOR.version,
        ),
        policies=PolicyVersions(
            extraction="shared-snapshot.v1",
            router=ROUTER_POLICY_NAME,
            retrieval=POLICY_NAME,
            consolidation="etec.v1",
        ),
        budget=BudgetSpec(
            input_tokens=config.base.controls.budget_tokens or 2048,
            max_items_per_source=config.max_items_per_source,
            max_candidates_per_source=config.max_candidates_per_source,
        ),
        git=GitState(
            commit=current_git_commit(),
            dirty=_git_is_dirty(),
            dirty_diff_hash=_dirty_diff_hash() if _git_is_dirty() else None,
        ),
        config_hash=_hash_json(config.model_dump(mode="json")),
        expected_sample_ids=list(expected_sample_ids),
        expected_question_ids=list(expected_question_ids),
        metadata={
            "ablation_id": config.ablation_id,
            "store": _store_kind(config),
            "factor_map": {arm.name: arm.factor for arm in config.arms},
        },
    )


def _arm_manifest(
    config: AblationConfig,
    arm: AblationArmConfig,
    arm_dir: Path,
    identities: dict[str, ResolvedModelConfig],
    base_run_manifest: RunManifest | None,
    *,
    expected_sample_ids: Sequence[str],
    expected_question_ids: Sequence[str],
    controlled_run_hash: str,
    base_run_hash: str,
) -> AblationRunManifest:
    return AblationRunManifest(
        run_id=arm_dir.name,
        artifact_class=_artifact_class(config),
        dataset=config.dataset,
        dataset_path=str(config.dataset_path),
        dataset_hash=_dataset_hash(config.dataset_path),
        scope=_scope(config),
        methods=[arm.name],
        reader=provider_identity(identities["reader"]),
        extractor=provider_identity(identities["extractor"]),
        embedding=provider_identity(identities["embedding"]),
        tokenizer=TokenizerIdentity(
            name=DEFAULT_TOKEN_ESTIMATOR.name,
            version=DEFAULT_TOKEN_ESTIMATOR.version,
        ),
        policies=PolicyVersions(
            extraction="shared-snapshot.v1",
            router=ROUTER_POLICY_NAME,
            retrieval=POLICY_NAME,
            consolidation="etec.v1",
        ),
        budget=BudgetSpec(
            input_tokens=arm.controls.budget_tokens or 2048,
            max_items_per_source=config.max_items_per_source,
            max_candidates_per_source=config.max_candidates_per_source,
        ),
        git=GitState(
            commit=current_git_commit(),
            dirty=_git_is_dirty(),
            dirty_diff_hash=_dirty_diff_hash() if _git_is_dirty() else None,
        ),
        config_hash=_hash_json(config.model_dump(mode="json")),
        expected_sample_ids=list(expected_sample_ids),
        expected_question_ids=list(expected_question_ids),
        ablation=arm.name,
        controlled_run_hash=controlled_run_hash,
        base_run_hash=base_run_hash,
        changed_factors=[arm.factor],
        metadata={
            "factor": arm.factor,
            "controls": arm.controls.model_dump(mode="json"),
            "store": _store_kind(config),
            "base_run_identity": (
                {
                    "run_id": base_run_manifest.run_id,
                    "manifest_hash": base_run_manifest.manifest_hash(),
                }
                if base_run_manifest is not None
                else {"run_id": None, "manifest_hash": None}
            ),
        },
    )


def _retrieval_payload(question_id: str, result: QEMRRetrievalResult) -> dict[str, Any]:
    return {
        "question_id": question_id,
        "intent": result.intent.value,
        "strategy": result.strategy.value,
        "evidence_policy": result.evidence_policy.value,
        "budget_tokens": result.budget_tokens,
        "total_tokens": result.total_tokens,
        "content_tokens": result.budget.content_tokens,
        "prompt_overhead_tokens": result.budget.prompt_overhead_tokens,
        "total_input_tokens_estimate": result.budget.total_input_tokens_estimate,
        "packing_bound": _packing_bound(result),
        "candidate_count": len(result.candidates),
        "exclusion_count": len(result.exclusions),
        "exclusions": [
            {
                "memory_id": str(exclusion.memory_id),
                "reason": exclusion.reason,
            }
            for exclusion in result.exclusions
        ],
        "source_failures": [
            {
                "source": failure.source.value,
                "reason_code": failure.reason_code,
                "degraded_policy": failure.degraded_policy.value,
                "duration_ms": failure.duration_ms,
            }
            for failure in result.source_failures
        ],
        "packed_items": [
            {
                "memory_id": str(item.memory.memory_id),
                "content": item.memory.content,
                "final_score": item.final_score,
                "component_scores": item.component_scores,
                "token_count": item.token_count,
                "historical": item.historical,
                "reason": item.reason,
                "evidence_refs": [
                    {
                        "source_type": ref.source_type,
                        "source_id": ref.source_id,
                        "locator": ref.locator,
                        "quote": ref.quote,
                        "session_id": ref.metadata.get("session_id"),
                        "raw_turn_id": ref.metadata.get("raw_turn_id"),
                    }
                    for ref in item.evidence_refs
                ],
            }
            for item in result.selected_context
        ],
    }


def _packing_bound(result: QEMRRetrievalResult) -> bool:
    return any(
        exclusion.reason == "budget_exceeded" for exclusion in result.exclusions
    )


def _reference_time_for(dataset: str, record: NormalizedRecord) -> datetime | None:
    if dataset == "longmemeval":
        for question in record.questions:
            if question.asked_at is not None:
                return question.asked_at
        return None
    ordered = sorted(record.sessions, key=lambda session: (session.timestamp, session.session_id))
    if not ordered:
        return None
    return ordered[-1].timestamp


def _load_records(config: AblationConfig) -> list[NormalizedRecord]:
    iterator = (
        iter_locomo_records(config.dataset_path)
        if config.dataset in ("controlled", "locomo")
        else iter_longmemeval_records(config.dataset_path)
    )
    records: list[NormalizedRecord] = []
    for record in iterator:
        records.append(record)
        if config.sample_limit is not None and len(records) >= config.sample_limit:
            break
    return records


def _require_dir(path: Path | None) -> Path:
    if path is None:
        raise ValueError("a run directory is required")
    resolved = Path(path)
    if not resolved.exists():
        raise FileNotFoundError(f"run directory missing: {resolved}")
    return resolved


def _finalization_hash_of(run_dir: Path) -> str:
    return load_finalized(run_dir).finalization_hash()


def _is_finalized(run_dir: Path) -> bool:
    return (run_dir / "finalized" / "FINALIZED.json").exists()


def _load_arm_summary(arm_dir: Path) -> ArmSummary:
    summary_path = arm_dir / "summary.json"
    if not summary_path.exists():
        raise FileNotFoundError(f"finalized arm has no stored summary: {summary_path}")
    return ArmSummary.model_validate_json(summary_path.read_text())


def _artifact_class(config: AblationConfig) -> ArtifactClass:
    if config.dataset == "controlled":
        return ArtifactClass.SMOKE
    return ArtifactClass.PUBLICATION


def _scope(config: AblationConfig) -> str:
    if config.sample_limit is not None:
        return f"sample_limit={config.sample_limit}"
    return "full"


def _write_run_root_artifacts(
    run_dir: Path,
    arm_summaries: dict[str, ArmSummary],
) -> None:
    _rewrite_json(run_dir / "extraction_snapshot.json", [])
    _rewrite_jsonl(run_dir / "retrieval.jsonl", [])
    _rewrite_jsonl(run_dir / "evidence.jsonl", [])
    _rewrite_jsonl(run_dir / "consolidation.jsonl", [])
    _rewrite_json(
        run_dir / "arms.json",
        {name: summary.model_dump(mode="json") for name, summary in sorted(arm_summaries.items())},
    )


def _rewrite_jsonl(path: Path, records: Sequence[Any]) -> None:
    path.unlink(missing_ok=True)
    write_jsonl_write_once(path, records)


def _rewrite_json(path: Path, payload: Any) -> None:
    path.unlink(missing_ok=True)
    write_json_write_once(path, payload)


def _hash_json(payload: dict[str, Any]) -> str:
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _dataset_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1 << 20)
            if not chunk:
                break
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _git_is_dirty() -> bool:
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return True
    return bool(result.stdout.strip())


def _dirty_diff_hash() -> str:
    try:
        result = subprocess.run(
            ["git", "diff", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return ""
    return _hash_json({"diff": result.stdout})


if __name__ == "__main__":
    raise SystemExit(main())
