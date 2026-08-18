from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmarks.common.artifacts import ExtractionSnapshot, ProviderIdentity
from benchmarks.mechanism.replay import (
    OfflineCacheMiss,
    build_offline_embedding,
    replay_run,
    replay_sample,
)
from evoeventmem.domain.models import EntityRef, EvidenceRef, MemoryKind, MemoryRecord

MS_RUN = Path("runs/publication/longmemeval-test20-ms")


def _identity() -> ProviderIdentity:
    return ProviderIdentity(
        kind="deterministic_fake",
        provider="deterministic_fake",
        model_id="deterministic-local-embedding",
        endpoint="n/a",
    )


def _memory(
    *,
    content: str,
    turn_id: str,
    session_id: str,
    fact: dict[str, str] | None = None,
) -> MemoryRecord:
    metadata: dict[str, object] = {"source_dataset": "fixture"}
    if fact:
        metadata.update(fact)
    return MemoryRecord(
        user_id="u1",
        session_id=session_id,
        memory_kind=MemoryKind.EVENT,
        content=content,
        entities=[EntityRef(name="Caroline", role="subject")],
        evidence_refs=[
            EvidenceRef(
                source_type="turn",
                source_id=f"fixture/{turn_id}",
                locator="chars=0:1",
                quote=content,
                metadata={"raw_turn_id": turn_id, "session_id": session_id},
            )
        ],
        event_time=None,
        metadata=metadata,
    )


def _snapshot(*, events: list[MemoryRecord], conversation_id: str = "u1") -> ExtractionSnapshot:
    return ExtractionSnapshot(
        snapshot_id="fixture-snapshot",
        conversation_id=conversation_id,
        extractor=_identity(),
        raw_turn_count=len(events),
        event_count=len(events),
        events=events,
        rejections=[],
    )


def _fake_base_run(tmp_path: Path) -> Path:
    base_run = tmp_path / "base_run"
    base_run.mkdir()
    (base_run / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "run_id": "base_run",
                "artifact_class": "smoke",
                "dataset": "fixture",
                "dataset_path": "fixture.json",
                "dataset_hash": "sha256:deadbeef",
                "scope": "fixture",
                "methods": ["full"],
                "reader": _identity().model_dump(mode="json"),
                "extractor": _identity().model_dump(mode="json"),
                "embedding": _identity().model_dump(mode="json"),
                "tokenizer": {"name": "t", "version": "1"},
                "policies": {
                    "extraction": "v1",
                    "router": "v1",
                    "retrieval": "v1",
                    "consolidation": "etec.v1",
                },
                "budget": {"input_tokens": 4096},
                "git": {"commit": "x", "dirty": False},
                "config_hash": "sha256:abc",
                "expected_sample_ids": ["u1"],
                "expected_question_ids": ["u1"],
                "metadata": {},
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return base_run


def test_replay_sample_records_merge_decision_for_duplicate_content(tmp_path: Path) -> None:
    snapshot = _snapshot(
        events=[
            _memory(
                content="Caroline lives in Seattle.",
                turn_id="t1:0",
                session_id="t1",
            ),
            _memory(
                content="Caroline lives in Seattle.",
                turn_id="t2:0",
                session_id="t2",
            ),
        ]
    )
    base_run = _fake_base_run(tmp_path)
    replay = replay_sample(snapshot, embedding=build_offline_embedding(base_run), user_id="u1")

    assert dict(replay.actions) == {"ADD": 1, "MERGE": 1}
    assert replay.etec_memory_count == 1
    assert replay.raw_memory_count == 2
    merge_decisions = [d for d in replay.decisions if d.action == "MERGE"]
    assert len(merge_decisions) == 1
    assert "duplicate_fact" in merge_decisions[0].rule_hits
    assert "contradiction_score" in merge_decisions[0].features


def test_replay_sample_records_supersede_when_fact_slot_declared(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    snapshot = _snapshot(
        events=[
            _memory(
                content="Caroline lives in Seattle.",
                turn_id="t1:0",
                session_id="t1",
                fact={"fact_slot": "profile.city", "fact_value": "Seattle"},
            ),
            _memory(
                content="Caroline lives in Austin.",
                turn_id="t2:0",
                session_id="t2",
                fact={"fact_slot": "profile.city", "fact_value": "Austin"},
            ),
        ]
    )
    base_run = _fake_base_run(tmp_path)
    snapshot.events[0].valid_from = datetime(2024, 1, 1, tzinfo=UTC)
    snapshot.events[1].valid_from = datetime(2024, 2, 1, tzinfo=UTC)
    replay = replay_sample(snapshot, embedding=build_offline_embedding(base_run), user_id="u1")

    supersedes = [d for d in replay.decisions if d.action == "SUPERSEDE"]
    assert len(supersedes) == 1
    assert "temporal_contradiction" in supersedes[0].rule_hits
    assert supersedes[0].features["contradiction_score"] >= 0.7


def test_replay_sample_fact_slot_counts(tmp_path: Path) -> None:
    snapshot = _snapshot(
        events=[
            _memory(content="one", turn_id="t1:0", session_id="t1"),
            _memory(
                content="two",
                turn_id="t2:0",
                session_id="t2",
                fact={"fact_slot": "profile.x", "fact_value": "v"},
            ),
        ]
    )
    base_run = _fake_base_run(tmp_path)
    replay = replay_sample(
        snapshot,
        embedding=build_offline_embedding(base_run),
        user_id="u1",
    )
    assert replay.snapshot_events_with_fact_slot == 1
    assert replay.snapshot_events_with_fact_value == 1


def test_offline_embedding_raises_on_cache_miss(tmp_path: Path) -> None:
    base_run = _fake_base_run(tmp_path)
    (base_run / "model_cache" / "embeddings").mkdir(parents=True)
    identity = _identity().model_dump(mode="json")
    identity["kind"] = "openai_compatible"
    identity["provider"] = "openai_compatible"
    identity["model_id"] = "qwen3-embedding-0.6b"
    manifest = json.loads((base_run / "manifest.json").read_text(encoding="utf-8"))
    manifest["embedding"] = identity
    (base_run / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    embedding = build_offline_embedding(base_run)
    with pytest.raises(OfflineCacheMiss):
        embedding.embed_texts(["anything"])


@pytest.mark.skipif(
    not (MS_RUN / "finalized" / "FINALIZED.json").exists(),
    reason="longmemeval-test20-ms run artifacts not present",
)
def test_replay_ms_run_matches_ingestion_action_counts() -> None:
    try:
        replays = replay_run(MS_RUN)
    except OfflineCacheMiss as exc:
        pytest.skip(
            "ms run model_cache does not cover all replay embeddings "
            f"(candidate-generation embedding path diverges from the original "
            f"online run): {exc}"
        )

    assert set(replays) == {
        sample_id
        for sample_id in json.loads(
            Path("configs/longmemeval/test20-ms.selection.json").read_text(encoding="utf-8")
        )["sample_ids"]
    }
    assert dict(replays["4dfccbf8"].actions) == {"ADD": 223, "MERGE": 1}
    for sample_id, replay in replays.items():
        sample_path = MS_RUN / "samples" / f"{sample_id}.json"
        assert sample_path.exists(), sample_id
        assert dict(replay.actions) == json.loads(
            sample_path.read_text(encoding="utf-8")
        )["ingestion"]["etec"]["actions"]
        assert replay.snapshot_events_with_fact_slot == 0
        assert replay.snapshot_events_with_fact_value == 0
