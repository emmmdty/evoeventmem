"""Unit tests for the S1b extraction-smoke statistics script.

Uses a static JSON payload (no real LLM run required) to verify that
``compute_stats`` returns the expected shape and field values for the ETEC
schema non-empty rates. The fixture deliberately mixes events with and without
``fact_slot`` / ``valid_from`` so the assertions exercise both numerator and
denominator branches.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from benchmarks.mechanism.extraction_smoke import compute_stats, format_stats, load_snapshot


def _event(
    *,
    fact_slot: str | None = "user.location",
    fact_value: str | None = "Seattle",
    valid_from: str | None = "2024-01-01T00:00:00Z",
    valid_to: str | None = None,
    multi_valued: bool | None = None,
) -> dict:
    metadata: dict = {"extractor_prompt_version": "event-extraction.v2"}
    if fact_slot is not None:
        metadata["fact_slot"] = fact_slot
    if fact_value is not None:
        metadata["fact_value"] = fact_value
    if valid_from is not None:
        metadata["valid_from"] = valid_from
    if valid_to is not None:
        metadata["valid_until"] = valid_to
    if multi_valued is not None:
        metadata["multi_valued"] = multi_valued
    event: dict = {
        "memory_id": "mem-1",
        "content": "User lives in Seattle.",
        "memory_kind": "event",
        "metadata": metadata,
    }
    if valid_from is not None:
        event["valid_from"] = valid_from
    if valid_to is not None:
        event["valid_to"] = valid_to
    return event


def _snapshot(events: list[dict], sample_id: str = "sample-a") -> dict:
    return {
        "schema_version": "longmemeval.snapshot.v1",
        "snapshot_id": f"sha256:{sample_id}",
        "conversation_id": sample_id,
        "event_count": len(events),
        "events": events,
        "raw_turn_count": 10,
        "rejections": [],
        "extractor": {"model_id": "mimo-v2.5", "prompt_version": "event-extraction.v2"},
    }


def test_compute_stats_counts_non_empty_fields() -> None:
    snapshots = [
        _snapshot(
            [
                _event(),
                _event(fact_slot=None, fact_value=None, valid_from=None),
                _event(
                    fact_slot="user.location",
                    fact_value="Portland",
                    valid_from="2024-02-01T00:00:00Z",
                    valid_to="2024-03-01T00:00:00Z",
                ),
            ],
            sample_id="sample-a",
        ),
        _snapshot(
            [
                _event(fact_slot="user.job", fact_value="Engineer"),
            ],
            sample_id="sample-b",
        ),
    ]
    stats = compute_stats(snapshots)
    assert stats["sample_count"] == 2
    assert stats["total_events"] == 4
    # fact_slot present in 3/4 events
    assert stats["fact_slot_present"] == 3
    assert stats["fact_slot_rate"] == pytest.approx(0.75)
    # fact_value same as fact_slot
    assert stats["fact_value_present"] == 3
    # metadata.valid_from in 3/4 events
    assert stats["valid_from_meta_present"] == 3
    # top-level valid_from mirrored for the same 3 events
    assert stats["valid_from_top_present"] == 3
    # one event carries valid_until / valid_to
    assert stats["valid_until_meta_present"] == 1
    assert stats["valid_until_top_present"] == 1
    # multi_valued should be 0 — S1a did not populate it
    assert stats["multi_valued_present"] == 0
    assert stats["multi_valued_rate"] == 0.0
    # sample-a has 2 events sharing fact_slot=user.location with distinct
    # values ("Seattle" and "Portland") -> 1 unordered pair
    assert stats["distinct_fact_value_pairs_unchecked"] == 1
    per_sample = stats["per_sample"]
    assert [s["sample_id"] for s in per_sample] == ["sample-a", "sample-b"]
    sample_a = per_sample[0]
    assert sample_a["event_count"] == 3
    assert sample_a["fact_slot_present"] == 2
    assert sample_a["fact_slot_rate"] == pytest.approx(2 / 3)


def test_compute_stats_handles_empty_snapshot() -> None:
    stats = compute_stats([])
    assert stats["sample_count"] == 0
    assert stats["total_events"] == 0
    assert stats["fact_slot_rate"] == 0.0
    assert stats["per_sample"] == []


def test_format_stats_includes_key_lines() -> None:
    snapshots = [_snapshot([_event(), _event(fact_slot=None, valid_from=None)])]
    stats = compute_stats(snapshots)
    text = format_stats(stats)
    assert "fact_slot non-empty" in text
    assert "valid_until" in text
    assert "multi_valued=True" in text
    assert "50.0%" in text  # 1/2 events have fact_slot


def test_load_snapshot_raises_when_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_snapshot(tmp_path / "missing.json")
