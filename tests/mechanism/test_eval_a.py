from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from benchmarks.mechanism.eval_a import (
    bucket_decision,
    compute_m1_from_online,
    compute_m3,
    compute_metrics_from_online,
    has_fact_slot,
    ku_question_ids,
    match_new_value_memory,
    retrieved_session_ids,
    session_id_from_turn_id,
    summarize_replays,
)
from benchmarks.mechanism.gold import GoldAction, GoldPair
from benchmarks.mechanism.replay import ReplayDecision, SampleReplay
from evoeventmem.domain.models import EvidenceRef, MemoryKind, MemoryRecord
from evoeventmem.infra.in_memory_repository import InMemoryMemoryRepository

FIXTURE = Path("tests/fixtures/longmemeval/oracle_tiny.json")


def _decision(
    *,
    action: str = "ADD",
    rule_hits: list[str] | None = None,
    features: dict[str, object] | None = None,
    source_metadata: dict[str, object] | None = None,
    target_metadata: dict[str, object] | None = None,
    target_memory_id: str | None = "t",
) -> ReplayDecision:
    return ReplayDecision(
        sample_id="q",
        source_memory_id="s",
        action=action,
        score=0.5,
        reason="r",
        rule_hits=rule_hits or [],
        features={"contradiction_score": 0.0, "multi_valued": False, **(features or {})},
        source_metadata={"a": 1, **(source_metadata or {})},
        target_memory_id=target_memory_id,
        target_metadata={"b": 2, **(target_metadata or {})},
    )


def test_bucket_r1_when_fact_slot_absent() -> None:
    decision = _decision(action="MERGE", rule_hits=["duplicate_fact"])
    assert bucket_decision(decision) == "R1_structural_fact_slot_absent"


def test_bucket_r3_for_effective_time_rules() -> None:
    decision = _decision(
        action="REJECT",
        rule_hits=["missing_fact_effective_time"],
        source_metadata={"fact_slot": "profile.x"},
        target_metadata={"fact_slot": "profile.x"},
    )
    assert bucket_decision(decision) == "R3_effective_time_missing_or_equal"


def test_bucket_r5_for_multi_valued() -> None:
    decision = _decision(
        action="ADD",
        rule_hits=["explicit_multi_valued_slot"],
        features={"multi_valued": True},
        source_metadata={"fact_slot": "profile.x"},
        target_metadata={"fact_slot": "profile.x"},
    )
    assert bucket_decision(decision) == "R5_multi_valued"


def test_bucket_r7_for_disjoint_intervals() -> None:
    decision = _decision(
        action="ADD",
        rule_hits=["disjoint_temporal_intervals"],
        source_metadata={"fact_slot": "profile.x"},
        target_metadata={"fact_slot": "profile.x"},
    )
    assert bucket_decision(decision) == "R7_merge_temporal_disjoint"


def test_bucket_r4_for_missing_source_evidence() -> None:
    decision = _decision(
        action="REJECT",
        rule_hits=["missing_source_evidence"],
        source_metadata={"fact_slot": "profile.x"},
        target_metadata={"fact_slot": "profile.x"},
    )
    assert bucket_decision(decision) == "R4_extraction_miss"


def test_bucket_r2_for_low_contradiction_same_slot() -> None:
    decision = _decision(
        action="ADD",
        features={"contradiction_score": 0.3},
        source_metadata={"fact_slot": "profile.x"},
        target_metadata={"fact_slot": "profile.x"},
    )
    assert bucket_decision(decision) == "R2_contradiction_below_threshold"


def test_bucket_r6_for_no_candidate() -> None:
    decision = _decision(action="ADD", rule_hits=["no_active_candidate"], target_memory_id=None)
    assert bucket_decision(decision) == "R6_candidate_bounded_out"


