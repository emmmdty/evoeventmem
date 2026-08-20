"""S3 Step 1: Query router confusion-matrix diagnosis (N9 scope).

Maps LongMemEval ``question_type`` gold labels to the ``QueryIntent`` enum,
runs the deterministic ``QueryRouter`` (policy ``query-router.rules.v1``) on
each question, and emits a gold × predicted confusion matrix plus per-class
precision/recall/F1, misclassified samples, and rule-modification suggestions.

Scope boundary (``docs/S3-execution-prompt.md`` Step 1, lines 172-201):

- This module is **read-only** — it never mutates ``src/evoeventmem/router.py``.
- It only **produces** a confusion matrix + suggestions; rule edits are a
  separate post-S3 task (N9).
- The router is a pure deterministic function; classifying all 500
  LongMemEval questions does **not** run the benchmark pipeline (no retrieval,
  no reader LLM) and is therefore outside the "不跑 500 题" scope guard
  (which restricts the full benchmark run, not router classification).

The 50-question v2 test set is entirely ``single-session-user`` (gold =
SEMANTIC for all 50), so the 50-question matrix is a degenerate single-class
slice. The full 500-question classification is emitted as a supplement to
expose the router's behaviour across all six LongMemEval question types.

CLI::

    uv run python -m benchmarks.mechanism.router_diagnosis \\
        --source-run runs/publication/m13-longmemeval-test50-mimo-v2-factslot
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from benchmarks.common.normalization import iter_longmemeval_records
from benchmarks.mechanism.s2_diagnostics import _load_summary
from evoeventmem.router import POLICY_NAME, QueryIntent, QueryRouter

DEFAULT_SOURCE_RUN = Path("runs/publication/m13-longmemeval-test50-mimo-v2-factslot")
DEFAULT_DATASET = Path("data/raw/longmemeval/longmemeval_s_cleaned.json")
DEFAULT_OUTPUT = Path("router_diagnosis_report.md")

# LongMemEval question_type → QueryIntent gold-label mapping.
#
# Rationale (LongMemEval arXiv:2410.10813 §4 question categories):
# - single-session-user / -assistant / -preference: factual attribute lookups
#   scoped to one session → SEMANTIC (the router's fact/entity lookup intent).
# - multi-session: requires aggregating or comparing facts across sessions;
#   no explicit temporal anchor or relation cue → HYBRID (the router's
#   cross-source fallback for queries with entities but no dominant cue).
# - knowledge-update: asks whether a fact changed across sessions, i.e. the
#   answer is time-ordered → TEMPORAL (the closest intent the router exposes
#   for "which value is current").
# - temporal-reasoning: explicitly asks for first / last / most-recent /
#   ordering → TEMPORAL.
#
# This mapping is a documented judgment, not a tuned value; it is the gold
# label the router *should* predict if its rules matched LongMemEval's
# taxonomy. Disagreements are the signal S3 Step 1 measures.
GOLD_INTENT: dict[str, QueryIntent] = {
    "single-session-user": QueryIntent.SEMANTIC,
    "single-session-assistant": QueryIntent.SEMANTIC,
    "single-session-preference": QueryIntent.SEMANTIC,
    "multi-session": QueryIntent.HYBRID,
    "knowledge-update": QueryIntent.TEMPORAL,
    "temporal-reasoning": QueryIntent.TEMPORAL,
}

# Intents the router can emit (ordered for stable matrix rows/columns).
ROUTER_INTENTS: list[QueryIntent] = [
    QueryIntent.NO_MEMORY,
    QueryIntent.SEMANTIC,
    QueryIntent.TEMPORAL,
    QueryIntent.GRAPH,
    QueryIntent.EPISODIC,
    QueryIntent.PROCEDURAL,
    QueryIntent.HYBRID,
]


def classify_query(
    router: QueryRouter,
    query: str,
    reference_time: Any,
) -> QueryIntent:
    """Return the router's predicted intent for one query."""
    return router.route(query, reference_time=reference_time).intent


