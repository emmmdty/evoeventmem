"""Quantify extraction stability across repeated runs (S7 W2).

For a set of LongMemEval extraction run directories produced from identical
inputs with different sampling states, compute per-sample:

- event-set Jaccard similarity for every run pair, matching events by
  normalized ``content`` (lowercased, whitespace-collapsed);
- coefficient of variation (CV) of per-run event counts.

An overall ``stability_verdict`` is derived from the mean pairwise Jaccard
across all samples and pairs: ``stable`` (>= 0.80), ``moderate`` (>= 0.50),
otherwise ``unstable``.

Usage::

    uv run python scripts/extraction_variance.py \
        --run runs/diagnostic/s7-extract-variance-r1 \
        --run runs/diagnostic/s7-extract-variance-r2 \
        --run runs/diagnostic/s7-extract-variance-r3 \
        --out runs/analysis/extraction_variance.json
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from datetime import UTC, datetime
from itertools import combinations
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "extraction_variance.v1"
STABLE_THRESHOLD = 0.80
MODERATE_THRESHOLD = 0.50


def _normalize_content(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def _load_event_sets(run_dir: Path) -> dict[str, set[str]]:
    samples_dir = run_dir / "samples"
    if not samples_dir.is_dir():
        raise FileNotFoundError(f"missing samples directory: {samples_dir}")
    sets: dict[str, set[str]] = {}
    for path in sorted(samples_dir.glob("*.extraction_snapshot.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        sample_id = str(payload.get("conversation_id") or path.name.split(".")[0])
        contents = {
            _normalize_content(str(event.get("content", "")))
            for event in payload.get("events", [])
            if event.get("content")
        }
        sets[sample_id] = contents
    return sets


def _jaccard(a: set[str], b: set[str]) -> float | None:
    union = a | b
    if not union:
        return None
    return len(a & b) / len(union)


def _coefficient_of_variation(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    mean = statistics.mean(values)
    if mean == 0:
        return 0.0 if max(values) == 0 else None
    return statistics.stdev(values) / mean


def _verdict(mean_jaccard: float | None) -> str:
    if mean_jaccard is None:
        return "unstable"
    if mean_jaccard >= STABLE_THRESHOLD:
        return "stable"
    if mean_jaccard >= MODERATE_THRESHOLD:
        return "moderate"
    return "unstable"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run",
        action="append",
        required=True,
        type=Path,
        help="run directory containing samples/*.extraction_snapshot.json",
    )
    parser.add_argument("--out", type=Path, default=Path("runs/analysis/extraction_variance.json"))
    args = parser.parse_args(argv)

    if len(args.run) < 2:
        parser.error("at least two --run directories are required")

    per_run_sets = {str(run): _load_event_sets(run) for run in args.run}
    common_ids = sorted(set.intersection(*(set(s.keys()) for s in per_run_sets.values())))
    if not common_ids:
        print("ERROR: no samples common to all runs", file=sys.stderr)
        return 1

    samples_report: dict[str, dict[str, Any]] = {}
    all_pairwise_jaccards: list[float] = []
    all_cvs: list[float] = []
    for sample_id in common_ids:
        run_to_events = {name: sets[sample_id] for name, sets in per_run_sets.items()}
        pair_jaccards: dict[str, float | None] = {}
        for (name_a, events_a), (name_b, events_b) in combinations(run_to_events.items(), 2):
            pair_jaccards[f"{Path(name_a).name}~{Path(name_b).name}"] = _jaccard(
                events_a, events_b
            )
        numeric_jaccards = [j for j in pair_jaccards.values() if j is not None]
        counts = [float(len(events)) for events in run_to_events.values()]
        cv = _coefficient_of_variation(counts)
        mean_j = statistics.mean(numeric_jaccards) if numeric_jaccards else None
        samples_report[sample_id] = {
            "event_counts_by_run": {
                Path(name).name: len(events) for name, events in run_to_events.items()
            },
            "pairwise_jaccard": pair_jaccards,
            "mean_jaccard": mean_j,
            "event_count_cv": cv,
        }
        all_pairwise_jaccards.extend(numeric_jaccards)
        if cv is not None:
            all_cvs.append(cv)

    overall_mean_jaccard = (
        statistics.mean(all_pairwise_jaccards) if all_pairwise_jaccards else None
    )
    report = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "runs": [str(run) for run in args.run],
        "sample_count": len(common_ids),
        "samples": samples_report,
        "overall": {
            "mean_pairwise_jaccard": overall_mean_jaccard,
            "mean_event_count_cv": statistics.mean(all_cvs) if all_cvs else None,
            "stable_threshold": STABLE_THRESHOLD,
            "moderate_threshold": MODERATE_THRESHOLD,
        },
        "stability_verdict": _verdict(overall_mean_jaccard),
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {args.out}")
    print(f"sample_count={len(common_ids)} verdict={report['stability_verdict']}")
    if overall_mean_jaccard is not None:
        print(f"mean_pairwise_jaccard={overall_mean_jaccard:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