def test_summarize_replays_counts_actions_and_contradictions() -> None:
    replay = SampleReplay(
        sample_id="q",
        actions={"ADD": 2, "MERGE": 1},
        decisions=[
            _decision(action="ADD"),
            _decision(action="MERGE", rule_hits=["duplicate_fact"]),
            _decision(action="ADD", features={"contradiction_score": 0.0}),
        ],
        snapshot_event_count=3,
        snapshot_events_with_fact_slot=0,
        snapshot_events_with_fact_value=0,
    )
    summary = summarize_replays({"q": replay})
    assert summary["actions"] == {"ADD": 2, "MERGE": 1}
    assert summary["contradiction_score"]["zero_count"] == 3
    assert summary["r1_r7_non_add"]["R1_structural_fact_slot_absent"] == 1
    assert summary["fact_slot_presence"]["snapshot_events"] == 3
    assert summary["fact_slot_presence"]["events_with_fact_slot"] == 0


def test_session_id_from_turn_id_strips_index() -> None:
    assert session_id_from_turn_id("answer_a25d4a91_1:4") == "answer_a25d4a91_1"
    assert session_id_from_turn_id("plain-session") == "plain-session"


def _retrieval_row(question_id: str, method: str, raw_turn_ids: list[str]) -> dict[str, object]:
    return {
        "question_id": question_id,
        "method": method,
        "packed_items": [
            {
                "evidence_refs": [{"raw_turn_id": turn_id} for turn_id in raw_turn_ids],
            }
        ],
    }


def test_compute_m3_new_recall_without_gold() -> None:
    rows = [
        _retrieval_row("lme-q1", "full", ["session-new:0", "session-old:0"]),
        _retrieval_row("lme-q1", "etec", ["session-old:0"]),
    ]
    result = compute_m3(rows, FIXTURE, question_ids=["lme-q1"])
    full = result["per_question"]["lme-q1"]["full"]
    assert full["new_recall"] == 1.0
    assert full["old_recall"] is None
    assert full["je_recall@8"] is None
    etec = result["per_question"]["lme-q1"]["etec"]
    assert etec["new_recall"] == 0.0
    assert result["methods"]["full"]["old_side_na_questions"] == 1


def test_compute_m3_joint_recall_with_gold() -> None:
    gold = GoldPair(
        question_id="lme-q1",
        subject="user",
        attribute="city",
        old_value="Austin",
        new_value="Seattle",
        old_value_turn_ids=["session-old:0"],
        new_value_turn_ids=["session-new:0"],
        t_q=datetime(2024, 2, 3, tzinfo=UTC),
        t_old=datetime(2024, 1, 1, tzinfo=UTC),
        gold_action=GoldAction.SUPERSEDE,
    )
    rows = [
        _retrieval_row("lme-q1", "full", ["session-new:0", "session-old:0"]),
        _retrieval_row("lme-q1", "vector_rag", ["session-new:0"]),
    ]
    result = compute_m3(
        rows,
        FIXTURE,
        question_ids=["lme-q1"],
        gold_by_question={"lme-q1": gold},
    )
    full = result["per_question"]["lme-q1"]["full"]
    assert full["old_recall"] == 1.0
    assert full["new_recall"] == 1.0
    assert full["je_recall@8"] == 1
    rag = result["per_question"]["lme-q1"]["vector_rag"]
    assert rag["old_recall"] == 0.0
    assert rag["je_recall@8"] == 0
    assert result["methods"]["full"]["je_recall@8"] == 1.0
    assert result["methods"]["vector_rag"]["je_recall@8"] == 0.0


def test_retrieved_session_ids_union_across_items() -> None:
    row = _retrieval_row("q", "full", ["a:0", "b:1"])
    assert retrieved_session_ids(row) == {"a", "b"}


def test_ku_question_ids_read_from_selection_files(tmp_path: Path) -> None:
    ms_selection = tmp_path / "test20-ms.selection.json"
    new_selection = tmp_path / "mechanism-evala.selection.json"
    ms_selection.write_text(
        json.dumps({"sample_ids": ["lme-q1", "other"]}), encoding="utf-8"
    )
    new_selection.write_text(json.dumps({"sample_ids": ["other"]}), encoding="utf-8")
    ms_ku, new_ku = ku_question_ids([ms_selection, new_selection], FIXTURE)
    assert ms_ku == ["lme-q1"]
    assert new_ku == []


