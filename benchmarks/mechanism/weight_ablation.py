"""S3 Step 2: QEMR weight-profile ablation runner.

Re-runs **only the ``full`` retrieval method** on the v2 extraction snapshot
under three diagnostic weight strategies, on the same 50 questions, same
reader model (``mimo-v2.5``), same token budget (4096), same embedding
model (``qwen3-embedding-0.6b``). Only the QEMR weight profile differs.

Scope boundary (``docs/S3-execution-prompt.md`` Step 2, lines 203-242):

- The production ``QEMR_WEIGHT_PROFILES`` dict is **not** modified
  (``git diff src/evoeventmem/retrieval.py`` only adds the ablation strategy
  enum + ``resolve_weights`` branches).
- Ablation arms are observable: the ``strategy`` field on each
  ``QEMRRetrievalResult`` is recorded (no silent fallback to vector
  retrieval — AGENTS.md).
- Same model / same budget / same reader (AGENTS.md anti-mixed-methods).

Cache strategy: a composite cache reads embeddings + extraction-time chat
hits from the v2 run dir (read-only) and writes reader chat misses to the
ablation dir. The v2 run dir is never mutated.

CLI::

    uv run python -m benchmarks.mechanism.weight_ablation \\
        --source-run runs/publication/m13-longmemeval-test50-mimo-v2-factslot \\
        --output-dir runs/publication/m13-longmemeval-test50-mimo-v2-ablation \\
        --arms no_temporal no_graph uniform
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
import tomllib
from collections.abc import Sequence
from pathlib import Path
from statistics import mean
from typing import Any

from benchmarks.common.artifacts import ExtractionSnapshot
from benchmarks.common.memory_inputs import materialize_event_store
from benchmarks.common.metrics import compute_answer_metrics
from benchmarks.common.normalization import iter_longmemeval_records
from benchmarks.common.providers import ProviderKind
from benchmarks.longmemeval.run import LongMemEvalConfig
from evoeventmem.core.ports import ChatMessage, ChatModel, EmbeddingModel
from evoeventmem.infra.openai_compatible import (
    OpenAICompatibleChatClient,
    OpenAICompatibleConfig,
    OpenAICompatibleEmbeddingClient,
)
from evoeventmem.models.cache import (
    CachedChatModel,
    CachedEmbeddingModel,
    FileModelCache,
)
from evoeventmem.retrieval import RetrievalHarness, RetrievalStrategy

DEFAULT_SOURCE_RUN = Path("runs/publication/m13-longmemeval-test50-mimo-v2-factslot")
DEFAULT_OUTPUT_DIR = Path("runs/publication/m13-longmemeval-test50-mimo-v2-ablation")
DEFAULT_DATASET = Path("data/raw/longmemeval/longmemeval_s_cleaned.json")

# Smoke-test override (set via --sample-limit). None means run all samples.
_SMOKE_LIMIT: int | None = None

# Diagnostic IPv4 preference: opencode.ai resolves to both IPv4 and IPv6,
# and the IPv6 path intermittently resets connections on this host. The v2
# run completed before the issue surfaced; S3 ablation re-runs hit it.
# This monkeypatch filters ``getaddrinfo`` to AF_INET (IPv4) only, so the
# reader's ``urllib.request`` calls stay on the working IPv4 path. This is
# a diagnostic-only network shim in the ablation script; production code
# (``src/evoeventmem``) is untouched.
_orig_getaddrinfo = socket.getaddrinfo


def _ipv4_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):  # type: ignore[no-untyped-def]
    return _orig_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)


socket.getaddrinfo = _ipv4_getaddrinfo

# Outer retry for transient server-side connection resets. The production
# ``_post_json`` retries 3 times with short backoff; on flaky days that is
# not enough. This wrapper adds a longer outer loop around the reader call
# only (retrieval is deterministic and cache-fed, no network).
READER_OUTER_RETRIES = 5
READER_OUTER_BACKOFF = 8.0


def _reader_generate_with_retry(
    reader: ChatModel,
    messages: Sequence[ChatMessage],
) -> Any:
    last_exc: Exception | None = None
    for attempt in range(READER_OUTER_RETRIES):
        try:
            return reader.generate(messages)
        except RuntimeError as exc:
            last_exc = exc
            if attempt + 1 < READER_OUTER_RETRIES:
                wait = READER_OUTER_BACKOFF * (2**attempt)
                print(
                    f"[ablation] reader attempt {attempt + 1} failed "
                    f"({exc}); retrying in {wait:.0f}s",
                    file=sys.stderr,
                )
                time.sleep(wait)
    raise last_exc  # type: ignore[misc]


ARM_STRATEGIES: dict[str, RetrievalStrategy] = {
    "no_temporal": RetrievalStrategy.QEMR_NO_TEMPORAL,
    "no_graph": RetrievalStrategy.QEMR_NO_GRAPH,
    "uniform": RetrievalStrategy.QEMR_UNIFORM,
    # baseline qemr for same-pipeline re-run sanity (optional arm)
    "qemr": RetrievalStrategy.QEMR,
}


class _CompositeFileModelCache(FileModelCache):
    """Read-through cache: writable first, then a read-only base cache.

    Mirrors ``benchmarks.mechanism.probes._CompositeFileModelCache``. Reads
    hit the writable ablation cache first, then the v2 base cache. Writes
    always go to the writable cache, so the v2 run dir is never mutated.
    """

    def __init__(self, read_only: FileModelCache, writable: FileModelCache) -> None:
        super().__init__(writable.root)
        self._read_only = read_only
        self._writable = writable

    def get(self, namespace: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        hit = self._writable.get(namespace, payload)
        if hit is not None:
            return hit
        return self._read_only.get(namespace, payload)

    def set(
        self, namespace: str, payload: dict[str, Any], value: dict[str, Any]
    ) -> str:
        return self._writable.set(namespace, payload, value)


def _load_snapshots(source_run: Path) -> list[ExtractionSnapshot]:
    path = source_run / "extraction_snapshot.json"
    if not path.exists():
        raise FileNotFoundError(f"extraction_snapshot.json missing at {path}")
    payload = json.loads(path.read_bytes())
    if not isinstance(payload, list):
        raise TypeError(f"expected a JSON array in {path}, got {type(payload).__name__}")
    snapshots = [ExtractionSnapshot.model_validate(entry) for entry in payload]
    if _SMOKE_LIMIT is not None:
        snapshots = snapshots[:_SMOKE_LIMIT]
    return snapshots


def _load_questions(
    dataset_path: Path,
    sample_ids: set[str],
) -> dict[str, dict[str, Any]]:
    """Return {question_id: {question, answer, asked_at}} for the sample set."""
    out: dict[str, dict[str, Any]] = {}
    for record in iter_longmemeval_records(dataset_path):
        for question in record.questions:
            if question.question_id in sample_ids:
                out[question.question_id] = {
                    "question": question.question,
                    "answer": question.answer,
                    "asked_at": question.asked_at,
                }
    return out


def _build_models(
    config: LongMemEvalConfig,
    base_cache: FileModelCache,
    ablation_cache: FileModelCache,
) -> tuple[ChatModel, EmbeddingModel]:
    """Build reader + embedding with a composite cache (v2 base, ablation writable)."""
    composite = _CompositeFileModelCache(base_cache, ablation_cache)
    reader_cfg = config.providers.reader
    embed_cfg = config.providers.embedding
    if reader_cfg.kind is not ProviderKind.OPENAI_COMPATIBLE:
        raise ValueError("S3 ablation requires the openai_compatible provider")
    if embed_cfg.kind is not ProviderKind.OPENAI_COMPATIBLE:
        raise ValueError("S3 ablation requires the openai_compatible embedding provider")

    reader_key = os.environ.get(reader_cfg.api_key_env or "", "")
    if not reader_key:
        raise RuntimeError(
            f"missing environment variable {reader_cfg.api_key_env} for reader"
        )
    reader = CachedChatModel(
        OpenAICompatibleChatClient(
            OpenAICompatibleConfig(
                base_url=reader_cfg.base_url or "",
                api_key=reader_key,
                model=reader_cfg.model_id,
                timeout_s=reader_cfg.timeout_s,
                thinking=reader_cfg.thinking,
                max_tokens=reader_cfg.max_tokens,
            )
        ),
        composite,
    )

    embed_key_env = embed_cfg.api_key_env or ""
    embed_key = os.environ.get(embed_key_env, "not-required")
    embedding = CachedEmbeddingModel(
        OpenAICompatibleEmbeddingClient(
            OpenAICompatibleConfig(
                base_url=embed_cfg.base_url or "",
                api_key=embed_key,
                model=embed_cfg.model_id,
                timeout_s=embed_cfg.timeout_s,
            )
        ),
        composite,
    )
    return reader, embedding


def _run_arm(
    arm: str,
    strategy: RetrievalStrategy,
    snapshots: list[ExtractionSnapshot],
    questions: dict[str, dict[str, Any]],
    reader: ChatModel,
    embedding: EmbeddingModel,
    config: LongMemEvalConfig,
) -> dict[str, Any]:
    """Run one ablation arm across all samples; return per-sample + aggregate EM.

    A transient reader failure (connection reset, timeout) is recorded for
    that sample and the arm continues, so one network hiccup does not waste
    the whole arm. Failed samples are excluded from the EM mean.
    """
    budget = config.max_input_tokens
    max_items = config.max_items_per_source
    max_candidates = config.max_candidates_per_source
    per_sample: list[dict[str, Any]] = []
    em_scores: list[float] = []
    failures: list[dict[str, Any]] = []
    for index, snapshot in enumerate(snapshots):
        qid = snapshot.conversation_id
        question_data = questions.get(qid)
        if question_data is None:
            raise KeyError(f"no question found for snapshot conversation_id={qid}")
        query = question_data["question"]
        answer = question_data["answer"]
        asked_at = question_data["asked_at"]
        user_id = qid
        try:
            store, _ingest = materialize_event_store(
                snapshot,
                apply_etec=True,
                embedding_model=embedding,
                user_id=user_id,
            )
            harness = RetrievalHarness(
                store,
                embedding,
                max_items_per_source=max_items,
                max_candidates_per_source=max_candidates,
            )
            result = harness.retrieve(
                query,
                user_id=user_id,
                strategy=strategy,
                budget_tokens=budget,
                reference_time=asked_at,
            )
            response = _reader_generate_with_retry(reader, result.reader_messages)
            metrics = compute_answer_metrics(answer, response.text)
            em = metrics.exact_match
            em_scores.append(em)
            per_sample.append(
                {
                    "question_id": qid,
                    "strategy": strategy.value,
                    "prediction": response.text,
                    "exact_match": em,
                    "n_packed": len(result.selected_context),
                    "cache_key": response.cache_key,
                }
            )
        except Exception as exc:  # noqa: BLE001 - record + continue
            failures.append(
                {
                    "question_id": qid,
                    "error": f"{type(exc).__name__}: {exc}",
                    "index": index,
                }
            )
            print(
                f"[ablation] arm={arm} sample={qid} FAILED: "
                f"{type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
        if (index + 1) % 10 == 0:
            print(
                f"[ablation] arm={arm} progress {index + 1}/{len(snapshots)}",
                file=sys.stderr,
            )
    overall_em = mean(em_scores) if em_scores else 0.0
    return {
        "arm": arm,
        "strategy": strategy.value,
        "n": len(snapshots),
        "n_scored": len(em_scores),
        "n_failed": len(failures),
        "exact_match": overall_em,
        "per_sample": per_sample,
        "failures": failures,
    }


def _load_config(source_run: Path) -> LongMemEvalConfig:
    """Resolve the LongMemEvalConfig the v2 run used.

    The v2 run dir's manifest records the config; we re-resolve from the
    test50-mimo TOML (same as the v2-factslot run) so the ablation uses the
    same provider roles, token budgets, and harness settings.
    """
    config_path = Path("configs/longmemeval/test50-mimo.toml")
    if not config_path.exists():
        raise FileNotFoundError(f"config {config_path} not found")
    payload = tomllib.loads(config_path.read_text(encoding="utf-8"))
    return LongMemEvalConfig.model_validate(payload)


def build_report(
    source_run: Path,
    output_dir: Path,
    arms: Sequence[str],
    dataset_path: Path,
) -> str:
    snapshots = _load_snapshots(source_run)
    sample_ids = {s.conversation_id for s in snapshots}
    questions = _load_questions(dataset_path, sample_ids)
    config = _load_config(source_run)

    base_cache = FileModelCache(source_run / "model_cache")
    output_dir.mkdir(parents=True, exist_ok=True)
    ablation_cache = FileModelCache(output_dir / "model_cache")
    reader, embedding = _build_models(config, base_cache, ablation_cache)

    # v2 baseline EM (from summary, same model same budget — not a re-run).
    summary_path = source_run / "summary.json"
    v2_full_em = None
    if summary_path.exists():
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        v2_full_em = (
            summary.get("methods", {}).get("full", {}).get("exact_match")
        )

    results: list[dict[str, Any]] = []
    for arm in arms:
        strategy = ARM_STRATEGIES[arm]
        print(f"[ablation] running arm={arm} strategy={strategy.value}...", file=sys.stderr)
        result = _run_arm(
            arm, strategy, snapshots, questions, reader, embedding, config
        )
        results.append(result)
        per_arm_path = output_dir / f"ablation_{arm}.json"
        per_arm_path.write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(
            f"[ablation] arm={arm} EM={result['exact_match']:.4f} "
            f"(n={result['n']})",
            file=sys.stderr,
        )

    # Write the combined JSON.
    combined = {
        "source_run": str(source_run),
        "reader_model": config.providers.reader.model_id,
        "embedding_model": config.providers.embedding.model_id,
        "max_input_tokens": config.max_input_tokens,
        "v2_full_em": v2_full_em,
        "arms": results,
    }
    (output_dir / "ablation_summary.json").write_text(
        json.dumps(combined, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    # Markdown report.
    lines: list[str] = []
    lines.append("# S3 Step 2: QEMR weight-profile ablation")
    lines.append("")
    lines.append(f"- **Source run**: `{source_run}`")
    lines.append(f"- **Reader model**: `{config.providers.reader.model_id}`")
    lines.append(f"- **Embedding model**: `{config.providers.embedding.model_id}`")
    lines.append(f"- **Token budget**: {config.max_input_tokens}")
    lines.append(
        "- Same model / same budget / same reader / same embedding — only "
        "the QEMR weight profile differs (AGENTS.md anti-mixed-methods)."
    )
    lines.append("")
    lines.append("## EM comparison")
    lines.append("")
    lines.append("| arm | strategy | EM | scored | failed | n |")
    lines.append("|---|---|---|---|---|---|")
    if v2_full_em is not None:
        lines.append(
            f"| v2 full (baseline) | qemr | {v2_full_em:.4f} | "
            f"{len(snapshots)} | 0 | {len(snapshots)} |"
        )
    for result in results:
        lines.append(
            f"| {result['arm']} | {result['strategy']} | "
            f"{result['exact_match']:.4f} | {result['n_scored']} | "
            f"{result['n_failed']} | {result['n']} |"
        )
    lines.append("")
    lines.append("_No pre-declared expectation (negative-result framework)._")
    lines.append("")
    lines.append("## Per-arm notes")
    lines.append("")
    for result in results:
        hits = sum(1 for s in result["per_sample"] if s["exact_match"] == 1.0)
        lines.append(
            f"- **{result['arm']}** (`{result['strategy']}`): EM "
            f"{result['exact_match']:.4f} ({hits}/{result['n_scored']} exact"
            + (f", {result['n_failed']} failed" if result["n_failed"] else "")
            + ")."
        )
    lines.append("")
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="S3 Step 2: QEMR weight-profile ablation runner."
    )
    parser.add_argument(
        "--source-run",
        type=Path,
        default=DEFAULT_SOURCE_RUN,
        help=f"v2 run directory (default: {DEFAULT_SOURCE_RUN})",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"ablation output directory (default: {DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=DEFAULT_DATASET,
        help=f"LongMemEval cleaned JSON (default: {DEFAULT_DATASET})",
    )
    parser.add_argument(
        "--arms",
        nargs="+",
        choices=list(ARM_STRATEGIES),
        default=["no_temporal", "no_graph", "uniform"],
        help="Ablation arms to run (default: no_temporal no_graph uniform)",
    )
    parser.add_argument(
        "--sample-limit",
        type=int,
        default=None,
        help="Run only the first N samples (smoke test; default: all 50).",
    )
    args = parser.parse_args(argv)

    if not args.source_run.exists():
        print(f"error: source run {args.source_run} not found", file=sys.stderr)
        return 1
    if not args.dataset.exists():
        print(f"error: dataset {args.dataset} not found", file=sys.stderr)
        return 1

    if args.sample_limit is not None:
        global _SMOKE_LIMIT
        _SMOKE_LIMIT = args.sample_limit

    report = build_report(args.source_run, args.output_dir, args.arms, args.dataset)
    report_path = args.output_dir / "ablation_report.md"
    report_path.write_text(report, encoding="utf-8")
    print(f"ablation report written to {report_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
