"""S1b extraction smoke statistics: fact_slot / valid_from / valid_until non-empty rates.

Reads the combined ``extraction_snapshot.json`` written by the LongMemEval
runner and computes non-empty rates for the ETEC schema fields introduced in
Stage 1a (``fact_slot`` / ``fact_value`` / ``valid_from`` / ``valid_until``)
plus a coarse count of pairs that share a fact slot but disagree on fact
value (potential SUPERSEDE candidates that have NOT walked through
consolidation). The script is read-only: it never touches the consolidation
layer, never invokes an embedding model, and never mutates ``runs/``.

CLI::

    uv run python -m benchmarks.mechanism.extraction_smoke runs/s1b/smoke5

The argument is the run directory that contains ``extraction_snapshot.json``.
If the combined snapshot is missing the script exits non-zero with a clear
message (the run has not produced extraction artifacts yet).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any


def load_snapshot(path: Path) -> list[dict[str, Any]]:
    """Load the combined extraction snapshot file as a list of per-sample records.

    The file is written by ``_write_run_root_artifacts`` as a JSON array; each
    element is one sample's extraction snapshot (``events`` list + metadata).
    """
    if not path.exists():
        raise FileNotFoundError(
            f"extraction snapshot not found at {path}; run the LongMemEval "
            "runner with --extraction-only first"
        )
    payload = json.loads(path.read_bytes())
    if not isinstance(payload, list):
        raise TypeError(
            f"expected a JSON array in {path}, got {type(payload).__name__}"
        )
    return payload


def _event_metadata(event: dict[str, Any]) -> dict[str, Any]:
    meta = event.get("metadata")
    if not isinstance(meta, dict):
        return {}
    return meta


def compute_stats(snapshots: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Compute ETEC schema non-empty rates across all events in ``snapshots``.

    Pure function — no I/O. The returned dict has stable keys suitable for JSON
    serialization and human formatting via :func:`format_stats`.
    """
    sample_stats: list[dict[str, Any]] = []
    total_events = 0
    fact_slot_present = 0
    fact_value_present = 0
    valid_from_meta_present = 0
    valid_from_top_present = 0
    valid_until_meta_present = 0
    valid_until_top_present = 0
    multi_valued_present = 0
    per_slot_value_pairs = 0

    for snapshot in snapshots:
        sample_id = snapshot.get("conversation_id") or snapshot.get("snapshot_id") or "<unknown>"
        events = snapshot.get("events") or []
        if not isinstance(events, list):
            events = []
        sample_total = len(events)
        sample_slot = 0
        sample_vf = 0
        sample_vt = 0
        slot_value_groups: dict[str, set[str]] = {}

        for event in events:
            if not isinstance(event, dict):
                continue
            meta = _event_metadata(event)
            slot = meta.get("fact_slot")
            value = meta.get("fact_value")
            if slot is not None:
                fact_slot_present += 1
                sample_slot += 1
            if value is not None:
                fact_value_present += 1
            if meta.get("valid_from") is not None:
                valid_from_meta_present += 1
            if event.get("valid_from") is not None:
                valid_from_top_present += 1
                sample_vf += 1
            if meta.get("valid_until") is not None:
                valid_until_meta_present += 1
            if event.get("valid_to") is not None:
                valid_until_top_present += 1
                sample_vt += 1
            if meta.get("multi_valued") is True:
                multi_valued_present += 1
            if isinstance(slot, str) and isinstance(value, str):
                slot_value_groups.setdefault(slot, set()).add(value)

        # Count unordered (source, target) pairs that share a fact slot but
        # declare distinct fact values. These are the raw SUPERSEDE candidates
        # before consolidation runs; this count is NOT the SUPERSEDE trigger
        # count and does not run the four-gate conjunction.
        for values in slot_value_groups.values():
            if len(values) < 2:
                continue
            n = len(values)
            per_slot_value_pairs += n * (n - 1) // 2

        sample_stats.append(
            {
                "sample_id": sample_id,
                "event_count": sample_total,
                "fact_slot_present": sample_slot,
                "fact_slot_rate": (sample_slot / sample_total) if sample_total else 0.0,
                "valid_from_present": sample_vf,
                "valid_from_rate": (sample_vf / sample_total) if sample_total else 0.0,
                "valid_until_present": sample_vt,
                "valid_until_rate": (sample_vt / sample_total) if sample_total else 0.0,
            }
        )
        total_events += sample_total

    def rate(n: int) -> float:
        return (n / total_events) if total_events else 0.0

    return {
        "sample_count": len(snapshots),
        "total_events": total_events,
        "fact_slot_present": fact_slot_present,
        "fact_slot_rate": rate(fact_slot_present),
        "fact_value_present": fact_value_present,
        "fact_value_rate": rate(fact_value_present),
        "valid_from_meta_present": valid_from_meta_present,
        "valid_from_meta_rate": rate(valid_from_meta_present),
        "valid_from_top_present": valid_from_top_present,
        "valid_from_top_rate": rate(valid_from_top_present),
        "valid_until_meta_present": valid_until_meta_present,
        "valid_until_meta_rate": rate(valid_until_meta_present),
        "valid_until_top_present": valid_until_top_present,
        "valid_until_top_rate": rate(valid_until_top_present),
        "multi_valued_present": multi_valued_present,
        "multi_valued_rate": rate(multi_valued_present),
        "distinct_fact_value_pairs_unchecked": per_slot_value_pairs,
        "per_sample": sample_stats,
    }