def confusion_matrix(
    gold: Sequence[QueryIntent],
    predicted: Sequence[QueryIntent],
) -> dict[str, Any]:
    """Build a gold × predicted confusion matrix and per-class P/R/F1.

    Pure function (no I/O) so it can be unit-tested with fakes.
    """
    if len(gold) != len(predicted):
        raise ValueError(
            f"gold/predicted length mismatch: {len(gold)} vs {len(predicted)}"
        )
    total = len(gold)
    labels = sorted({*gold, *predicted}, key=lambda intent: intent.value)
    matrix: dict[str, dict[str, int]] = {
        gold_label.value: {pred_label.value: 0 for pred_label in labels}
        for gold_label in labels
    }
    for gold_intent, pred_intent in zip(gold, predicted, strict=True):
        matrix[gold_intent.value][pred_intent.value] += 1
    per_class: dict[str, dict[str, float]] = {}
    for label in labels:
        tp = matrix[label.value][label.value]
        fp = sum(matrix[row][label.value] for row in matrix if row != label.value)
        fn = sum(matrix[label.value][row] for row in matrix if row != label.value)
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = (
            2 * precision * recall / (precision + recall)
            if (precision + recall)
            else 0.0
        )
        per_class[label.value] = {
            "support": tp + fn,
            "precision": precision,
            "recall": recall,
            "f1": f1,
        }
    correct = sum(matrix[label.value][label.value] for label in labels)
    accuracy = correct / total if total else 0.0
    return {
        "n": total,
        "labels": [label.value for label in labels],
        "matrix": matrix,
        "per_class": per_class,
        "accuracy": accuracy,
        "correct": correct,
    }


def _format_matrix_table(result: dict[str, Any]) -> str:
    labels = result["labels"]
    matrix = result["matrix"]
    col_w = max(10, *(len(label) for label in labels))
    gold_pred_header = "gold \\ pred"
    header = f"| {gold_pred_header:<{col_w}} |" + "".join(
        f" {label:<{col_w}} |" for label in labels
    )
    sep = "|" + "---|" * (len(labels) + 1)
    rows = []
    for gold_label in labels:
        cells = "".join(
            f" {matrix[gold_label][pred]:<{col_w}} |" for pred in labels
        )
        rows.append(f"| {gold_label:<{col_w}} |{cells}")
    return "\n".join([header, sep, *rows])


def _format_per_class_table(result: dict[str, Any]) -> str:
    label_width = max(10, *(len(label) for label in result["labels"]))
    header = (
        f"| {'intent':<{label_width}} | "
        f"{'support':>7} | {'precision':>9} | {'recall':>9} | {'f1':>9} |"
    )
    sep = "|---|---|---|---|---|"
    rows = []
    for label, metrics in result["per_class"].items():
        rows.append(
            f"| {label:<{label_width}} | {metrics['support']:>7} | "
            f"{metrics['precision'] * 100:>8.1f}% | "
            f"{metrics['recall'] * 100:>8.1f}% | "
            f"{metrics['f1'] * 100:>8.1f}% |"
        )
    return "\n".join([header, sep, *rows])


def _misclassified(
    samples: list[dict[str, Any]],
    gold: list[QueryIntent],
    predicted: list[QueryIntent],
) -> list[dict[str, Any]]:
    out = []
    for sample, g, p in zip(samples, gold, predicted, strict=True):
        if g is not p:
            out.append(
                {
                    "question_id": sample["question_id"],
                    "question_type": sample["question_type"],
                    "gold": g.value,
                    "predicted": p.value,
                    "question": sample["question"],
                }
            )
    return out


def _suggestions(result: dict[str, Any]) -> list[str]:
    """Draft rule-modification suggestions from the confusion patterns.

    Read-only analysis; no router edits (N9 scope).
    """
    suggestions: list[str] = []
    matrix = result["matrix"]
    per_class = result["per_class"]

    # Temporal gold mis-routed to semantic/hybrid: router misses temporal cues.
    temporal_to_semantic = matrix.get("temporal", {}).get("semantic", 0)
    temporal_to_hybrid = matrix.get("temporal", {}).get("hybrid", 0)
    if temporal_to_semantic + temporal_to_hybrid > 0:
        suggestions.append(
            f"- temporal gold → semantic/hybrid mis-route: "
            f"{temporal_to_semantic}→semantic, {temporal_to_hybrid}→hybrid. "
            "Consider strengthening ``_TEMPORAL_STRONG_RE`` for LongMemEval "
            "phrasings (e.g. 'most recent', 'has changed', 'used to / now')."
        )
    if per_class.get("temporal", {}).get("recall", 1.0) < 0.8:
        suggestions.append(
            f"- temporal recall = {per_class['temporal']['recall'] * 100:.1f}% "
            "(N9 threshold 80%): below threshold. A dedicated "
            "knowledge-update regex would help, but is a post-S3 task."
        )
    if per_class.get("semantic", {}).get("recall", 1.0) < 0.8:
        suggestions.append(
            f"- semantic recall = {per_class['semantic']['recall'] * 100:.1f}%: "
            "some factual lookups fall through to HYBRID; review "
            "``_FACT_RE`` coverage of LongMemEval phrasings."
        )
    if not suggestions:
        suggestions.append(
            "- No confusion pattern exceeded the suggestion thresholds; "
            "router rules are not the obvious bottleneck on this slice."
        )
    return suggestions