def test_has_fact_slot_requires_non_empty_string() -> None:
    assert has_fact_slot({"fact_slot": "profile.city"})
    assert not has_fact_slot({})
    assert not has_fact_slot({"fact_slot": ""})
    assert not has_fact_slot({"fact_slot": 7})


def test_match_new_value_memory_uses_fact_value_or_content_coverage() -> None:
    gold = GoldPair(
        question_id="lme-q1",
        subject="user",
        attribute="city",
        old_value="Austin",
        new_value="Seattle",
        old_value_turn_ids=["session-old:0"],
        new_value_turn_ids=["session-new:0"],
        t_q=datetime(2024, 2, 3, tzinfo=UTC),
        t_old=datetime(2024, 1, 1, tzinfo=UTC),
        gold_action=GoldAction.SUPERSEDE,
    )
    repository = InMemoryMemoryRepository()
    repository.add(
        MemoryRecord(
            user_id="lme-q1",
            session_id="session-new",
            memory_kind=MemoryKind.EVENT,
            content="I moved to Seattle.",
            entities=[],
            evidence_refs=[
                EvidenceRef(source_type="turn", source_id="x", metadata={"raw_turn_id": "s:0"})
            ],
            event_time=datetime(2024, 2, 3, tzinfo=UTC),
            metadata={
                "fact_slot": "profile.city",
                "fact_value": "Seattle",
                "etec": {"decision": {"action": "ADD"}},
            },
        )
    )
    replay = SampleReplay(
        sample_id="lme-q1",
        actions={"ADD": 1},
        etec_store=repository,
    )
    memory, action = match_new_value_memory(replay, gold)
    assert action == "ADD"
    assert memory["metadata"]["fact_slot"] == "profile.city"


# --------------------------------------------------------------------------- #
# compute_metrics_from_online: reproduces runs/mechanism/evala/metrics.partial.json
# from online ingestion.etec.actions (NOT offline replay; the ms run model_cache
# has cache-miss divergence). Verifies the partial-artifact schema fields.
# --------------------------------------------------------------------------- #


