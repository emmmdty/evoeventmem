from __future__ import annotations

import pytest

from benchmarks.common.artifacts import (
    ArtifactClass,
    BudgetSpec,
    GitState,
    PolicyVersions,
    ProviderIdentity,
    RunManifest,
    TokenizerIdentity,
    check_resume,
    check_working_state,
    finalize_run,
    load_finalized,
    regenerate_derived,
    require_manifest,
    required_file_paths,
    validate_manifest_ids,
    write_manifest,
    write_per_sample,
)


def provider(kind: str) -> ProviderIdentity:
    return ProviderIdentity(
        kind=kind,
        provider="openai",
        model_id=f"model-{kind}",
        version="1.0",
        endpoint=f"https://api.example/{kind}",
    )


def manifest(
    *,
    artifact_class: ArtifactClass = ArtifactClass.SMOKE,
    dirty: bool = False,
    sample_ids: list[str] | None = None,
    question_ids: list[str] | None = None,
    **overrides: object,
) -> RunManifest:
    values: dict[str, object] = {
        "run_id": "run-1",
        "artifact_class": artifact_class,
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
            extraction="v1", retrieval="v1", consolidation="v1"
        ),
        "budget": BudgetSpec(input_tokens=2048),
        "git": GitState(commit="abc123", dirty=dirty),
        "config_hash": "sha256:config",
        "expected_sample_ids": sample_ids if sample_ids is not None else ["s1", "s2"],
        "expected_question_ids": question_ids if question_ids is not None else ["q1", "q2"],
    }
    values.update(overrides)
    return RunManifest.model_validate(values)


def seed_working_run(tmp_path, m: RunManifest) -> None:
    write_manifest(tmp_path, m)
    for path in required_file_paths(tmp_path, m.artifact_class):
        if path.name != "manifest.json":
            path.write_text("placeholder")


def test_write_manifest_is_write_once(tmp_path) -> None:
    m = manifest()
    write_manifest(tmp_path, m)
    with pytest.raises(FileExistsError):
        write_manifest(tmp_path, m)


def test_manifest_detects_duplicate_ids() -> None:
    m = manifest(sample_ids=["s1", "s1"], question_ids=["q1", "q2"])
    with pytest.raises(ValueError, match="duplicate sample IDs"):
        validate_manifest_ids(m)


def test_manifest_requires_both_id_lists() -> None:
    m = manifest(sample_ids=[], question_ids=["q1", "q2"])
    with pytest.raises(ValueError, match="expected sample and question"):
        validate_manifest_ids(m)


def test_working_run_allows_adding_and_regenerating(tmp_path) -> None:
    m = manifest()
    write_manifest(tmp_path, m)
    write_per_sample(tmp_path, "samples/s1.json", m)
    regenerate_derived(tmp_path, {"a": 1}, "summary.json")
    assert (tmp_path / "summary.json").exists()
    assert (tmp_path / "samples/s1.json").exists()


def test_regenerate_non_derived_file_refused(tmp_path) -> None:
    write_manifest(tmp_path, manifest())
    with pytest.raises(ValueError, match="not a derived file"):
        regenerate_derived(tmp_path, {"a": 1}, "manifest.json")


def test_finalize_smoke_run_creates_write_once_finalized(tmp_path) -> None:
    m = manifest()
    write_manifest(tmp_path, m)
    record = finalize_run(tmp_path, m)
    assert (tmp_path / "finalized" / "FINALIZED.json").exists()
    assert record.manifest_hash == m.manifest_hash()
    assert record.required_hashes["manifest.json"].startswith("sha256:")

    with pytest.raises(FileExistsError):
        finalize_run(tmp_path, m)


def test_finalized_overwrite_rejected(tmp_path) -> None:
    m = manifest()
    write_manifest(tmp_path, m)
    finalize_run(tmp_path, m)
    with pytest.raises(FileExistsError):
        finalize_run(tmp_path, m)


def test_finalize_refuses_missing_required_artifact(tmp_path) -> None:
    m = manifest()
    write_manifest(tmp_path, m)
    (tmp_path / "manifest.json").unlink()
    with pytest.raises(FileNotFoundError, match="missing"):
        finalize_run(tmp_path, m)


def test_load_finalized_rejects_hash_drift(tmp_path) -> None:
    m = manifest()
    write_manifest(tmp_path, m)
    finalize_run(tmp_path, m)
    (tmp_path / "manifest.json").write_text("tampered")
    with pytest.raises(ValueError, match="hash drift"):
        load_finalized(tmp_path)


def test_finalized_run_refuses_mutation(tmp_path) -> None:
    m = manifest()
    write_manifest(tmp_path, m)
    finalize_run(tmp_path, m)
    with pytest.raises(ValueError, match="finalized"):
        regenerate_derived(tmp_path, {"a": 1}, "summary.json")
    with pytest.raises(ValueError, match="finalized"):
        write_per_sample(tmp_path, "samples/s1.json", m)


def test_resume_refuses_manifest_drift(tmp_path) -> None:
    m = manifest()
    write_manifest(tmp_path, m)
    drift = manifest(config_hash="sha256:drifted")
    with pytest.raises(ValueError, match="manifest drift"):
        check_resume(tmp_path, drift)
    check_resume(tmp_path, m)


def test_dirty_publication_refused(tmp_path) -> None:
    m = manifest(artifact_class=ArtifactClass.PUBLICATION, dirty=True)
    required_file_paths(tmp_path, m.artifact_class)
    with pytest.raises(ValueError, match="clean Git tree"):
        finalize_run(tmp_path, m)


def test_publication_requires_complete_required_files(tmp_path) -> None:
    m = manifest(artifact_class=ArtifactClass.PUBLICATION, dirty=False)
    write_manifest(tmp_path, m)
    with pytest.raises(FileNotFoundError, match="missing required"):
        finalize_run(tmp_path, m)


def test_complete_clean_publication_finalizes(tmp_path) -> None:
    m = manifest(artifact_class=ArtifactClass.PUBLICATION, dirty=False)
    write_manifest(tmp_path, m)
    for path in required_file_paths(tmp_path, m.artifact_class):
        if path.name != "manifest.json":
            path.write_text("content")
    record = finalize_run(tmp_path, m)
    assert record.artifact_class is ArtifactClass.PUBLICATION
    assert set(record.required_hashes) == {
        "manifest.json",
        "extraction_snapshot.json",
        "retrieval.jsonl",
        "evidence.jsonl",
        "consolidation.jsonl",
    }
    load_finalized(tmp_path)


def test_working_state_requires_manifest(tmp_path) -> None:
    with pytest.raises(FileNotFoundError, match="missing manifest"):
        require_manifest(tmp_path)


def test_check_working_state(tmp_path) -> None:
    m = manifest()
    write_manifest(tmp_path, m)
    assert check_working_state(tmp_path).run_id == "run-1"


def test_duplicate_question_ids_rejected(tmp_path) -> None:
    m = manifest(question_ids=["q1", "q1"])
    with pytest.raises(ValueError, match="duplicate question IDs"):
        validate_manifest_ids(m)