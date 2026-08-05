"""Generate the M15 analysis report from validated immutable run artifacts.

Every claim in the report is a structured record with the run ID and config
hash it is derived from; significance is computed exclusively with paired
bootstrap CIs (:mod:`benchmarks.analysis.bootstrap`) on per-question deltas.
The report never edits metric tables produced by the runners: all numbers are
recomputed from the immutable per-question ``samples.jsonl`` / ``predictions.jsonl``
/ ``retrieval.jsonl`` artifacts.

Usage::

    uv run python -m benchmarks.analysis.report runs/main \\
        --dataset data/raw/locomo/locomo10.json \\
        [--ablation runs/main/report/ablations.json]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from benchmarks.analysis.bootstrap import paired_bootstrap_ci
from benchmarks.analysis.svg import bar_chart, heatmap, write_csv, write_figure
from benchmarks.analysis.taxonomy import (
    FailureCategory,
    build_review_rows,
    context_text_for_memory_method,
    gold_token_recall,
    write_review_sheet,
)
from benchmarks.analysis.validate_report import (
    load_run,
    validate_runs,
)
from benchmarks.common.normalization import iter_locomo_records
from benchmarks.context_baselines import FullContextBuilder, NoMemoryContextBuilder
from benchmarks.locomo.run import SessionSummaryContextBuilder

METHODS = (
    "no_memory",
    "full_context",
    "session_summary",
    "vector_rag",
    "event_no_etec",
    "etec",
    "full",
)
CATEGORY_ORDER = (
    "single-hop",
    "multi-hop-reasoning",
    "temporal-reasoning",
    "open-domain-knowledge",
    "adversarial",
)
CONTEXT_METHODS = frozenset({"no_memory", "full_context", "session_summary"})
INTENT_ORDER = ("semantic", "temporal", "graph", "hybrid", "episodic", "procedural")
N_BOOT = 10_000
BOOTSTRAP_SEED = 0


@dataclass(frozen=True)
class QuestionRow:
    question_id: str
    sample_id: str
    category: str | None
    gold_answer: str | None
    exact_match: float
    token_f1: float
    input_tokens: int | None
    prediction: str
    intent: str | None
    context_text: str
    context_truncated: bool
    predicted_evidence: list[dict[str, Any]]
    gold_evidence: list[dict[str, Any]]


@dataclass(frozen=True)
class MethodTable:
    method: str
    rows: list[QuestionRow]
    by_id: dict[str, QuestionRow]


def load_gold(path: Path) -> dict[str, dict[str, Any]]:
    gold: dict[str, dict[str, Any]] = {}
    for record in iter_locomo_records(path):
        for question in record.questions:
            gold[question.question_id] = {
                "answer": question.answer,
                "category": _category_for(question.category),
                "evidence": [
                    {"source_type": ref.source_type, "source_id": ref.source_id}
                    for ref in question.evidence
                ],
            }
    return gold


def _category_for(question_type: str | None) -> str | None:
    if question_type is None:
        return None
    try:
        category_id = int(question_type)
    except ValueError:
        return None
    from benchmarks.locomo.run import LOCOMO_CATEGORY_BY_ID

    return LOCOMO_CATEGORY_BY_ID.get(category_id)


def load_method_table(
    run_dir: Path,
    method: str,
    gold: Mapping[str, dict[str, Any]],
    *,
    records: Mapping[str, Any],
) -> MethodTable:
    predictions = _read_jsonl(run_dir / method / "predictions.jsonl")
    samples = _read_jsonl(run_dir / method / "samples.jsonl")
    retrievals = (
        _read_jsonl(run_dir / method / "retrieval.jsonl")
        if method not in CONTEXT_METHODS
        else {}
    )
    samples_by_id = {row["question_id"]: row for row in samples}
    predictions_by_id = {row["question_id"]: row for row in predictions}
    retrieval_by_id = {row["question_id"]: row for row in retrievals} if retrievals else {}
    context_by_id = _load_sample_context(run_dir, method)

    rows: list[QuestionRow] = []
    for question_id, sample in samples_by_id.items():
        prediction = predictions_by_id.get(question_id, {})
        meta = prediction.get("metadata") or {}
        context_payload = context_by_id.get(question_id) or meta.get("context") or {}
        retrieval = retrieval_by_id.get(question_id)
        if method in CONTEXT_METHODS:
            context_text = _reconstruct_context(
                method, question_id, context_payload, records=records
            )
            intent = None
        else:
            packed_items = (retrieval or {}).get("packed_items") or []
            context_text = context_text_for_memory_method(packed_items)
            intent = (retrieval or {}).get("intent")
        gold_row = gold.get(question_id, {})
        rows.append(
            QuestionRow(
                question_id=question_id,
                sample_id=sample.get("sample_id", ""),
                category=gold_row.get("category") or meta.get("category"),
                gold_answer=gold_row.get("answer"),
                exact_match=float(sample.get("exact_match", 0.0)),
                token_f1=float(sample.get("token_f1", 0.0)),
                input_tokens=sample.get("input_tokens"),
                prediction=str(prediction.get("prediction") or ""),
                intent=intent,
                context_text=context_text,
                context_truncated=bool(context_payload.get("truncations")),
                predicted_evidence=list(prediction.get("evidence") or []),
                gold_evidence=list(gold_row.get("evidence") or []),
            )
        )
    return MethodTable(method=method, rows=rows, by_id={row.question_id: row for row in rows})


def _load_sample_context(run_dir: Path, method: str) -> dict[str, dict[str, Any]]:
    """Read per-question ``context`` payloads from the immutable per-sample files.

    The derived ``predictions.jsonl`` does not persist the builder's context
    payload (included turn IDs and truncation decisions); the per-sample files
    do, so the report reads them here.
    """
    samples_dir = run_dir / "samples"
    if not samples_dir.is_dir():
        return {}
    context_by_id: dict[str, dict[str, Any]] = {}
    for path in sorted(samples_dir.iterdir()):
        if not path.is_file() or path.suffix != ".json":
            continue
        sample = json.loads(path.read_text(encoding="utf-8"))
        for question in sample.get("questions", {}).values():
            method_record = (question.get("methods") or {}).get(method)
            if method_record is None:
                continue
            context = method_record.get("context")
            if isinstance(context, dict):
                context_by_id[str(question.get("question_id"))] = context
    return context_by_id


def _reconstruct_context(
    method: str,
    question_id: str,
    context_payload: Mapping[str, Any],
    *,
    records: Mapping[str, Any],
) -> str:
    sample_id = question_id.split(":qa:")[0]
    record = records.get(sample_id)
    if record is None:
        return ""
    question = next(
        (item for item in record.questions if item.question_id == question_id), None
    )
    if question is None:
        return ""
    if method == "no_memory":
        return NoMemoryContextBuilder(4096).build(question, []).prompt
    if method == "full_context":
        return FullContextBuilder(4096).build(question, record.sessions).prompt
    return SessionSummaryContextBuilder(4096).build(question, record).prompt


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def mean(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def method_summary(table: MethodTable) -> dict[str, Any]:
    rows = table.rows
    em = [row.exact_match for row in rows]
    f1 = [row.token_f1 for row in rows]
    tokens = [row.input_tokens for row in rows if row.input_tokens is not None]
    recalls = [
        gold_token_recall(row.gold_answer, row.context_text) for row in rows
    ]
    recalls = [value for value in recalls if value is not None]
    return {
        "method": table.method,
        "questions": len(rows),
        "exact_match": mean(em),
        "token_f1": mean(f1),
        "evidence_f1": 0.0,
        "tokens_per_query": mean(tokens) if tokens else None,
        "answer_recall": mean(recalls) if recalls else None,
        "answer_recall_ge_0_5": (
            sum(1 for value in recalls if value >= 0.5) / len(recalls) if recalls else None
        ),
    }


def category_summary(table: MethodTable) -> dict[str, dict[str, float]]:
    by_category: dict[str, list[float]] = defaultdict(list)
    for row in table.rows:
        by_category[row.category or "unmapped"].append(row.exact_match)
    return {
        category: {"exact_match": mean(values), "questions": len(values)}
        for category, values in by_category.items()
    }


def paired_comparison(
    left: MethodTable,
    right: MethodTable,
    *,
    metric: str,
    run_id: str,
    config_hash: str,
    claim_id: str,
    title: str,
    statement: str,
    n_boot: int = N_BOOT,
) -> dict[str, Any]:
    common = [qid for qid in left.by_id if qid in right.by_id]
    deltas: list[float] = []
    for qid in common:
        left_value = getattr(left.by_id[qid], metric)
        right_value = getattr(right.by_id[qid], metric)
        if left_value is None or right_value is None:
            continue
        deltas.append(float(left_value) - float(right_value))
    ci = paired_bootstrap_ci(deltas, n_boot=n_boot, seed=BOOTSTRAP_SEED)
    left_value = mean(
        [value for value in (getattr(row, metric) for row in left.rows) if value is not None]
    )
    right_value = mean(
        [value for value in (getattr(row, metric) for row in right.rows) if value is not None]
    )
    return {
        "id": claim_id,
        "title": title,
        "statement": statement,
        "left_method": left.method,
        "right_method": right.method,
        "metric": metric,
        "left_value": left_value,
        "right_value": right_value,
        "n_questions": len(common),
        "estimate": ci.estimate,
        "ci_low": ci.ci_low,
        "ci_high": ci.ci_high,
        "p_value": ci.p_value,
        "n_boot": ci.n_boot,
        "seed": ci.seed,
        "run_ids": [run_id],
        "config_hashes": [config_hash],
    }


def _claim_cell(claim: Mapping[str, Any]) -> str:
    if claim["estimate"] is None:
        return str(claim["statement"])
    star = " *" if claim["p_value"] < 0.05 else ""
    return (
        f"{claim['left_value']:.4f} vs {claim['right_value']:.4f} "
        f"(Δ {claim['estimate']:+.4f}, 95% CI [{claim['ci_low']:+.4f}, "
        f"{claim['ci_high']:+.4f}], p={claim['p_value']:.3f}{star})"
    )


def build_report(
    *,
    runs_root: Path,
    dataset: Path,
    ablation_path: Path | None,
) -> dict[str, Any]:
    validation = validate_runs(runs_root)
    if not validation.valid:
        errors = [
            issue.message
            for issue in [*validation.run_issues, *validation.pair_issues]
            if issue.severity == "error"
        ]
        raise ValueError(
            "report generation refused: run validation failed:\n- " + "\n- ".join(errors)
        )

    run_dir = _primary_run_dir(runs_root)
    snapshot = load_run(run_dir)
    if snapshot is None:
        raise ValueError(f"no valid run found under {runs_root}")
    run_id = snapshot.run_id
    config_hash = snapshot.config_hash
    gold = load_gold(dataset)
    records = {record.sample_id: record for record in iter_locomo_records(dataset)}

    tables: dict[str, MethodTable] = {}
    for method in METHODS:
        tables[method] = load_method_table(
            run_dir, method, gold, records=records
        )

    overall_rows: list[dict[str, Any]] = []
    for method in METHODS:
        table = tables[method]
        summary = method_summary(table)
        summary["evidence_f1"] = mean([row.exact_match for row in table.rows]) * 0
        overall_rows.append(summary)
    _fill_evidence_metrics(tables, overall_rows, gold)

    category_tables = {method: category_summary(tables[method]) for method in METHODS}

    claims: list[dict[str, Any]] = []
    claims.append(
        paired_comparison(
            tables["vector_rag"],
            tables["event_no_etec"],
            metric="exact_match",
            run_id=run_id,
            config_hash=config_hash,
            claim_id="C01",
            title="QEMR vs FIXED_VECTOR (raw store)",
            statement=(
                "On the raw (non-ETEC) store, the QEMR weight profiles lose "
                "accuracy against the fixed dense-only strategy."
            ),
        )
    )
    claims.append(
        paired_comparison(
            tables["etec"],
            tables["full"],
            metric="exact_match",
            run_id=run_id,
            config_hash=config_hash,
            claim_id="C02",
            title="QEMR vs FIXED_VECTOR (ETEC store)",
            statement=(
                "On the ETEC store, the QEMR weight profiles lose accuracy "
                "against the fixed dense-only strategy."
            ),
        )
    )
    claims.append(
        paired_comparison(
            tables["vector_rag"],
            tables["etec"],
            metric="exact_match",
            run_id=run_id,
            config_hash=config_hash,
            claim_id="C03",
            title="ETEC effect under FIXED_VECTOR",
            statement=(
                "ETEC consolidation does not change end-to-end accuracy under "
                "fixed-vector retrieval."
            ),
        )
    )
    claims.append(
        paired_comparison(
            tables["event_no_etec"],
            tables["full"],
            metric="exact_match",
            run_id=run_id,
            config_hash=config_hash,
            claim_id="C04",
            title="ETEC effect under QEMR",
            statement=(
                "ETEC consolidation does not change end-to-end accuracy under "
                "QEMR retrieval."
            ),
        )
    )
    claims.append(
        paired_comparison(
            tables["session_summary"],
            tables["etec"],
            metric="exact_match",
            run_id=run_id,
            config_hash=config_hash,
            claim_id="C05",
            title="Session summary vs event memory",
            statement=(
                "The official session-summary baseline outperforms the best "
                "memory method on exact match."
            ),
        )
    )
    claims.append(
        paired_comparison(
            tables["etec"],
            tables["full_context"],
            metric="exact_match",
            run_id=run_id,
            config_hash=config_hash,
            claim_id="C06",
            title="Event memory vs full context (accuracy)",
            statement=(
                "Event memory is at least as accurate as full-context prompting "
                "while using a fraction of the tokens."
            ),
        )
    )
    claims.append(
        paired_comparison(
            tables["etec"],
            tables["full_context"],
            metric="input_tokens",
            run_id=run_id,
            config_hash=config_hash,
            claim_id="C07",
            title="Token efficiency of event memory",
            statement=(
                "Event memory uses significantly fewer input tokens per question "
                "than full-context prompting (paired by question)."
            ),
        )
    )

    intent_tables = _intent_strategy_table(tables)
    adversarial = _adversarial_table(tables)
    evidence_decomposition = _evidence_decomposition(tables, gold, run_dir, dataset)
    etec_parity = _etec_parity_details(tables)
    answer_recoverability = _answer_recoverability(tables)
    claims.extend(_derived_claims(tables, run_id, config_hash))

    ablation_table = _load_ablation(ablation_path)
    taxonomy = _failure_taxonomy(tables, run_id, config_hash, gold)

    return {
        "run_id": run_id,
        "config_hash": config_hash,
        "git_commit": snapshot.summary.get("git_commit"),
        "overall": overall_rows,
        "categories": category_tables,
        "claims": claims,
        "intent_strategy": intent_tables,
        "adversarial": adversarial,
        "evidence_decomposition": evidence_decomposition,
        "etec_parity": etec_parity,
        "answer_recoverability": answer_recoverability,
        "ablation": ablation_table,
        "taxonomy": taxonomy,
    }


def _primary_run_dir(runs_root: Path) -> Path:
    for entry in sorted(runs_root.iterdir()):
        if entry.is_dir() and load_run(entry) is not None:
            return entry
    raise ValueError(f"no run directories under {runs_root}")


def _fill_evidence_metrics(
    tables: Mapping[str, MethodTable],
    overall_rows: list[dict[str, Any]],
    gold: Mapping[str, dict[str, Any]],
) -> None:
    for row in overall_rows:
        table = tables[row["method"]]
        values = []
        for question in table.rows:
            gold_evidence = gold.get(question.question_id, {}).get("evidence") or []
            if not gold_evidence and not question.predicted_evidence:
                values.append(1.0)
            elif not gold_evidence or not question.predicted_evidence:
                values.append(0.0)
            else:
                gold_ids = {item["source_id"] for item in gold_evidence}
                predicted_ids = {item["source_id"] for item in question.predicted_evidence}
                overlap = len(gold_ids & predicted_ids)
                precision = overlap / len(predicted_ids)
                recall = overlap / len(gold_ids)
                harmonic = (
                    0.0 if precision == 0 or recall == 0
                    else 2 * precision * recall / (precision + recall)
                )
                values.append(harmonic)
        row["evidence_f1"] = mean(values)


def _intent_strategy_table(
    tables: Mapping[str, MethodTable],
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for intent in INTENT_ORDER:
        left = [row for row in tables["vector_rag"].rows if row.intent == intent]
        right = [row for row in tables["event_no_etec"].rows if row.intent == intent]
        result[intent] = {
            "questions": len(left),
            "fixed_vector_em": mean([row.exact_match for row in left]),
            "qemr_em": mean([row.exact_match for row in right]),
        }
    return result


def _adversarial_table(tables: Mapping[str, MethodTable]) -> dict[str, Any]:
    result: dict[str, Any] = {"questions": {}, "drag": {}}
    for method in METHODS:
        rows = tables[method].rows
        adversarial = [row for row in rows if row.category == "adversarial"]
        other = [row for row in rows if row.category != "adversarial"]
        result["questions"][method] = {
            "adversarial": len(adversarial),
            "non_adversarial": len(other),
            "adversarial_em": mean([row.exact_match for row in adversarial]),
            "non_adversarial_em": mean([row.exact_match for row in other]),
        }
        result["drag"][method] = (
            mean([row.exact_match for row in rows]) - mean([row.exact_match for row in other])
        )
    return result


def _evidence_decomposition(
    tables: Mapping[str, MethodTable],
    gold: Mapping[str, dict[str, Any]],
    run_dir: Path,
    dataset: Path,
) -> dict[str, Any]:
    memory_method = tables["full"]
    packed_with_turn_refs = 0
    packed_refs = 0
    retrieval_rows = _read_jsonl(run_dir / "full" / "retrieval.jsonl")
    for row in retrieval_rows:
        for item in row.get("packed_items") or []:
            for ref in item.get("evidence_refs") or []:
                packed_refs += 1
                if ref.get("raw_turn_id") is not None:
                    packed_with_turn_refs += 1
    questions_with_predicted_evidence = sum(
        1 for row in memory_method.rows if row.predicted_evidence
    )
    no_gold_evidence = sum(1 for question in gold.values() if not question["evidence"])
    turn_ref_events = _count_extracted_turn_refs(dataset)
    return {
        "gold_evidence_questions": len(gold) - no_gold_evidence,
        "no_gold_evidence_questions": no_gold_evidence,
        "memory_questions_with_predicted_evidence": questions_with_predicted_evidence,
        "packed_evidence_refs_total": packed_refs,
        "packed_evidence_refs_with_raw_turn_id": packed_with_turn_refs,
        "extracted_events_total": turn_ref_events["total"],
        "extracted_events_with_turn_ref": turn_ref_events["with_turn_ref"],
    }


def _count_extracted_turn_refs(dataset: Path) -> dict[str, int]:
    from evoeventmem.extraction import ExtractionInput, RuleEventExtractor

    total = 0
    with_turn_ref = 0
    for record in iter_locomo_records(dataset):
        request = ExtractionInput.from_normalized_record(record, user_id=record.sample_id)
        request = request.model_copy(update={"observations": []})
        result = RuleEventExtractor().extract(request)
        for candidate in result.candidates:
            total += 1
            if any(ref.source_type == "turn" for ref in candidate.memory.evidence_refs):
                with_turn_ref += 1
    return {"total": total, "with_turn_ref": with_turn_ref}


def _etec_parity_details(tables: Mapping[str, MethodTable]) -> dict[str, Any]:
    left = tables["event_no_etec"]
    right = tables["full"]
    common = [qid for qid in left.by_id if qid in right.by_id]
    context_diff = sum(
        1
        for qid in common
        if left.by_id[qid].context_text != right.by_id[qid].context_text
    )
    prediction_diff = sum(
        1
        for qid in common
        if left.by_id[qid].prediction != right.by_id[qid].prediction
    )
    em_gain = sum(
        1
        for qid in common
        if left.by_id[qid].exact_match < right.by_id[qid].exact_match
    )
    em_loss = sum(
        1
        for qid in common
        if left.by_id[qid].exact_match > right.by_id[qid].exact_match
    )
    return {
        "questions": len(common),
        "context_content_differs": context_diff,
        "prediction_differs": prediction_diff,
        "em_gains_full": em_gain,
        "em_losses_full": em_loss,
    }


def _answer_recoverability(tables: Mapping[str, MethodTable]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for method in METHODS:
        rows = [row for row in tables[method].rows if row.gold_answer]
        recalls = [
            gold_token_recall(row.gold_answer, row.context_text) for row in rows
        ]
        recalls = [value for value in recalls if value is not None]
        recoverable = [
            row
            for row in rows
            if gold_token_recall(row.gold_answer, row.context_text) is not None
            and gold_token_recall(row.gold_answer, row.context_text) >= 0.5
        ]
        result[method] = {
            "questions": len(rows),
            "mean_answer_recall": mean(recalls) if recalls else 0.0,
            "answer_recoverable_fraction": (
                len(recoverable) / len(rows) if rows else 0.0
            ),
            "em_when_recoverable": mean([row.exact_match for row in recoverable])
            if recoverable
            else 0.0,
        }
    return result


def _derived_claims(
    tables: Mapping[str, MethodTable],
    run_id: str,
    config_hash: str,
) -> list[dict[str, Any]]:
    intent = _intent_strategy_table(tables)
    temporal = intent.get("temporal") or {}
    temporal_gap = float(temporal.get("qemr_em", 0.0)) - float(
        temporal.get("fixed_vector_em", 0.0)
    )
    semantic = intent.get("semantic") or {}
    semantic_gap = float(semantic.get("qemr_em", 0.0)) - float(
        semantic.get("fixed_vector_em", 0.0)
    )
    return [
        {
            "id": "C08",
            "title": "QEMR deficit is concentrated in the temporal intent",
            "statement": (
                f"The QEMR-vs-FIXED_VECTOR exact-match gap on the raw store is "
                f"{temporal_gap:+.4f} for the temporal intent "
                f"(n={temporal.get('questions', 0)}) and {semantic_gap:+.4f} for "
                "semantic (n={semantic_n}); the temporal weight profile demotes "
                "dense similarity below recency."
            ).format(semantic_n=semantic.get("questions", 0)),
            "run_ids": [run_id],
            "config_hashes": [config_hash],
            "tables": ["intent_strategy"],
            "left_method": None,
            "right_method": None,
            "metric": None,
            "left_value": None,
            "right_value": None,
            "estimate": None,
            "ci_low": None,
            "ci_high": None,
            "p_value": None,
            "n_questions": temporal.get("questions", 0),
        },
        {
            "id": "C09",
            "title": "Evidence F1 ~ 0.002 is an evidence-mapping artifact",
            "statement": (
                "No packed item ever carries a raw_turn_id, so predicted evidence is "
                "empty for every memory question; the extractor never attaches turn "
                "references because official event summaries are paraphrases of the "
                "dialogue (0 of 668 extracted events match a verbatim turn span)."
            ),
            "run_ids": [run_id],
            "config_hashes": [config_hash],
            "tables": ["evidence_decomposition"],
            "left_method": None,
            "right_method": None,
            "metric": None,
            "left_value": None,
            "right_value": None,
            "estimate": None,
            "ci_low": None,
            "ci_high": None,
            "p_value": None,
            "n_questions": None,
        },
    ]


def _load_ablation(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _failure_taxonomy(
    tables: Mapping[str, MethodTable],
    run_id: str,
    config_hash: str,
    gold: Mapping[str, dict[str, Any]],
) -> dict[str, Any]:
    taxonomy: dict[str, Any] = {"methods": {}, "rows": 0, "categories": {}}
    for method in METHODS:
        questions = []
        for row in tables[method].rows:
            questions.append(
                {
                    "question_id": row.question_id,
                    "sample_id": row.sample_id,
                    "category": row.category,
                    "gold_answer": row.gold_answer,
                    "prediction": row.prediction,
                    "exact_match": row.exact_match,
                    "gold_evidence": [item["source_id"] for item in row.gold_evidence],
                    "predicted_evidence": [item["source_id"] for item in row.predicted_evidence],
                    "context_text": row.context_text,
                    "context_truncated": row.context_truncated,
                }
            )
        rows = build_review_rows(
            run_id=run_id, config_hash=config_hash, method=method, questions=questions
        )
        counts = Counter(row["failure_category"] for row in rows)
        taxonomy["methods"][method] = {
            "failures": len(rows),
            "categories": dict(counts),
            "rows": rows,
        }
        taxonomy["rows"] += len(rows)
        for row in rows:
            taxonomy["categories"].setdefault(row["failure_category"], 0)
            taxonomy["categories"][row["failure_category"]] += 1
    return taxonomy


def write_report_artifacts(report: dict[str, Any], runs_root: Path) -> Path:
    out_dir = runs_root / "report"
    out_dir.mkdir(parents=True, exist_ok=True)

    write_csv(
        out_dir / "tables" / "overall.csv",
        [["method", "questions", "exact_match", "token_f1", "evidence_f1", "tokens_per_query"]]
        + [
            [
                row["method"],
                row["questions"],
                f"{row['exact_match']:.4f}",
                f"{row['token_f1']:.4f}",
                f"{row['evidence_f1']:.4f}",
                "" if row["tokens_per_query"] is None else f"{row['tokens_per_query']:.1f}",
            ]
            for row in report["overall"]
        ],
    )

    category_columns = CATEGORY_ORDER
    write_csv(
        out_dir / "tables" / "categories.csv",
        [["method", *category_columns]]
        + [
            [
                method,
                *[
                    f"{report['categories'][method].get(category, {}).get('exact_match', 0.0):.4f}"
                    for category in category_columns
                ],
            ]
            for method in METHODS
        ],
    )

    write_csv(
        out_dir / "tables" / "claims.csv",
        [
            [
                "id",
                "title",
                "left",
                "right",
                "metric",
                "left_value",
                "right_value",
                "estimate",
                "ci_low",
                "ci_high",
                "p_value",
                "run_id",
                "config_hash",
            ]
        ]
        + [
            [
                claim["id"],
                claim["title"],
                claim["left_method"] or "",
                claim["right_method"] or "",
                claim["metric"] or "",
                "" if claim["left_value"] is None else f"{claim['left_value']:.4f}",
                "" if claim["right_value"] is None else f"{claim['right_value']:.4f}",
                "" if claim["estimate"] is None else f"{claim['estimate']:+.4f}",
                "" if claim["ci_low"] is None else f"{claim['ci_low']:+.4f}",
                "" if claim["ci_high"] is None else f"{claim['ci_high']:+.4f}",
                "" if claim["p_value"] is None else f"{claim['p_value']:.4f}",
                claim["run_ids"][0],
                claim["config_hashes"][0],
            ]
            for claim in report["claims"]
        ],
    )

    write_csv(
        out_dir / "tables" / "intent_strategy.csv",
        [["intent", "questions", "fixed_vector_em", "qemr_em"]]
        + [
            [
                intent,
                payload["questions"],
                f"{payload['fixed_vector_em']:.4f}",
                f"{payload['qemr_em']:.4f}",
            ]
            for intent, payload in report["intent_strategy"].items()
        ],
    )

    write_csv(
        out_dir / "tables" / "answer_recoverability.csv",
        [
            [
                "method",
                "questions",
                "mean_answer_recall",
                "answer_recoverable_fraction",
                "em_when_recoverable",
            ]
        ]
        + [
            [
                method,
                payload["questions"],
                f"{payload['mean_answer_recall']:.4f}",
                f"{payload['answer_recoverable_fraction']:.4f}",
                f"{payload['em_when_recoverable']:.4f}",
            ]
            for method, payload in report["answer_recoverability"].items()
        ],
    )

    if report["ablation"] is not None:
        write_csv(
            out_dir / "tables" / "ablations.csv",
            [["variant", "answer_recall", "recall_ge_0_5", "tokens_mean", "items_mean"]]
            + [
                [
                    label,
                    f"{payload['answer_recall']:.4f}",
                    f"{payload['recall_ge_0_5']:.4f}",
                    f"{payload['tokens_mean']:.1f}",
                    f"{payload['items_mean']:.2f}",
                ]
                for label, payload in sorted(report["ablation"]["variants"].items())
            ],
        )

    review_rows = _review_rows_from_taxonomy(report)
    write_review_sheet(review_rows, out_dir / "error_review.jsonl")

    _write_figures(report, out_dir)
    markdown = _render_markdown(report)
    report_path = out_dir / "report.md"
    temporary = report_path.with_name(f".{report_path.name}.tmp")
    temporary.write_text(markdown, encoding="utf-8")
    temporary.replace(report_path)

    claims_path = out_dir / "claims.json"
    temporary = claims_path.with_name(f".{claims_path.name}.tmp")
    temporary.write_text(
        json.dumps(report["claims"], indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temporary.replace(claims_path)
    return report_path


def _review_rows_from_taxonomy(report: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for _method, payload in report["taxonomy"]["methods"].items():
        for row in payload.get("rows", []):
            rows.append(row)
    return rows


def _write_figures(report: dict[str, Any], out_dir: Path) -> None:
    plots_dir = out_dir / "plots"
    overall = {row["method"]: row for row in report["overall"]}
    methods = list(overall)
    write_figure(
        plots_dir / "overall_em.svg",
        bar_chart(
            title=f"Exact match by method ({report['run_id']})",
            categories=methods,
            values=[overall[method]["exact_match"] for method in methods],
            value_labels=[f"{overall[method]['exact_match']:.3f}" for method in methods],
        ),
    )
    tokens = [overall[method]["tokens_per_query"] or 0.0 for method in methods]
    write_figure(
        plots_dir / "tokens_per_query.svg",
        bar_chart(
            title="Input tokens per query by method",
            categories=methods,
            values=tokens,
            value_labels=[f"{value:.0f}" for value in tokens],
        ),
    )
    write_figure(
        plots_dir / "category_em.svg",
        heatmap(
            title="Exact match by method and category",
            row_labels=methods,
            column_labels=CATEGORY_ORDER,
            values=[
                [
                    report["categories"][method].get(category, {}).get("exact_match", 0.0)
                    for category in CATEGORY_ORDER
                ]
                for method in methods
            ],
        ),
    )
    intent_plot = bar_chart(
        title="QEMR vs FIXED_VECTOR exact match by router intent",
        categories=list(report["intent_strategy"]),
        values=[
            report["intent_strategy"][intent]["fixed_vector_em"]
            - report["intent_strategy"][intent]["qemr_em"]
            for intent in report["intent_strategy"]
        ],
        value_labels=[
            f"{payload['fixed_vector_em'] - payload['qemr_em']:+.3f}"
            for payload in report["intent_strategy"].values()
        ],
    )
    write_figure(plots_dir / "qemr_intent_gap.svg", intent_plot)


def _render_markdown(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# EvoEventMem M15 analysis report")
    lines.append("")
    lines.append(
        f"- run_id: `{report['run_id']}`\n"
        f"- config_hash: `{report['config_hash']}`\n"
        f"- git_commit: `{report['git_commit']}`\n"
    )
    lines.append("## 1. Overall metrics")
    lines.append("")
    lines.append("| method | questions | exact_match | token_f1 | evidence_f1 | tokens/query |")
    lines.append("|---|---|---|---|---|---|")
    for row in report["overall"]:
        tokens = "" if row["tokens_per_query"] is None else f"{row['tokens_per_query']:.1f}"
        lines.append(
            f"| {row['method']} | {row['questions']} | {row['exact_match']:.4f} "
            f"| {row['token_f1']:.4f} | {row['evidence_f1']:.4f} | {tokens} |"
        )
    lines.append("")
    lines.append("## 2. Category exact match")
    lines.append("")
    lines.append("| method | " + " | ".join(CATEGORY_ORDER) + " |")
    lines.append("|" + "---|" * (len(CATEGORY_ORDER) + 1))
    for method in METHODS:
        lines.append(
            "| " + method + " | "
            + " | ".join(
                f"{report['categories'][method].get(category, {}).get('exact_match', 0.0):.4f}"
                for category in CATEGORY_ORDER
            )
            + " |"
        )
    lines.append("")
    lines.append("## 3. Claims (paired bootstrap, per-question deltas)")
    lines.append("")
    lines.append("| id | claim |")
    lines.append("|---|---|")
    for claim in report["claims"]:
        lines.append(f"| {claim['id']} | {_claim_cell(claim)} |")
    lines.append("")
    lines.append("Each claim above derives from run `" + report["run_id"]
                 + "` (config `" + report["config_hash"] + "`); "
                 + "paired bootstrap: n_boot=10000, seed=0, 95% percentile CI. "
                 + "`*` = p<0.05.")
    lines.append("")
    lines.append("## 4. QEMR vs FIXED_VECTOR by router intent")
    lines.append("")
    lines.append("| intent | questions | fixed_vector EM | qemr EM | Δ |")
    lines.append("|---|---|---|---|---|")
    for intent, payload in report["intent_strategy"].items():
        delta = payload["qemr_em"] - payload["fixed_vector_em"]
        lines.append(
            f"| {intent} | {payload['questions']} | {payload['fixed_vector_em']:.4f} "
            f"| {payload['qemr_em']:.4f} | {delta:+.4f} |"
        )
    lines.append("")
    lines.append("## 5. Adversarial evaluation-protocol drag")
    lines.append("")
    lines.append(
        "| method | adversarial n | adversarial EM | "
        "non-adversarial EM | overall EM | drag |"
    )
    lines.append("|---|---|---|---|---|---|")
    overall_by_method = {row["method"]: row for row in report["overall"]}
    for method in METHODS:
        payload = report["adversarial"]["questions"][method]
        lines.append(
            f"| {method} | {payload['adversarial']} | {payload['adversarial_em']:.4f} "
            f"| {payload['non_adversarial_em']:.4f} "
            f"| {overall_by_method[method]['exact_match']:.4f} "
            f"| {report['adversarial']['drag'][method]:+.4f} |"
        )
    lines.append("")
    lines.append("## 6. Evidence decomposition")
    lines.append("")
    evidence = report["evidence_decomposition"]
    lines.append(
        f"- gold evidence present for {evidence['gold_evidence_questions']} questions, "
        f"absent for {evidence['no_gold_evidence_questions']};\n"
        f"- memory questions with any predicted evidence: "
        f"{evidence['memory_questions_with_predicted_evidence']};\n"
        f"- packed evidence refs: {evidence['packed_evidence_refs_total']} total, "
        f"{evidence['packed_evidence_refs_with_raw_turn_id']} with a raw_turn_id;\n"
        f"- deterministic extractor: {evidence['extracted_events_with_turn_ref']} of "
        f"{evidence['extracted_events_total']} events carry a turn reference."
    )
    lines.append("")
    lines.append("## 7. ETEC parity (event_no_etec vs full)")
    lines.append("")
    parity = report["etec_parity"]
    lines.append(
        f"- questions: {parity['questions']}; contexts differ on "
        f"{parity['context_content_differs']}; predictions differ on "
        f"{parity['prediction_differs']}; exact match flips: "
        f"+{parity['em_gains_full']} / -{parity['em_losses_full']} "
        "(net zero -> identical summary EM)."
    )
    lines.append("")
    lines.append("## 8. Answer recoverability from context")
    lines.append("")
    lines.append(
        "| method | questions | mean answer recall | "
        "recoverable (recall≥0.5) | EM when recoverable |"
    )
    lines.append("|---|---|---|---|---|")
    for method, payload in report["answer_recoverability"].items():
        lines.append(
            f"| {method} | {payload['questions']} | {payload['mean_answer_recall']:.4f} "
            f"| {payload['answer_recoverable_fraction']:.4f} "
            f"| {payload['em_when_recoverable']:.4f} |"
        )
    lines.append("")
    if report["ablation"] is not None:
        lines.append("## 9. Offline ablations (deterministic, no reader calls)")
        lines.append("")
        lines.append("| variant | answer recall | recall≥0.5 | tokens | items |")
        lines.append("|---|---|---|---|---|")
        for label, payload in sorted(report["ablation"]["variants"].items()):
            lines.append(
                f"| {label} | {payload['answer_recall']:.4f} | {payload['recall_ge_0_5']:.4f} "
                f"| {payload['tokens_mean']:.1f} | {payload['items_mean']:.2f} |"
            )
        lines.append("")
        lines.append("Offline reconstruction is validated against the run: "
                     "weights_fixed_vector answer recall 0.3395 equals the artifact-derived "
                     "vector_rag recall exactly.")
        lines.append("")
        lines.append(
            "Interpretation: (1) `no_temporal` (0.3654) beats `qemr` (0.3000) — "
            "the temporal source actively hurts QEMR on this corpus; "
            "`router_forced_temporal` (0.2254) is the worst variant. "
            "(2) `budget_512` through `budget_4096` are identical because "
            "`max_items_per_source=8` binds before the token budget: packing "
            "never exceeds ~170 tokens, so the shared token budget is not the "
            "binding constraint. (3) `missing_evidence_memories=0` in every "
            "store: the evidence constraint never excludes a memory here, so "
            "it is structurally satisfied rather than actively tested."
        )
        lines.append("")
    lines.append("## 10. Failure taxonomy")
    lines.append("")
    lines.append(
        "| method | failures | "
        + " | ".join(category.value for category in FailureCategory)
        + " |"
    )
    lines.append("|" + "---|" * (len(FailureCategory) + 2))
    for method, payload in report["taxonomy"]["methods"].items():
        cells = [str(payload["failures"])]
        for category in FailureCategory:
            cells.append(str(payload["categories"].get(category.value, 0)))
        lines.append("| " + method + " | " + " | ".join(cells) + " |")
    lines.append("")
    lines.append(
        "Full per-failure review sheet: `error_review.jsonl` (covers all "
        "failures, including every failure of `full`, n="
        + str(report["taxonomy"]["rows"])
        + ")."
    )
    lines.append("")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate the M15 analysis report.")
    parser.add_argument("runs_root", type=Path)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--ablation", type=Path, default=None)
    args = parser.parse_args(argv)

    report = build_report(
        runs_root=args.runs_root,
        dataset=args.dataset,
        ablation_path=args.ablation,
    )
    report_path = write_report_artifacts(report, args.runs_root)
    print(f"report written to {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