def _write_synthetic_online_run(
    run_dir: Path,
    dataset: Path,
    ms_selection: Path,
) -> None:
    """Build a minimal ms run fixture mirroring the publication layout.

    Two KU samples with online ingestion.etec.actions = {"ADD": N}, extraction
    snapshots with no fact_slot (mirrors real extraction pipeline), and a
    retrieval.jsonl whose packed_items raw turn ids cover answer_session_ids.
    """
    samples_dir = run_dir / "samples"
    samples_dir.mkdir(parents=True, exist_ok=True)

    dataset_records = [
        {
            "question_id": "lme-q1",
            "question_type": "knowledge-update",
            "question": "city?",
            "answer": "Seattle",
            "answer_session_ids": ["session-new"],
            "haystack_session_ids": ["session-old", "session-new"],
            "haystack_dates": ["2024-01-01", "2024-02-01"],
            "haystack_sessions": [[{"role": "user", "content": "old"}], []],
        },
        {
            "question_id": "lme-q2",
            "question_type": "knowledge-update",
            "question": "city?",
            "answer": "Denver",
            "answer_session_ids": ["session-b"],
            "haystack_session_ids": ["session-a", "session-b"],
            "haystack_dates": ["2024-01-01", "2024-02-01"],
            "haystack_sessions": [[], []],
        },
    ]
    dataset.write_text(json.dumps(dataset_records), encoding="utf-8")
    ms_selection.write_text(
        json.dumps(
            {"schema_version": "longmemeval.slice-selection.v1", "sample_ids": ["lme-q1", "lme-q2"]}
        ),
        encoding="utf-8",
    )

    sample_actions = {"lme-q1": {"ADD": 2}, "lme-q2": {"ADD": 3, "MERGE": 1}}
    for sample_id, actions in sample_actions.items():
        (samples_dir / f"{sample_id}.json").write_text(
            json.dumps(
                {
                    "sample_id": sample_id,
                    "question_id": sample_id,
                    "question_type": "knowledge-update",
                    "ingestion": {
                        "etec": {
                            "actions": actions,
                            "memory_count": sum(actions.values()),
                        },
                    },
                }
            ),
            encoding="utf-8",
        )
        events = [
            {
                "memory_id": f"{sample_id}-ev-{i}",
                "metadata": {
                    "extractor_prompt_version": "event-extraction.v1",
                    "source_dataset": "longmemeval",
                    "source_sample_id": sample_id,
                },
            }
            for i in range(sum(actions.values()))
        ]
        (samples_dir / f"{sample_id}.extraction_snapshot.json").write_text(
            json.dumps(
                {
                    "snapshot_id": sample_id,
                    "conversation_id": sample_id,
                    "raw_turn_count": sum(actions.values()),
                    "event_count": sum(actions.values()),
                    "events": events,
                    "rejections": [],
                }
            ),
            encoding="utf-8",
        )

    # retrieval.jsonl: one row per (question, method) with raw turn ids that
    # cover each question's answer_session_ids (new_recall = 1.0).
    retrieval_rows = []
    for method in ("full", "event_no_etec", "etec", "vector_rag"):
        for record in dataset_records:
            answer_session = record["answer_session_ids"][0]
            retrieval_rows.append(
                {
                    "question_id": record["question_id"],
                    "sample_id": record["question_id"],
                    "method": method,
                    "packed_items": [
                        {
                            "item_id": f"{method}-{record['question_id']}",
                            "evidence_refs": [
                                {"raw_turn_id": f"{answer_session}:0"}
                            ],
                        }
                    ],
                }
            )
    (run_dir / "retrieval.jsonl").write_text(
        "\n".join(json.dumps(row) for row in retrieval_rows) + "\n",
        encoding="utf-8",
    )


def test_compute_metrics_from_online_reproduces_partial_schema(tmp_path: Path) -> None:
    run_dir = tmp_path / "ms-run"
    run_dir.mkdir()
    dataset = tmp_path / "dataset.json"
    ms_selection = tmp_path / "test20-ms.selection.json"
    out_path = tmp_path / "metrics.partial.json"
    _write_synthetic_online_run(run_dir, dataset, ms_selection)

    metrics = compute_metrics_from_online(
        source_run=run_dir,
        out_path=out_path,
        dataset=dataset,
        ms_selection=ms_selection,
    )

    # Top-level schema fields present.
    assert metrics["schema_version"] == "mechanism.evala.partial.v1"
    assert metrics["content_hash"].startswith("sha256:")
    assert "status" in metrics
    assert "data_source_decision" in metrics
    assert "r1_empirical_conclusion" in metrics

    # etec_actions.ms_8 comes from online ingestion.etec.actions (not replay).
    ms_8_actions = metrics["etec_actions"]["ms_8"]
    assert ms_8_actions["source"] == "online ingestion.etec.actions"
    assert ms_8_actions["add_count"] == 5  # 2 + 3
    assert ms_8_actions["merge_count"] == 1
    assert ms_8_actions["reject_count"] == 0
    assert ms_8_actions["supersede_count"] == 0
    assert ms_8_actions["per_sample"] == {
        "lme-q1": {"ADD": 2},
        "lme-q2": {"ADD": 3, "MERGE": 1},
    }
    assert ms_8_actions["total"] == {"ADD": 5, "MERGE": 1}
    assert metrics["etec_actions"]["new_24"]["status"].startswith("pending")

    # m3.ms_8 has new_recall_mean for every method (new side only; gold absent).
    m3_ms_8 = metrics["m3"]["ms_8"]
    assert "per_question" in m3_ms_8
    assert "methods" in m3_ms_8
    for method in ("full", "etec", "event_no_etec", "vector_rag"):
        summary = m3_ms_8["methods"][method]
        assert summary["new_recall_mean"] == 1.0
        assert summary["old_recall_mean"] is None
        assert summary["je_recall@8"] is None
    assert metrics["m3"]["new_24"]["status"].startswith("pending")
    assert metrics["m3"]["scope"]
    # caliber_limitation must be non-empty (gold old side NA documented).
    assert metrics["m3"]["caliber_limitation"]
    assert "old_recall" in metrics["m3"]["caliber_limitation"]

    # m1/m5 are pending gold annotation.
    assert metrics["m1"]["status"] == "na_pending_gold_annotation"
    assert metrics["m5"]["status"] == "na_pending_gold_annotation"

    # fact_slot_presence mirrors extraction snapshots.
    fact_slot = metrics["fact_slot_presence"]["ms_8"]
    assert fact_slot["source"] == "extraction snapshots"
    assert fact_slot["events"] == 6
    assert fact_slot["with_fact_slot"] == 0
    assert fact_slot["metadata_keys"] == [
        "extractor_prompt_version",
        "source_dataset",
        "source_sample_id",
    ]

    # The artifact file was written and re-loads to the same content_hash.
    written = json.loads(out_path.read_text(encoding="utf-8"))
    assert written["content_hash"] == metrics["content_hash"]


