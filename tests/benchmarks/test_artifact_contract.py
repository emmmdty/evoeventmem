from __future__ import annotations

import pytest
from pydantic import ValidationError

from benchmarks.common.artifacts import (
    AblationRunManifest,
    ArtifactClass,
    BudgetSpec,
    ConsolidationAction,
    ConsolidationRecord,
    EvidenceRecord,
    ExtractionRejection,
    ExtractionSnapshot,
    FinalizationRecord,
    GitState,
    PackedItem,
    PolicyVersions,
    ProviderIdentity,
    RetrievalRecord,
    RunManifest,
    SourceFailure,
    TokenizerIdentity,
    canonical_json,
    canonical_json_hash,
)


def provider(kind: str) -> ProviderIdentity:
    return ProviderIdentity(
        kind=kind,
        provider="openai",
        model_id=f"model-{kind}",
        version="1.0",
        endpoint=f"https://api.example/{kind}",
    )


def manifest(**overrides: object) -> RunManifest:
    values: dict[str, object] = {
        "run_id": "run-1",
        "artifact_class": ArtifactClass.SMOKE,
        "dataset": "longmemeval",
        "dataset_path": "data/longmemeval",
        "dataset_hash": "sha256:dataset",
        "scope": "lm-small",
        "methods": ["vector_rag", "etec"],
        "reader": provider("reader"),
        "extractor": provider("extractor"),
        "embedding": provider("embedding"),
        "tokenizer": TokenizerIdentity(name="tiktoken", version="1"),
        "policies": PolicyVersions(
            extraction="v1", router="v1", retrieval="v1", consolidation="v1"
        ),
        "budget": BudgetSpec(input_tokens=2048, max_items_per_source=5),
        "git": GitState(commit="abc123", dirty=False),
        "config_hash": "sha256:config",
        "expected_sample_ids": ["s1", "s2"],
        "expected_question_ids": ["q1", "q2"],
    }
    values.update(overrides)
    return RunManifest.model_validate(values)


def test_manifest_resolves_separate_provider_identities() -> None:
    m = manifest()
    assert m.reader.model_id == "model-reader"
    assert m.extractor.model_id == "model-extractor"
    assert m.embedding.model_id == "model-embedding"
    assert m.reader.endpoint != m.extractor.endpoint
    assert m.embedding.endpoint != m.reader.endpoint


def test_manifest_resolves_dataset_scope_and_hash() -> None:
    m = manifest()
    assert m.dataset_path == "data/longmemeval"
    assert m.dataset_hash == "sha256:dataset"
    assert m.scope == "lm-small"


def test_manifest_resolves_tokenizer_policies_and_budget() -> None:
    m = manifest()
    assert m.tokenizer.name == "tiktoken"
    assert m.policies.consolidation == "v1"
    assert m.budget.input_tokens == 2048
    assert m.budget.max_items_per_source == 5


def test_manifest_resolves_git_state_and_config_hash() -> None:
    m = manifest()
    assert m.git.commit == "abc123"
    assert m.git.dirty is False
    assert m.config_hash == "sha256:config"


def test_ablation_manifest_extends_manifest() -> None:
    a = AblationRunManifest.model_validate(
        {
            **manifest().model_dump(),
            "ablation": "evidence_policy",
            "controlled_run_hash": "sha256:controlled",
            "base_run_hash": "sha256:base",
            "changed_factors": ["evidence_policy"],
        }
    )
    assert a.artifact_class is ArtifactClass.SMOKE
    assert a.controlled_run_hash == "sha256:controlled"
    assert a.changed_factors == ["evidence_policy"]
    assert isinstance(a, RunManifest)


def test_extraction_snapshot_typing_and_provenance() -> None:
    snap = ExtractionSnapshot(
        snapshot_id="snap-1",
        conversation_id="conv-1",
        extractor=provider("extractor"),
        raw_turn_count=3,
        event_count=2,
        rejections=[
            ExtractionRejection(raw_turn_id="t1", reason="unsupported span")
        ],
    )
    assert snap.provenance_ok()
    assert snap.snapshot_hash().startswith("sha256:")
    assert snap.rejections[0].raw_turn_id == "t1"
    assert snap.snapshot_hash() == ExtractionSnapshot.model_validate(
        snap.model_dump()
    ).snapshot_hash()


def test_retrieval_record_budget_fields() -> None:
    rec = RetrievalRecord(
        question_id="q1",
        evidence_policy="constrained",
        packed_items=[PackedItem(item_id="i1", evidence_refs=["t1"], content_tokens=10)],
        total_input_tokens=2048,
        content_tokens=1024,
        prompt_overhead_tokens=1024,
        source_failures=[
            SourceFailure(source="temporal", reason_code="E_TEMPORAL_DOWN", degraded_policy=True)
        ],
    )
    assert rec.total_input_tokens == rec.content_tokens + rec.prompt_overhead_tokens
    assert rec.packed_items[0].evidence_refs == ["t1"]
    assert rec.source_failures[0].degraded_policy is True
    assert rec.packing_bound is False


def test_consolidation_record_typing() -> None:
    rec = ConsolidationRecord(
        sample_id="s1",
        evidence=[EvidenceRecord(question_id="q1", raw_turn_id="t1", span="span")],
        action=ConsolidationAction.MERGE,
    )
    assert rec.action is ConsolidationAction.MERGE
    assert rec.evidence[0].exact is True


def test_canonical_json_is_deterministic_and_key_sorted() -> None:
    assert canonical_json({"b": 1, "a": 2}) == '{"a":2,"b":1}'
    assert canonical_json({"b": 1, "a": 2}) == canonical_json({"a": 2, "b": 1})
    assert canonical_json_hash({"x": [1, 2]}) == canonical_json_hash({"x": [1, 2]})


def test_canonical_hash_is_sha256_prefixed() -> None:
    digest = canonical_json_hash({"a": 1})
    assert digest.startswith("sha256:")
    assert len(digest) == len("sha256:") + 64


def test_manifest_hash_is_stable_across_serialization() -> None:
    m = manifest()
    assert m.manifest_hash() == manifest().manifest_hash()
    assert m.manifest_hash() == RunManifest.model_validate(
        m.model_dump()
    ).manifest_hash()


def test_manifest_hash_changes_with_config_and_methods() -> None:
    base = manifest().manifest_hash()
    assert manifest(config_hash="sha256:other").manifest_hash() != base
    assert manifest(methods=["vector_rag"]).manifest_hash() != base


def test_finalization_record_hash_is_stable() -> None:
    record = FinalizationRecord(
        artifact_class=ArtifactClass.SMOKE,
        manifest_hash="sha256:manifest",
        required_hashes={"manifest.json": "sha256:manifest"},
    )
    assert record.finalization_hash() == FinalizationRecord.model_validate(
        record.model_dump()
    ).finalization_hash()


def test_artifact_classes_are_distinct() -> None:
    assert ArtifactClass.SMOKE.value == "smoke"
    assert ArtifactClass.DIAGNOSTIC.value == "diagnostic"
    assert ArtifactClass.PUBLICATION.value == "publication"


def test_retrieval_evidence_policy_union() -> None:
    for policy in ("constrained", "provenance_only"):
        RetrievalRecord(
            question_id="q1",
            evidence_policy=policy,
            total_input_tokens=1,
            content_tokens=1,
            prompt_overhead_tokens=0,
        )
    with pytest.raises(ValidationError):
        RetrievalRecord(
            question_id="q1",
            evidence_policy="anything_else",
            total_input_tokens=1,
            content_tokens=1,
            prompt_overhead_tokens=0,
        )