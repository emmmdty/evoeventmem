"""Aggregate S7 benchmark statistics with uncertainty and significance (W3).

Given a finalized (or partially completed) LongMemEval run directory, produce:

1. Three metrics side by side per method: raw EM, contains-EM (normalized gold
   is a substring of the normalized prediction; a *lenient upper bound*), and
   token F1 (the primary metric, taken from the run artifacts).
2. Bootstrap 95% confidence intervals (percentile method) for every
   method x metric over completed samples.
3. Paired permutation tests (sign-flip, ``--n-perm`` permutations) for
   ``full`` vs ``vector_rag`` and ``full`` vs ``event_no_etec``, overall and
   per category, on each metric.
4. A retrieval-level BM25 coverage proxy baseline: a dependency-free Okapi
   BM25 over raw turns selects top-k turns per question (k aligned to the
   median QEMR packed-item count); we report the fraction of samples where the
   top-k turns touch at least one gold answer session, compared with the same
   hit-rate computed from vector_rag / QEMR packed-item evidence refs.
   NOTE: this is a retrieval-coverage proxy, not an end-to-end EM number.

Usage::

    uv run python scripts/benchmark_stats.py \
        --run-dir runs/publication/s7-pilot50-complete \
        --config configs/longmemeval/test50-mimo.toml \
        --out-json runs/analysis/stats.json \
        --out-md runs/analysis/stats.md \
        --n-boot 10000 --n-perm 10000 --seed 20260822
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from benchmarks.common.normalization import iter_longmemeval_records  # noqa: E402
from benchmarks.longmemeval.run import CATEGORY_BY_QUESTION_TYPE, load_config  # noqa: E402

SCHEMA_VERSION = "benchmark_stats.v1"
PRIMARY_METRIC = "token_f1"
METRICS = ("raw_em", "contains_em", "token_f1")
PERM_TEST_PAIRS = (("full", "vector_rag"), ("full", "event_no_etec"))


def _norm_text(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text)


def _load_dataset_index(
    dataset_path: Path, sample_ids: Sequence[str]
) -> dict[str, dict[str, Any]]:
    wanted = set(sample_ids)
    index: dict[str, dict[str, Any]] = {}
    for record in iter_longmemeval_records(dataset_path):
        if record.sample_id not in wanted:
            continue
        question = record.questions[0]
        index[record.sample_id] = {
            "gold_answer": question.answer,
            "category": (
                CATEGORY_BY_QUESTION_TYPE.get(question.category or "", "unmapped")
                if question.category
                else "unmapped"
            ),
            "answer_session_ids": [
                ref.source_id for ref in question.evidence if ref.locator == "answer_session_ids"
            ],
            "question_text": question.question,
            "turns": [
                {"session_id": session.session_id, "content": turn.content}
                for session in record.sessions
                for turn in session.turns
                if turn.content.strip()
            ],
        }
        if len(index) == len(wanted):
            break
    missing = wanted - set(index)
    if missing:
        raise KeyError(f"samples not found in dataset: {sorted(missing)}")
    return index


def _load_run_records(run_dir: Path, methods: Sequence[str]) -> list[dict[str, Any]]:
    samples_dir = run_dir / "samples"
    rows: list[dict[str, Any]] = []
    for path in sorted(samples_dir.glob("*.json")):
        if path.name.endswith(".extraction_snapshot.json"):
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        sample_id = str(payload["sample_id"])
        row: dict[str, Any] = {
            "sample_id": sample_id,
            "category": payload.get("category") or "unmapped",
            "methods": {},
        }
        for method in methods:
            record = payload.get("methods", {}).get(method)
            if record is None:
                continue
            prediction = str(record.get("prediction") or "")
            row["methods"][method] = {
                "exact_match": float(record["exact_match"]),
                "token_f1": float(record["token_f1"]),
                "prediction": prediction,
            }
        rows.append(row)
    return rows


def _contains_em(gold: str | None, prediction: str) -> float:
    if not gold:
        return 0.0
    return 1.0 if _norm_text(gold) in _norm_text(prediction) else 0.0


def _metric_values_with_contains(
    rows: list[dict[str, Any]],
    dataset_index: dict[str, dict[str, Any]],
    method: str,
    metric: str,
) -> np.ndarray:
    values: list[float] = []
    for row in rows:
        record = row["methods"].get(method)
        if record is None:
            continue
        if metric == "raw_em":
            values.append(record["exact_match"])
        elif metric == "token_f1":
            values.append(record["token_f1"])
        else:
            gold = dataset_index[row["sample_id"]]["gold_answer"]
            values.append(_contains_em(gold, record["prediction"]))
    return np.asarray(values, dtype=float)


def _bootstrap_ci(values: np.ndarray, n_boot: int, seed: int) -> dict[str, float]:
    rng = np.random.default_rng(seed)
    n = len(values)
    if n == 0:
        return {"mean": 0.0, "ci_low": 0.0, "ci_high": 0.0}
    means = rng.choice(values, size=(n_boot, n), replace=True).mean(axis=1)
    low, high = np.percentile(means, [2.5, 97.5])
    return {
        "mean": float(values.mean()),
        "ci_low": float(low),
        "ci_high": float(high),
    }


def _paired_permutation_test(
    values_a: np.ndarray, values_b: np.ndarray, n_perm: int, seed: int
) -> dict[str, float | int]:
    diff = values_a - values_b
    n = len(diff)
    observed = abs(float(diff.mean())) if n else 0.0
    if n == 0:
        return {"p_value": 1.0, "n_pairs": 0, "mean_diff": 0.0, "observed_abs_diff": 0.0}
    rng = np.random.default_rng(seed)
    signs = rng.choice(np.array([-1.0, 1.0]), size=(n_perm, n))
    perm_means = np.abs((signs * diff).mean(axis=1))
    p_value = float((np.sum(perm_means >= observed) + 1) / (n_perm + 1))
    return {
        "p_value": p_value,
        "n_pairs": n,
        "mean_diff": float(diff.mean()),
        "observed_abs_diff": observed,
    }


class _OkapiBM25:
    """Minimal Okapi BM25 over pre-tokenized documents (no external deps)."""

    def __init__(
        self, corpus_tokens: list[list[str]], k1: float = 1.5, b: float = 0.75
    ) -> None:
        self.k1 = k1
        self.b = b
        self.doc_count = len(corpus_tokens)
        self.doc_lens = [len(doc) for doc in corpus_tokens]
        self.avgdl = (sum(self.doc_lens) / self.doc_count) if self.doc_count else 0.0
        self.tf: list[Counter[str]] = [Counter(doc) for doc in corpus_tokens]
        df: Counter[str] = Counter()
        for counts in self.tf:
            df.update(counts.keys())
        self.idf = {
            term: math.log(1.0 + (self.doc_count - freq + 0.5) / (freq + 0.5))
            for term, freq in df.items()
        }

    def scores(self, query_tokens: list[str]) -> list[float]:
        output = [0.0] * self.doc_count
        for i, counts in enumerate(self.tf):
            if not self.doc_lens[i]:
                continue
            score = 0.0
            denom_norm = self.k1 * (1.0 - self.b + self.b * self.doc_lens[i] / self.avgdl)
            for term in query_tokens:
                freq = counts.get(term)
                if not freq:
                    continue
                idf = self.idf.get(term)
                if idf is None:
                    continue
                score += idf * (freq * (self.k1 + 1.0)) / (freq + denom_norm)
            output[i] = score
        return output


def _tokenize(text: str) -> list[str]:
    return re.findall(r"\w+", text.lower())


def _bm25_coverage(
    rows: list[dict[str, Any]],
    dataset_index: dict[str, dict[str, Any]],
    run_dir: Path,
    methods_for_k: Sequence[str],
) -> dict[str, Any]:
    retrieval_path = run_dir / "retrieval.jsonl"
    qemr_counts: list[int] = []
    retrieved_sessions: dict[tuple[str, str], set[Any]] = {}
    observed_methods: list[str] = []
    if retrieval_path.exists():
        for line in retrieval_path.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            method = str(record["method"])
            item_sessions: set[Any] = {
                ref.get("session_id")
                for item in record.get("packed_items", [])
                for ref in item.get("evidence_refs", [])
                if ref.get("session_id")
            }
            sessions = item_sessions
            retrieved_sessions[(method, str(record["sample_id"]))] = sessions
            if method not in observed_methods:
                observed_methods.append(method)
            if method in methods_for_k:
                qemr_counts.append(len(record.get("packed_items", [])))
    k_aligned = int(np.median(qemr_counts)) if qemr_counts else 10

    bm25_hits: dict[str, bool] = {}
    per_sample: dict[str, dict[str, Any]] = {}
    for row in rows:
        sample_id = row["sample_id"]
        info = dataset_index[sample_id]
        docs = info["turns"]
        corpus_tokens = [_tokenize(doc["content"]) for doc in docs]
        bm25 = _OkapiBM25(corpus_tokens)
        query_tokens = _tokenize(info["question_text"])
        ranked = np.argsort(np.asarray(bm25.scores(query_tokens)))[::-1][:k_aligned]
        top_sessions = {docs[int(i)]["session_id"] for i in ranked}
        gold_sessions = set(info["answer_session_ids"])
        hit = bool(top_sessions & gold_sessions)
        bm25_hits[sample_id] = hit
        per_sample[sample_id] = {
            "k": k_aligned,
            "bm25_hit": hit,
            "top_k_sessions": sorted(top_sessions),
        }

    method_hits: dict[str, float] = {}
    method_details: dict[str, Any] = {}
    for method in [*observed_methods, "bm25_turn_topk"]:
        hits: list[bool] = []
        for row in rows:
            sample_id = row["sample_id"]
            if method == "bm25_turn_topk":
                hits.append(bm25_hits[sample_id])
                continue
            retrieved = retrieved_sessions.get((method, sample_id))
            if retrieved is None:
                continue
            hits.append(bool(retrieved & set(dataset_index[sample_id]["answer_session_ids"])))
        if hits:
            method_hits[method] = sum(hits) / len(hits)
            method_details[method] = {"hit_rate": method_hits[method], "n": len(hits)}

    return {
        "k_aligned_to_qemr_median_packed_items": k_aligned,
        "note": (
            "Retrieval-level proxy: fraction of samples whose top-k units "
            "(BM25 turns vs method packed-item evidence refs) touch at least one "
            "gold answer session. Not an end-to-end EM metric."
        ),
        "methods": method_details,
        "per_sample_bm25": per_sample,
    }


def _render_markdown(report: dict[str, Any], out_md: Path) -> None:
    lines: list[str] = ["# S7 benchmark statistics", ""]
    lines.append(f"- Run: `{report['run_dir']}`")
    lines.append(f"- Generated: {report['generated_at']}")
    lines.append(f"- Completed samples: {report['completed_samples']}")
    lines.append("")

    lines.append("## Metrics with bootstrap 95% CI")
    lines.append("")
    lines.append("| method | metric | mean | ci_low | ci_high | n |")
    lines.append("|---|---|---|---|---|---|")
    for method, metrics in report["metrics"].items():
        for metric in METRICS:
            entry = metrics[metric]
            lines.append(
                f"| {method} | {metric} | {entry['mean']:.4f} "
                f"| {entry['ci_low']:.4f} | {entry['ci_high']:.4f} | {entry['n']} |"
            )
    lines.append("")
    lines.append(
        "Notes: `contains_em` is a lenient upper bound (normalized gold substring "
        "of prediction). `token_f1` is the primary metric."
    )
    lines.append("")

    lines.append("## Paired permutation tests (sign-flip)")
    lines.append("")
    lines.append("| pair | scope | metric | mean_diff (a-b) | p_value | n_pairs |")
    lines.append("|---|---|---|---|---|---|")
    for pair, scopes in report["paired_tests"].items():
        for scope, entries in scopes.items():
            for metric, entry in entries.items():
                lines.append(
                    f"| {pair} | {scope} | {metric} | {entry['mean_diff']:+.4f} "
                    f"| {entry['p_value']:.4f} | {entry['n_pairs']} |"
                )
    lines.append("")

    bm25 = report["bm25_coverage"]
    lines.append("## BM25 retrieval-coverage proxy baseline")
    lines.append("")
    k_aligned = bm25["k_aligned_to_qemr_median_packed_items"]
    lines.append(f"- k aligned to QEMR median packed items: **{k_aligned}**")
    lines.append("")
    lines.append("| method | gold-session hit rate | n |")
    lines.append("|---|---|---|")
    for method, detail in bm25["methods"].items():
        lines.append(
            f"| {method} | {detail['hit_rate']:.4f} | {detail['n']} |"
        )
    lines.append("")
    lines.append(bm25["note"])
    lines.append("")

    out_md.write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--out-json", type=Path, default=Path("runs/analysis/stats.json"))
    parser.add_argument("--out-md", type=Path, default=Path("runs/analysis/stats.md"))
    parser.add_argument("--n-boot", type=int, default=10000)
    parser.add_argument("--n-perm", type=int, default=10000)
    parser.add_argument("--seed", type=int, default=20260822)
    args = parser.parse_args(argv)

    config = load_config(args.config)
    summary = json.loads((args.run_dir / "summary.json").read_text(encoding="utf-8"))
    methods = [str(m) for m in summary.get("methods", {})]
    rows = _load_run_records(args.run_dir, methods)
    if not rows:
        print("ERROR: no completed samples found in run dir", file=sys.stderr)
        return 1
    dataset_index = _load_dataset_index(config.dataset_path, [row["sample_id"] for row in rows])

    metrics_block: dict[str, dict[str, Any]] = {}
    for method in methods:
        metrics_block[method] = {}
        for metric_index, metric in enumerate(METRICS):
            values = _metric_values_with_contains(rows, dataset_index, method, metric)
            boot = _bootstrap_ci(values, args.n_boot, args.seed + metric_index)
            boot["n"] = int(len(values))
            metrics_block[method][metric] = boot

    paired_tests: dict[str, dict[str, dict[str, dict[str, float | int]]]] = {}
    categories = sorted({row["category"] for row in rows})
    for pair in PERM_TEST_PAIRS:
        name = f"{pair[0]}_vs_{pair[1]}"
        paired_tests[name] = {}

        def _scope_rows(scope: str) -> list[dict[str, Any]]:
            if scope == "overall":
                return rows
            return [row for row in rows if row["category"] == scope]

        for scope in ["overall", *categories]:
            scoped = _scope_rows(scope)
            paired_tests[name][scope] = {}
            for metric in METRICS:
                values_a = _metric_values_with_contains(scoped, dataset_index, pair[0], metric)
                values_b = _metric_values_with_contains(scoped, dataset_index, pair[1], metric)
                keep = min(len(values_a), len(values_b))
                paired_tests[name][scope][metric] = _paired_permutation_test(
                    values_a[:keep], values_b[:keep], args.n_perm, args.seed
                )

    bm25 = _bm25_coverage(rows, dataset_index, args.run_dir, methods_for_k=("full",))

    report = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "run_dir": str(args.run_dir),
        "config": str(args.config),
        "seed": args.seed,
        "n_boot": args.n_boot,
        "n_perm": args.n_perm,
        "primary_metric": PRIMARY_METRIC,
        "completed_samples": len(rows),
        "expected_samples": summary.get("sample_validation", {}).get("expected_sample_count"),
        "sample_validation": summary.get("sample_validation"),
        "categories_present": categories,
        "metrics": metrics_block,
        "paired_tests": paired_tests,
        "bm25_coverage": bm25,
    }

    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _render_markdown(report, args.out_md)
    print(f"wrote {args.out_json}")
    print(f"wrote {args.out_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