def test_compute_metrics_from_online_question_scope_with_mechanism40_selection(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "ms-run"
    run_dir.mkdir()
    dataset = tmp_path / "dataset.json"
    ms_selection = tmp_path / "test20-ms.selection.json"
    mechanism40_selection = tmp_path / "mechanism-evala.selection.json"
    out_path = tmp_path / "metrics.partial.json"
    _write_synthetic_online_run(run_dir, dataset, ms_selection)

    # Add three more KU records and the mechanism40 selection over them.
    existing = json.loads(dataset.read_text(encoding="utf-8"))
    new_records = [
        {
            "question_id": f"new-ku-{i}",
            "question_type": "knowledge-update",
            "question": "x?",
            "answer": "y",
            "answer_session_ids": ["s"],
            "haystack_session_ids": ["s"],
            "haystack_dates": ["2024-01-01"],
            "haystack_sessions": [[]],
        }
        for i in range(3)
    ]
    dataset.write_text(json.dumps([*existing, *new_records]), encoding="utf-8")
    mechanism40_selection.write_text(
        json.dumps(
            {
                "schema_version": "longmemeval.slice-selection.v1",
                "sample_ids": ["new-ku-0", "new-ku-1", "new-ku-2"],
            }
        ),
        encoding="utf-8",
    )

    metrics = compute_metrics_from_online(
        source_run=run_dir,
        out_path=out_path,
        dataset=dataset,
        ms_selection=ms_selection,
        mechanism40_selection=mechanism40_selection,
    )

    # ms KU still computed from the source run; new 24 KU counted as planned
    # scope but reported as pending in the per-block new_24 status.
    scope = metrics["question_scope"]
    assert scope["ms_ku_count"] == 2
    assert scope["new_ku_count"] == 3
    assert scope["total_32"] == 5
    assert metrics["etec_actions"]["new_24"]["status"].startswith("pending")
    assert metrics["m3"]["new_24"]["status"].startswith("pending")


def test_compute_m1_from_online_flags_coincidental_add_match(tmp_path: Path) -> None:
    """M1 from online actions: ADD gold pair matches ADD ETEC action coincidentally.

    Reproduces the 22d2cb42 case: when the sample is ADD-only (R1 default) and
    the gold action is also ADD, the match is flagged coincidental. A SUPERSEDE
    gold pair on an ADD-only sample is counted wrong with the R1 root cause.
    """
    run_dir = tmp_path / "ms-run"
    run_dir.mkdir()
    samples_dir = run_dir / "samples"
    samples_dir.mkdir()
    # Two ADD-only samples (mirrors ms 8 KU where every sample is ADD-only).
    for sid, n in [("add-q1", 5), ("supersede-q1", 8)]:
        (samples_dir / f"{sid}.json").write_text(
            json.dumps(
                {
                    "sample_id": sid,
                    "question_id": sid,
                    "ingestion": {"etec": {"actions": {"ADD": n}}},
                }
            ),
            encoding="utf-8",
        )

    gold_pairs = [
        GoldPair(
            question_id="add-q1",
            subject="me",
            attribute="guitar service location",
            old_value="",
            old_value_turn_ids=[],
            new_value="Seattle",
            new_value_turn_ids=["s:0"],
            t_q=datetime(2024, 1, 1, tzinfo=UTC),
            t_old=datetime(2024, 1, 1, tzinfo=UTC),
            gold_action=GoldAction.ADD,
        ),
        GoldPair(
            question_id="supersede-q1",
            subject="me",
            attribute="city",
            old_value="Austin",
            old_value_turn_ids=["s-old:0"],
            new_value="Seattle",
            new_value_turn_ids=["s-new:0"],
            t_q=datetime(2024, 2, 1, tzinfo=UTC),
            t_old=datetime(2024, 1, 1, tzinfo=UTC),
            gold_action=GoldAction.SUPERSEDE,
        ),
    ]
    out_path = tmp_path / "m1.json"
    report = compute_m1_from_online(run_dir, gold_pairs, out_path=out_path)

    assert report["n_found"] == 2
    assert report["n_correct"] == 1
    assert report["m1_accuracy"] == 0.5
    assert report["coincidental_correct_question_ids"] == ["add-q1"]
    add_row = report["per_question"]["add-q1"]
    assert add_row["etec_action"] == "ADD"
    assert add_row["gold_action"] == "ADD"
    assert add_row["correct"] is True
    assert add_row["coincidental_match"] is True
    sup_row = report["per_question"]["supersede-q1"]
    assert sup_row["etec_action"] == "ADD"
    assert sup_row["gold_action"] == "SUPERSEDE"
    assert sup_row["correct"] is False
    assert "R1_structural_fact_slot_absent" in sup_row["root_cause"]
    # Artifact written and reloads to the same hash.
    written = json.loads(out_path.read_text(encoding="utf-8"))
    assert written["content_hash"] == report["content_hash"]


def test_compute_m1_from_online_flags_indeterminable_on_mixed_actions(
    tmp_path: Path,
) -> None:
    """A sample with non-ADD actions cannot have its per-memory action resolved offline."""
    run_dir = tmp_path / "ms-run"
    run_dir.mkdir()
    samples_dir = run_dir / "samples"
    samples_dir.mkdir()
    (samples_dir / "mixed-q1.json").write_text(
        json.dumps(
            {
                "sample_id": "mixed-q1",
                "question_id": "mixed-q1",
                "ingestion": {"etec": {"actions": {"ADD": 10, "MERGE": 2}}},
            }
        ),
        encoding="utf-8",
    )
    gold_pairs = [
        GoldPair(
            question_id="mixed-q1",
            subject="me",
            attribute="city",
            old_value="Austin",
            old_value_turn_ids=["s-old:0"],
            new_value="Seattle",
            new_value_turn_ids=["s-new:0"],
            t_q=datetime(2024, 2, 1, tzinfo=UTC),
            t_old=datetime(2024, 1, 1, tzinfo=UTC),
            gold_action=GoldAction.SUPERSEDE,
        ),
    ]
    report = compute_m1_from_online(run_dir, gold_pairs)
    assert report["n_found"] == 0
    assert report["n_indeterminable_offline"] == 1
    assert report["indeterminable_question_ids"] == ["mixed-q1"]
    assert report["m1_accuracy"] is None