def _load_v2_sample_ids(source_run: Path) -> list[str] | None:
    """Return the 50 question_ids the v2 run evaluated, if available."""
    try:
        summary = _load_summary(source_run)
    except FileNotFoundError:
        return None
    samples_dir = source_run / "samples"
    if not samples_dir.exists():
        return None
    ids = sorted(
        p.stem
        for p in samples_dir.glob("*.json")
        if "extraction_snapshot" not in p.name
    )
    if not ids:
        validation = summary.get("sample_validation") or {}
        ids = sorted(validation.get("completed_sample_ids") or [])
    return ids or None


def _iter_questions(
    dataset_path: Path,
    sample_ids: set[str] | None,
):
    """Yield (question_id, question_type, question_text, asked_at) tuples."""
    for record in iter_longmemeval_records(dataset_path):
        for question in record.questions:
            qid = question.question_id
            if sample_ids is not None and qid not in sample_ids:
                continue
            yield qid, question.category, question.question, question.asked_at


def build_report(
    source_run: Path,
    dataset_path: Path,
) -> str:
    """Build the router diagnosis markdown report."""
    v2_ids = _load_v2_sample_ids(source_run)
    v2_id_set = set(v2_ids) if v2_ids else None
    if v2_id_set is None:
        print(
            "warning: could not resolve v2 sample ids; "
            "falling back to first-50 dataset slice",
            file=sys.stderr,
        )

    router = QueryRouter()

    # 50-question slice (matches the v2 benchmark run).
    samples_50: list[dict[str, Any]] = []
    gold_50: list[QueryIntent] = []
    pred_50: list[QueryIntent] = []
    for qid, qtype, text, asked_at in _iter_questions(dataset_path, v2_id_set):
        gold = GOLD_INTENT.get(qtype or "", QueryIntent.HYBRID)
        pred = classify_query(router, text, asked_at)
        samples_50.append(
            {
                "question_id": qid,
                "question_type": qtype or "unknown",
                "question": text,
            }
        )
        gold_50.append(gold)
        pred_50.append(pred)

    result_50 = confusion_matrix(gold_50, pred_50)
    misclassified_50 = _misclassified(samples_50, gold_50, pred_50)

    # Full 500-question supplement (pure router classification; no benchmark).
    samples_all: list[dict[str, Any]] = []
    gold_all: list[QueryIntent] = []
    pred_all: list[QueryIntent] = []
    for qid, qtype, text, asked_at in _iter_questions(dataset_path, None):
        gold_all.append(GOLD_INTENT.get(qtype or "", QueryIntent.HYBRID))
        pred_all.append(classify_query(router, text, asked_at))
        samples_all.append(
            {
                "question_id": qid,
                "question_type": qtype or "unknown",
                "question": text,
            }
        )
    result_all = confusion_matrix(gold_all, pred_all)
    misclassified_all = _misclassified(samples_all, gold_all, pred_all)

    lines: list[str] = []
    lines.append("# S3 Step 1: Router confusion-matrix diagnosis")
    lines.append("")
    lines.append(f"- **Router policy**: `{POLICY_NAME}`")
    lines.append(f"- **Source run**: `{source_run}`")
    lines.append(f"- **Dataset**: `{dataset_path}`")
    lines.append(
        "- **Gold-label mapping** (LongMemEval ``question_type`` → "
        "``QueryIntent``): "
        + ", ".join(
            f"`{k}`→`{v.value}`" for k, v in GOLD_INTENT.items()
        )
    )
    lines.append("")
    lines.append(
        "> Scope (N9): this step only **produces** a confusion matrix and "
        "rule-edit suggestions; ``router.py`` rules are **not** modified in "
        "S3. The full 500-question classification is pure deterministic "
        "router routing (no retrieval, no reader LLM) and is outside the "
        '"不跑 500 题" benchmark-run scope guard.'
    )
    lines.append("")

    lines.append("## 50-question slice (matches the v2 benchmark run)")
    lines.append("")
    lines.append(
        f"- N = {result_50['n']}, accuracy = "
        f"{result_50['accuracy'] * 100:.1f}% "
        f"({result_50['correct']}/{result_50['n']})"
    )
    lines.append(
        "- All 50 v2 questions are `single-session-user` (gold = SEMANTIC); "
        "this slice shows how the router classifies factual single-session "
        "lookups, but cannot expose multi-class confusion."
    )
    lines.append("")
    lines.append("### Confusion matrix (gold × predicted)")
    lines.append("")
    lines.append("```")
    lines.append(_format_matrix_table(result_50))
    lines.append("```")
    lines.append("")
    lines.append("### Per-class precision / recall / F1")
    lines.append("")
    lines.append(_format_per_class_table(result_50))
    lines.append("")

    if misclassified_50:
        lines.append("### Misclassified samples (50-question slice)")
        lines.append("")
        lines.append("| question_id | gold | predicted | question |")
        lines.append("|---|---|---|---|")
        for m in misclassified_50[:10]:
            q = m["question"].replace("|", "\\|").replace("\n", " ")[:80]
            lines.append(
                f"| {m['question_id']} | {m['gold']} | "
                f"{m['predicted']} | {q} |"
            )
        if len(misclassified_50) > 10:
            lines.append(f"... ({len(misclassified_50) - 10} more)")
        lines.append("")

    lines.append("## Full 500-question supplement (router-only, no LLM)")
    lines.append("")
    lines.append(
        f"- N = {result_all['n']}, accuracy = "
        f"{result_all['accuracy'] * 100:.1f}% "
        f"({result_all['correct']}/{result_all['n']})"
    )
    gold_dist = Counter(s["question_type"] for s in samples_all)
    lines.append(
        "- LongMemEval question_type distribution: "
        + ", ".join(f"`{k}`={v}" for k, v in sorted(gold_dist.items()))
    )
    lines.append("")
    lines.append("### Confusion matrix (gold × predicted)")
    lines.append("")
    lines.append("```")
    lines.append(_format_matrix_table(result_all))
    lines.append("```")
    lines.append("")
    lines.append("### Per-class precision / recall / F1")
    lines.append("")
    lines.append(_format_per_class_table(result_all))
    lines.append("")

    lines.append("## Misclassified samples (full 500, first 25)")
    lines.append("")
    if misclassified_all:
        lines.append("| question_id | question_type | gold | predicted | question |")
        lines.append("|---|---|---|---|---|")
        for m in misclassified_all[:25]:
            q = m["question"].replace("|", "\\|").replace("\n", " ")[:80]
            lines.append(
                f"| {m['question_id']} | {m['question_type']} | "
                f"{m['gold']} | {m['predicted']} | {q} |"
            )
        if len(misclassified_all) > 25:
            lines.append(f"... ({len(misclassified_all) - 25} more)")
    else:
        lines.append("_None._")
    lines.append("")

    lines.append("## Rule-modification suggestions (N9: not applied in S3)")
    lines.append("")
    lines.extend(_suggestions(result_all))
    lines.append("")

    lines.append("## N9 verdict")
    lines.append("")
    if result_all["accuracy"] >= 0.80:
        lines.append(
            f"- Full-500 router accuracy = {result_all['accuracy'] * 100:.1f}% "
            "≥ 80% threshold → router rules are **not** the obvious QEMR "
            "failure root cause. Weight profile (Step 2) and embedding "
            "(Step 3) are the next levers."
        )
    else:
        lines.append(
            f"- Full-500 router accuracy = {result_all['accuracy'] * 100:.1f}% "
            "< 80% threshold → router mis-routing contributes to QEMR "
            "failure; rule edits routed to a post-S3 task (N9 scope)."
        )
    lines.append(
        f"- 50-question slice accuracy = {result_50['accuracy'] * 100:.1f}% "
        "(single-class slice; informational only)."
    )
    lines.append("")

    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="S3 Step 1: router confusion-matrix diagnosis (N9, read-only)."
    )
    parser.add_argument(
        "--source-run",
        type=Path,
        default=DEFAULT_SOURCE_RUN,
        help=f"v2 run directory (default: {DEFAULT_SOURCE_RUN})",
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=DEFAULT_DATASET,
        help=f"LongMemEval cleaned JSON (default: {DEFAULT_DATASET})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write the report to this path. Default: stdout.",
    )
    args = parser.parse_args(argv)

    if not args.dataset.exists():
        print(f"error: dataset {args.dataset} not found", file=sys.stderr)
        return 1
    if not args.source_run.exists():
        print(
            f"warning: source run {args.source_run} not found; "
            "will use first-50 dataset slice for the 50-question view",
            file=sys.stderr,
        )

    report = build_report(args.source_run, args.dataset)

    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report, encoding="utf-8")
        print(f"Router diagnosis report written to {args.output}", file=sys.stderr)
    else:
        print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
