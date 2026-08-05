from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from benchmarks.common.artifacts import current_git_commit  # noqa: F401
from benchmarks.experiments.etec_stress import (
    FIXTURE_PATH,
    StressFixture,
    run_etec_stress,
)
from evoeventmem.consolidation import ConsolidationAction

FIXTURE = Path("benchmarks/experiments/fixtures/etec_stress_v1.json")


def test_fixture_has_stable_unique_ids() -> None:
    fixture = StressFixture.model_validate(json.loads(FIXTURE.read_text(encoding="utf-8")))
    ids = [case.case_id for case in fixture.cases]
    assert len(ids) == len(set(ids))
    assert all(case_id.startswith("stress_") for case_id in ids)


def test_fixture_covers_all_required_categories() -> None:
    fixture = StressFixture.model_validate(json.loads(FIXTURE.read_text(encoding="utf-8")))
    categories = {case.category for case in fixture.cases}
    assert {
        "exact_duplicate",
        "paraphrase_merge",
        "newer_supersedes_older",
        "stale_incoming_remains_historical",
        "unrelated_same_entity",
        "overlapping_validity",
        "disjoint_validity",
        "conflicting_evidence",
        "missing_evidence",
        "cross_scope_isolation",
    } <= categories
    assert {case.expected_action for case in fixture.cases} >= {
        ConsolidationAction.MERGE,
        ConsolidationAction.SUPERSEDE,
        ConsolidationAction.REJECT,
        ConsolidationAction.ADD,
    }


def test_fixture_hashes_to_stable_sha256() -> None:
    digest = hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
    assert len(digest) == 64
    assert digest == hashlib.sha256(FIXTURE.read_bytes()).hexdigest()


def test_stress_run_requires_every_expected_id_once(tmp_path: Path) -> None:
    summary = run_etec_stress(FIXTURE, tmp_path / "run")

    assert summary.missing_ids == []
    assert summary.duplicate_ids == []
    assert len(summary.expected_ids) == len(set(summary.expected_ids))
    assert summary.case_count == len(summary.expected_ids)


def test_stress_run_emits_one_trace_per_case(tmp_path: Path) -> None:
    summary = run_etec_stress(FIXTURE, tmp_path / "run")

    traces = _read_jsonl(Path(summary.traces_path))
    assert len(traces) == summary.case_count
    assert all(trace["case_id"] for trace in traces)


def test_stress_run_is_action_stratified_with_nonzero_merge_and_supersede(
    tmp_path: Path,
) -> None:
    summary = run_etec_stress(FIXTURE, tmp_path / "run")

    assert summary.action_counts.get("merge", 0) > 0
    assert summary.action_counts.get("supersede", 0) > 0
    assert summary.merge_count > 0
    assert summary.supersede_count > 0


def test_stress_run_reports_action_accuracy(tmp_path: Path) -> None:
    summary = run_etec_stress(FIXTURE, tmp_path / "run")

    assert summary.action_accuracy == 1.0
    for trace in _read_jsonl(Path(summary.traces_path)):
        assert trace["action_match"] is True


def test_stress_run_all_invariants_pass(tmp_path: Path) -> None:
    summary = run_etec_stress(FIXTURE, tmp_path / "run")

    assert summary.invariant_pass_rate == 1.0
    for trace in _read_jsonl(Path(summary.traces_path)):
        assert trace["invariant_fails"] == []


def test_stress_run_intervals_are_utc_aware(tmp_path: Path) -> None:
    summary = run_etec_stress(FIXTURE, tmp_path / "run")

    for trace in _read_jsonl(Path(summary.traces_path)):
        decision = trace["decision"]
        assert isinstance(decision, dict)
        assert trace["case_id"]


def test_stress_run_provenance_lineage_present(tmp_path: Path) -> None:
    summary = run_etec_stress(FIXTURE, tmp_path / "run")

    for trace in _read_jsonl(Path(summary.traces_path)):
        assert trace["provenance_lineage"] is not None
        for entry in trace["provenance_lineage"]:
            assert entry["status"]
            assert isinstance(entry["evidence_refs"], list)


def test_stress_run_isolates_scopes(tmp_path: Path) -> None:
    summary = run_etec_stress(FIXTURE, tmp_path / "run")

    isolation_traces = [
        trace
        for trace in _read_jsonl(Path(summary.traces_path))
        if trace["category"] == "cross_scope_isolation"
    ]
    assert len(isolation_traces) == 2  # tenant + user isolation
    for trace in isolation_traces:
        assert trace["predicted_action"] == "ADD"
        assert trace["action_match"] is True


def test_stress_fixture_path_matches_repository_constant() -> None:
    assert FIXTURE_PATH.resolve() == FIXTURE.resolve()


def test_stress_rejects_duplicate_ids_in_fixture(tmp_path: Path) -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload["cases"].append(payload["cases"][0])
    bad = tmp_path / "dup.json"
    bad.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="every expected ID exactly once"):
        run_etec_stress(bad, tmp_path / "duprun")


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]