def format_stats(stats: dict[str, Any]) -> str:
    """Render the stats dict as a human-readable multi-line string."""
    lines: list[str] = []
    lines.append("=== S1b extraction smoke statistics ===")
    lines.append(
        f"samples: {stats['sample_count']}  total events: {stats['total_events']}"
    )
    lines.append(
        f"fact_slot non-empty:        {stats['fact_slot_present']:>6} / "
        f"{stats['total_events']} = {stats['fact_slot_rate'] * 100:5.1f}%"
    )
    lines.append(
        f"fact_value non-empty:       {stats['fact_value_present']:>6} / "
        f"{stats['total_events']} = {stats['fact_value_rate'] * 100:5.1f}%"
    )
    lines.append(
        f"metadata.valid_from:        {stats['valid_from_meta_present']:>6} / "
        f"{stats['total_events']} = {stats['valid_from_meta_rate'] * 100:5.1f}%"
    )
    lines.append(
        f"top-level valid_from:       {stats['valid_from_top_present']:>6} / "
        f"{stats['total_events']} = {stats['valid_from_top_rate'] * 100:5.1f}%"
    )
    lines.append(
        f"metadata.valid_until:       {stats['valid_until_meta_present']:>6} / "
        f"{stats['total_events']} = {stats['valid_until_meta_rate'] * 100:5.1f}%"
    )
    lines.append(
        f"top-level valid_to:         {stats['valid_until_top_present']:>6} / "
        f"{stats['total_events']} = {stats['valid_until_top_rate'] * 100:5.1f}%"
    )
    lines.append(
        f"metadata.multi_valued=True: {stats['multi_valued_present']:>5} / "
        f"{stats['total_events']} = {stats['multi_valued_rate'] * 100:5.1f}%"
    )
    lines.append(
        f"distinct fact_value pairs (pre-consolidation): "
        f"{stats['distinct_fact_value_pairs_unchecked']}"
    )
    lines.append("--- per sample ---")
    for sample in stats["per_sample"]:
        lines.append(
            f"  {sample['sample_id']}: events={sample['event_count']:>4} "
            f"fact_slot={sample['fact_slot_rate'] * 100:5.1f}% "
            f"valid_from={sample['valid_from_rate'] * 100:5.1f}% "
            f"valid_until={sample['valid_until_rate'] * 100:5.1f}%"
        )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compute ETEC schema non-empty rates from an extraction snapshot."
    )
    parser.add_argument(
        "run_dir",
        type=Path,
        help="Run directory containing extraction_snapshot.json",
    )
    parser.add_argument(
        "--snapshot-name",
        default="extraction_snapshot.json",
        help="Combined snapshot file name (default: extraction_snapshot.json).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of human-readable text.",
    )
    args = parser.parse_args(argv)

    snapshot_path = args.run_dir / args.snapshot_name
    try:
        snapshots = load_snapshot(snapshot_path)
    except (FileNotFoundError, TypeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    stats = compute_stats(snapshots)
    if args.json:
        print(json.dumps(stats, indent=2, sort_keys=True))
    else:
        print(format_stats(stats))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
