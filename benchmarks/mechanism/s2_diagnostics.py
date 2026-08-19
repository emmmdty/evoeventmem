"""S2 consolidated diagnostics: emit the v1-vs-v2 / ETEC / fact_slot /
sentinel / reachability report in one shot.

The S2 spec (``docs/S2-execution-prompt.md`` Step 4 lines 197-269) lists
six diagnostic queries to run on the v2-factslot run dir:

    (a) ETEC actions distribution + SUPERSEDE count
    (b) fact_slot / valid_from / valid_until / sentinel rates
    (c) sentinel rate + effective fact_slot rate (excluding sentinels)
    (d) reachability test on the v2 extraction snapshot
    (e) v1 vs v2 EM comparison table
    (f) replay/online consistency check

This module consolidates (a)–(e) into a single report. (d) and (f) are
delegated to existing modules (the S1b/S1c reachability test and
``benchmarks.mechanism.replay``) which the S2 acceptance test invokes
separately.

The script is read-only — it never mutates ``runs/``. It exits non-zero
if the v2 run dir is missing or the v2 summary.json is malformed.

CLI::

    uv run python -m benchmarks.mechanism.s2_diagnostics
    uv run python -m benchmarks.mechanism.s2_diagnostics \\
        --run-dir runs/publication/m13-longmemeval-test50-mimo-v2-factslot \\
        --v1-run-dir runs/publication/m13-longmemeval-test50-mimo \\
        --output runs/publication/m13-longmemeval-test50-mimo-v2-factslot/s2_diagnostics_report.md
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

DEFAULT_S2_RUN_DIR = Path("runs/publication/m13-longmemeval-test50-mimo-v2-factslot")
DEFAULT_V1_RUN_DIR = Path("runs/publication/m13-longmemeval-test50-mimo")
DEFAULT_OUTPUT_PATH = Path("s2_diagnostics_report.md")
EXPECTED_PROMPT_VERSION = "event-extraction.v3"
SENTINEL_LITERAL = "none"


def _load_summary(run_dir: Path) -> dict[str, Any]:
    summary_path = run_dir / "summary.json"
    if not summary_path.exists():
        raise FileNotFoundError(
            f"summary.json missing at {summary_path}; the run has not "
            "produced a summary yet. Run the launcher + finalize first."
        )
    return json.loads(summary_path.read_text(encoding="utf-8"))


def _load_extraction_snapshot(run_dir: Path) -> list[dict[str, Any]]:
    snapshot_path = run_dir / "extraction_snapshot.json"
    if not snapshot_path.exists():
        raise FileNotFoundError(
            f"extraction_snapshot.json missing at {snapshot_path}; "
            "the run has not produced extraction artifacts yet."
        )
    payload = json.loads(snapshot_path.read_bytes())
    if not isinstance(payload, list):
        raise TypeError(
            f"expected a JSON array in {snapshot_path}, got "
            f"{type(payload).__name__}"
        )
    return payload


def _iter_sample_records(run_dir: Path) -> list[tuple[Path, dict[str, Any]]]:
    samples_dir = run_dir / "samples"
    if not samples_dir.exists():
        raise FileNotFoundError(f"samples/ dir missing at {samples_dir}")
    out: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(samples_dir.glob("*.json")):
        if path.name.endswith(".extraction_snapshot.json"):
            continue
        out.append((path, json.loads(path.read_text(encoding="utf-8"))))
    return out


def _count_retrieval_jsonl_lines(run_dir: Path) -> int:
    retrieval_path = run_dir / "retrieval.jsonl"
    if not retrieval_path.exists():
        return 0
    return sum(1 for _ in retrieval_path.read_text(encoding="utf-8").splitlines() if _.strip())


def _etec_action_counts(run_dir: Path) -> tuple[Counter, list[tuple[str, Counter]]]:
    per_sample: list[tuple[str, Counter]] = []
    total = Counter()
    for _path, record in _iter_sample_records(run_dir):
        sample_id = record.get("sample_id") or record.get("question_id") or _path.stem
        actions = (
            record.get("ingestion", {}).get("etec", {}).get("actions") or {}
        )
        if not isinstance(actions, dict):
            continue
        counter = Counter({k: int(v) for k, v in actions.items()})
        per_sample.append((sample_id, counter))
        total.update(counter)
    return total, per_sample


def _sentinel_breakdown(snapshot: list[dict[str, Any]]) -> dict[str, Any]:
    total_events = 0
    total_sentinel = 0
    total_real = 0
    total_valid_from = 0
    total_valid_until = 0
    per_sample: list[dict[str, Any]] = []
    for entry in snapshot:
        sample_id = (
            entry.get("conversation_id")
            or entry.get("snapshot_id")
            or "<unknown>"
        )
        events = entry.get("events") or []
        if not isinstance(events, list):
            events = []
        sentinel = 0
        real = 0
        valid_from_present = 0
        valid_until_present = 0
        for event in events:
            if not isinstance(event, dict):
                continue
            meta = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
            slot = meta.get("fact_slot")
            if slot == SENTINEL_LITERAL:
                sentinel += 1
            elif slot not in (None, "", SENTINEL_LITERAL):
                real += 1
            if meta.get("valid_from") is not None:
                valid_from_present += 1
            if meta.get("valid_until") is not None or event.get("valid_to") is not None:
                valid_until_present += 1
        sample_total = len(events)
        total_events += sample_total
        total_sentinel += sentinel
        total_real += real
        total_valid_from += valid_from_present
        total_valid_until += valid_until_present
        per_sample.append(
            {
                "sample_id": sample_id,
                "events": sample_total,
                "real_fact_slot": real,
                "sentinel": sentinel,
                "valid_from": valid_from_present,
                "valid_until": valid_until_present,
                "effective_rate": (real / sample_total) if sample_total else 0.0,
                "sentinel_rate": (sentinel / sample_total) if sample_total else 0.0,
                "valid_from_rate": (valid_from_present / sample_total) if sample_total else 0.0,
                "valid_until_rate": (valid_until_present / sample_total) if sample_total else 0.0,
            }
        )
    return {
        "total_events": total_events,
        "total_real": total_real,
        "total_sentinel": total_sentinel,
        "total_valid_from": total_valid_from,
        "total_valid_until": total_valid_until,
        "effective_rate": (total_real / total_events) if total_events else 0.0,
        "sentinel_rate": (total_sentinel / total_events) if total_events else 0.0,
        "valid_from_rate": (total_valid_from / total_events) if total_events else 0.0,
        "valid_until_rate": (total_valid_until / total_events) if total_events else 0.0,
        "per_sample": per_sample,
    }


def _v3_prompt_breakdown(snapshot: list[dict[str, Any]]) -> dict[str, Any]:
    total_events = 0
    v3_events = 0
    per_sample: list[dict[str, Any]] = []
    for entry in snapshot:
        sample_id = (
            entry.get("conversation_id")
            or entry.get("snapshot_id")
            or "<unknown>"
        )
        events = entry.get("events") or []
        sample_total = 0
        sample_v3 = 0
        for event in events:
            if not isinstance(event, dict):
                continue
            sample_total += 1
            total_events += 1
            meta = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
            if meta.get("extractor_prompt_version") == EXPECTED_PROMPT_VERSION:
                sample_v3 += 1
                v3_events += 1
        per_sample.append(
            {
                "sample_id": sample_id,
                "events": sample_total,
                "v3_events": sample_v3,
            }
        )
    return {
        "total_events": total_events,
        "v3_events": v3_events,
        "v3_rate": (v3_events / total_events) if total_events else 0.0,
        "per_sample": per_sample,
    }


def _format_per_sample_table(per_sample: list[dict[str, Any]]) -> str:
    header = (
        f"| {'sample':<14} | {'events':>6} | {'real':>5} | {'sentinel':>9} "
        f"| {'vf':>4} | {'vt':>4} | {'eff%':>5} | {'snt%':>5} | {'vf%':>5} | {'vt%':>5} |"
    )
    sep = "|" + "|".join(["---"] * 10) + "|"
    rows = [
        (
            f"| {row['sample_id']:<14} | {row['events']:>6} | "
            f"{row['real_fact_slot']:>5} | {row['sentinel']:>9} | "
            f"{row['valid_from']:>4} | {row['valid_until']:>4} | "
            f"{row['effective_rate'] * 100:>5.1f} | "
            f"{row['sentinel_rate'] * 100:>5.1f} | "
            f"{row['valid_from_rate'] * 100:>5.1f} | "
            f"{row['valid_until_rate'] * 100:>5.1f} |"
        )
        for row in per_sample
    ]
    return "\n".join([header, sep, *rows])


def build_report(s2_run_dir: Path, v1_run_dir: Path) -> str:
    """Build the consolidated S2 diagnostic report as markdown."""
    s2_summary = _load_summary(s2_run_dir)
    s2_snapshot = _load_extraction_snapshot(s2_run_dir)
    total_actions, per_sample_actions = _etec_action_counts(s2_run_dir)
    sentinel_breakdown = _sentinel_breakdown(s2_snapshot)
    v3_breakdown = _v3_prompt_breakdown(s2_snapshot)
    retrieval_lines = _count_retrieval_jsonl_lines(s2_run_dir)

    # v1 vs v2 EM comparison
    v1_em_table: list[dict[str, Any]] = []
    v1_reader: str | None = None
    v2_reader: str | None = s2_summary.get("reader_model")
    same_model = False
    if v1_run_dir.exists():
        try:
            v1_summary = _load_summary(v1_run_dir)
            v1_reader = v1_summary.get("reader_model")
            same_model = v1_reader == v2_reader
            for method in (
                "no_memory",
                "full_context",
                "vector_rag",
                "event_no_etec",
                "etec",
                "full",
            ):
                v1_em = v1_summary.get("methods", {}).get(method, {}).get("exact_match")
                v2_em = s2_summary.get("methods", {}).get(method, {}).get("exact_match")
                delta = (
                    v2_em - v1_em
                    if v1_em is not None and v2_em is not None
                    else None
                )
                v1_em_table.append(
                    {
                        "method": method,
                        "v1_em": v1_em,
                        "v2_em": v2_em,
                        "delta": delta,
                    }
                )
        except FileNotFoundError:
            v1_em_table = []

    supersede_count = total_actions.get("SUPERSEDE", 0)
    validation = s2_summary.get("sample_validation") or {}
    expected_count = validation.get("expected_sample_count")
    completed_count = validation.get("completed_sample_count")
    missing = validation.get("missing_sample_ids") or []
    valid_flag = validation.get("valid")

    # Build the markdown report
    lines: list[str] = []
    lines.append("# S2 v2-factslot diagnostic report")
    lines.append("")
    lines.append(f"- **Run dir**: `{s2_run_dir}`")
    lines.append(f"- **v1 baseline dir**: `{v1_run_dir}`")
    lines.append(f"- **Reader model**: v1=`{v1_reader}`, v2=`{v2_reader}`, "
                 f"same_model=`{same_model}`")
    if not same_model:
        lines.append("  - ⚠ v1 vs v2 cross-model comparison is forbidden (N8)")
    lines.append("")

    lines.append("## Hard gates (spec acceptance criteria)")
    lines.append("")
    lines.append(f"- FINALIZED.json: "
                 f"{'OK' if (s2_run_dir / 'finalized' / 'FINALIZED.json').exists() else 'MISSING'}")
    lines.append(f"- Sample validation: expected={expected_count}, "
                 f"completed={completed_count}, missing={len(missing)}, valid={valid_flag}")
    lines.append(f"- retrieval.jsonl lines: {retrieval_lines} (target: 200)")
    lines.append(f"- ETEC actions reported: {dict(total_actions)} (total={total_actions.total()})")
    lines.append(f"- v3 prompt coverage: {v3_breakdown['v3_events']} / "
                 f"{v3_breakdown['total_events']} = "
                 f"{v3_breakdown['v3_rate'] * 100:.1f}%")
    lines.append("")

    lines.append("## Soft gates (informational; failure routes to S3/S5)")
    lines.append("")
    eff_rate = sentinel_breakdown["effective_rate"]
    snt_rate = sentinel_breakdown["sentinel_rate"]
    vf_rate = sentinel_breakdown["valid_from_rate"]
    vt_rate = sentinel_breakdown["valid_until_rate"]
    lines.append(f"- fact_slot effective rate (excl. sentinels): "
                 f"{sentinel_breakdown['total_real']} / "
                 f"{sentinel_breakdown['total_events']} = "
                 f"{eff_rate * 100:.1f}% (spec floor: 50%)")
    lines.append(f"- valid_from rate: {sentinel_breakdown['total_valid_from']} / "
                 f"{sentinel_breakdown['total_events']} = "
                 f"{vf_rate * 100:.1f}% (spec floor: 50%)")
    lines.append(f"- valid_until rate: {sentinel_breakdown['total_valid_until']} / "
                 f"{sentinel_breakdown['total_events']} = "
                 f"{vt_rate * 100:.1f}% (no spec floor; informational)")
    lines.append(f"- sentinel rate: {sentinel_breakdown['total_sentinel']} / "
                 f"{sentinel_breakdown['total_events']} = "
                 f"{snt_rate * 100:.1f}% (spec ceiling: 20%)")
    lines.append(f"- SUPERSEDE count: {supersede_count} "
                 f"(v1 was 0; S2 measures the v2 count; "
                 f"0 → S5 path A, >0 → S3)")
    lines.append("")

    lines.append("## Per-sample breakdown")
    lines.append("")
    lines.append(_format_per_sample_table(sentinel_breakdown["per_sample"]))
    lines.append("")

    lines.append("## ETEC actions per sample (first 15)")
    lines.append("")
    lines.append("| sample | actions |")
    lines.append("|---|---|")
    for sample_id, actions in per_sample_actions[:15]:
        lines.append(f"| {sample_id} | {dict(actions)} |")
    if len(per_sample_actions) > 15:
        lines.append(f"... ({len(per_sample_actions) - 15} more)")
    lines.append("")

    if v1_em_table:
        lines.append("## v1 vs v2 EM comparison (same model: "
                     f"{'YES' if same_model else 'NO'})")
        lines.append("")
        lines.append("| method | v1 EM | v2 EM | Δ |")
        lines.append("|---|---|---|---|")
        for row in v1_em_table:
            v1_str = f"{row['v1_em']:.2f}" if row["v1_em"] is not None else "n/a"
            v2_str = f"{row['v2_em']:.2f}" if row["v2_em"] is not None else "n/a"
            delta_str = (f"{row['delta']:+.2f}"
                         if row["delta"] is not None else "n/a")
            lines.append(f"| {row['method']} | {v1_str} | {v2_str} | "
                         f"{delta_str} |")
        lines.append("")
        lines.append("_No pre-declared expectation (negative-result framework)._")
        lines.append("")

    lines.append("## Next-step routing")
    lines.append("")
    if supersede_count > 0:
        lines.append("- SUPERSEDE > 0 → run S3 (QEMR diagnosis + M2 stale-judge)")
    else:
        lines.append("- SUPERSEDE = 0 → pivot to S5 path A (negative-result paper); "
                     "S3 still runs to explain QEMR failure")
    if snt_rate >= 0.20:
        lines.append("- sentinel rate ≥ 20% → do NOT re-tune the prompt in S2; "
                     "route to S3/S5 decision")
    if eff_rate < 0.50:
        lines.append("- fact_slot effective rate < 50% → spec fallback "
                     "(re-evaluate threshold on 50 questions)")
    if retrieval_lines != 200:
        lines.append(f"- retrieval.jsonl line count = {retrieval_lines} (not 200) → "
                     "some samples did not complete retrieval; rerun --resume")
    lines.append("")

    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build the consolidated S2 v2-factslot diagnostic report."
    )
    parser.add_argument(
        "--run-dir",
        type=Path,
        default=DEFAULT_S2_RUN_DIR,
        help=f"v2-factslot run directory (default: {DEFAULT_S2_RUN_DIR})",
    )
    parser.add_argument(
        "--v1-run-dir",
        type=Path,
        default=DEFAULT_V1_RUN_DIR,
        help=f"v1 baseline run directory (default: {DEFAULT_V1_RUN_DIR})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write the report to this path. Default: stdout.",
    )
    args = parser.parse_args(argv)

    if not args.run_dir.exists():
        print(
            f"error: v2 run dir {args.run_dir} does not exist. "
            "Run scripts/run50-parallel-v2-factslot.sh first.",
            file=sys.stderr,
        )
        return 1

    try:
        report = build_report(args.run_dir, args.v1_run_dir)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report, encoding="utf-8")
        print(f"S2 diagnostic report written to {args.output}", file=sys.stderr)
    else:
        print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
