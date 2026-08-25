"""S3 Step 4: M2 stale-memory judge (B2 fix; judge ≠ reader).

SUPERSEDE fired 109 times in S2 (consolidation layer). M2 asks whether the
109 superseded memories are actually *consumed* by retrieval, or whether
``full`` (ETEC + QEMR) still serves stale values that ``event_no_etec``
(non-ETEC) would also serve.

Judge model: ``minimax-m3`` via the Ark API (``ARK_*`` env). The judge is
**not** ``mimo-v2.5`` (the reader/extractor) — different model family
(spec N8 / B4; AGENTS.md "LLM judges require cached inputs/outputs and a
documented judge model"). Judge inputs/outputs are cached to
``<source-run>/m2_judge_cache/`` for reproducibility.

CLI::

    uv run python -m benchmarks.mechanism.m2_stale_judge \\
        --source-run runs/publication/m13-longmemeval-test50-mimo-v2-factslot \\
        --judge-model minimax-m3
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Sequence
from pathlib import Path
from typing import Any

# S8 Step 2: the diagnostic AF_INET ``getaddrinfo`` filter that lived
# here has been removed. The shim masked the real network path (forcing
# AF_INET) and was a Phase-A diagnostic-only network patch from S3;
# production code in ``src/evoeventmem`` was always untouched. If
# dual-stack routing issues resurface, fix the gateway / DNS layer,
# not this script.

DEFAULT_SOURCE_RUN = Path("runs/publication/m13-longmemeval-test50-mimo-v2-factslot")
DEFAULT_JUDGE_MODEL = "minimax-m3"
JUDGE_CACHE_NAMESPACE = "m2_judge"
MAX_RETRY_ATTEMPTS = 3
RETRY_BACKOFF = 4.0

JUDGE_SYSTEM_PROMPT = (
    "You are a strict judge comparing two answers to the same question. "
    "One answer comes from a memory system WITH temporal consolidation "
    "(SUPERSEDE: when a fact changes, the old value is replaced). "
    "The other comes from a system WITHOUT consolidation (both old and "
    "new values exist as duplicates). "
    "Decide which answer is LESS STALE (more up-to-date / reflects the "
    "most recent value)."
)

JUDGE_USER_TEMPLATE = """Question: {question}
Gold answer: {gold}

Answer A (full, WITH consolidation/SUPERSEDE): {full_pred}
Answer B (event_no_etec, WITHOUT consolidation): {etec_pred}

Which answer is LESS STALE (more up-to-date)? Consider: if the underlying
fact changed over time, the less-stale answer reflects the newer value.
If both reflect the same value or neither addresses recency, mark "tie".

Respond ONLY with a JSON object on one line:
{{"less_stale": "A" | "B" | "tie", "reason": "one short sentence"}}
"""


def _judge_cache_dir(source_run: Path) -> Path:
    cache_dir = source_run / "m2_judge_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def _cache_key(payload: dict[str, Any]) -> str:
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    return f"sha256-{digest}"


def _cache_path(cache_dir: Path, key: str) -> Path:
    return cache_dir / f"{key}.json"


def _judge_get(
    cache_dir: Path, payload: dict[str, Any]
) -> dict[str, Any] | None:
    key = _cache_key(payload)
    path = _cache_path(cache_dir, key)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _judge_set(
    cache_dir: Path,
    payload: dict[str, Any],
    response_text: str,
) -> str:
    key = _cache_key(payload)
    path = _cache_path(cache_dir, key)
    path.write_text(
        json.dumps(
            {"input": payload, "output": response_text},
            sort_keys=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return key


def _post_judge(
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict[str, str]],
    timeout_s: float,
) -> str:
    """Call the judge chat endpoint; return the raw text response."""
    url = f"{base_url.rstrip('/')}/chat/completions"
    body = json.dumps(
        {
            "model": model,
            "messages": messages,
            "max_tokens": 256,
            "temperature": 0.0,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "evoeventmem-s3-m2-judge/1.0",
        },
        method="POST",
    )
    last_error: Exception | None = None
    for attempt in range(MAX_RETRY_ATTEMPTS):
        if attempt:
            time.sleep(RETRY_BACKOFF * (2 ** (attempt - 1)))
        try:
            with urllib.request.urlopen(request, timeout=timeout_s) as response:
                decoded = json.loads(response.read().decode("utf-8"))
            return str(decoded["choices"][0]["message"]["content"])
        except urllib.error.HTTPError as exc:
            if exc.code < 500 and exc.code != 429:
                raise RuntimeError(f"judge HTTP error: {exc}") from exc
            last_error = exc
        except (urllib.error.URLError, ConnectionError, TimeoutError) as exc:
            last_error = exc
    raise RuntimeError(
        f"judge request failed after {MAX_RETRY_ATTEMPTS} attempts: {last_error}"
    )


def _parse_judge_response(text: str) -> dict[str, Any]:
    """Parse the judge's JSON response; tolerate surrounding prose."""
    import re

    match = re.search(r"\{[^{}]*\}", text)
    if not match:
        return {
            "less_stale": "parse_error",
            "reason": f"no JSON object found in: {text[:120]}",
            "raw": text,
        }
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        return {
            "less_stale": "parse_error",
            "reason": f"JSON decode error: {exc}",
            "raw": text,
        }
    less_stale = str(parsed.get("less_stale", "")).strip().upper()
    # Normalize A/B/TIE to lowercase for consistent count keys.
    if less_stale == "A":
        return {
            "less_stale": "a",
            "reason": str(parsed.get("reason", ""))[:200],
            "raw": text,
        }
    if less_stale == "B":
        return {
            "less_stale": "b",
            "reason": str(parsed.get("reason", ""))[:200],
            "raw": text,
        }
    if less_stale == "TIE":
        return {
            "less_stale": "tie",
            "reason": str(parsed.get("reason", ""))[:200],
            "raw": text,
        }
    return {
        "less_stale": "parse_error",
        "reason": f"unexpected less_stale value: {less_stale}",
        "raw": text,
    }


# S8 Step 3c: the M2 judge runs **only** on the temporal-salient subset
# where the gold question_type has a time-ordered answer that
# consolidation can change. The S3 §4 74% tie on single-session-user
# was a correctness/staleness confusion artefact (single-session-user
# questions have no temporal-salient answer for SUPERSEDE to change).
# Pre-registered in docs/S8-PREREGISTRATION.md §4.
TEMPORAL_SALIENT_QUESTION_TYPES = frozenset(
    {
        "temporal-reasoning",
        "knowledge-update",
        "multi-session",
    }
)


def _load_differing_samples(
    source_run: Path,
    *,
    temporal_salient_only: bool = True,
) -> list[dict[str, Any]]:
    """Load samples where ``full`` prediction != ``event_no_etec`` prediction.

    When ``temporal_salient_only`` is True (S8 Step 3c default), the
    judge scope is restricted to the temporal-salient subset
    (``temporal-reasoning`` + ``knowledge-update`` + ``multi-session``)
    where SUPERSEDE can actually change the reader-visible answer.
    Single-session-* questions have no temporal-salient answer and are
    excluded to avoid the S3 §4 correctness/staleness confusion.
    """
    samples_dir = source_run / "samples"
    if not samples_dir.exists():
        raise FileNotFoundError(f"samples dir missing: {samples_dir}")
    # Load gold answers + question_type from the dataset.
    dataset_path = Path("data/raw/longmemeval/longmemeval_s_cleaned.json")
    gold_by_id: dict[str, str] = {}
    qtype_by_id: dict[str, str] = {}
    if dataset_path.exists():
        data = json.loads(dataset_path.read_bytes())
        for record in data:
            qid = record["question_id"]
            gold_by_id[qid] = record.get("answer", "")
            qtype_by_id[qid] = record.get("question_type", "")
    out: list[dict[str, Any]] = []
    for path in sorted(samples_dir.glob("*.json")):
        if "extraction_snapshot" in path.name:
            continue
        record = json.loads(path.read_text(encoding="utf-8"))
        methods = record.get("methods", {})
        full = methods.get("full", {})
        etec = methods.get("event_no_etec", {})
        full_pred = full.get("prediction", "")
        etec_pred = etec.get("prediction", "")
        if full_pred == etec_pred:
            continue
        qid = record.get("sample_id") or record.get("question_id") or path.stem
        qtype = qtype_by_id.get(qid, "") or record.get("question_type", "")
        if temporal_salient_only and qtype not in TEMPORAL_SALIENT_QUESTION_TYPES:
            continue
        out.append(
            {
                "question_id": qid,
                "question_type": qtype,
                "question": record.get("question")
                or _question_text_from_dataset(qid)
                or "(question text unavailable)",
                "gold": gold_by_id.get(qid, ""),
                "full_pred": full_pred,
                "etec_pred": etec_pred,
                "full_em": full.get("exact_match", 0),
                "etec_em": etec.get("exact_match", 0),
            }
        )
    return out


def _question_text_from_dataset(qid: str) -> str:
    """Fallback to load question text from the dataset if not in sample."""
    dataset_path = Path("data/raw/longmemeval/longmemeval_s_cleaned.json")
    if not dataset_path.exists():
        return ""
    data = json.loads(dataset_path.read_bytes())
    for record in data:
        if record.get("question_id") == qid:
            return record.get("question", "")
    return ""


def run_judge(
    source_run: Path,
    judge_model: str,
    judge_base_url: str,
    judge_api_key: str,
    timeout_s: float,
    *,
    temporal_salient_only: bool = True,
) -> dict[str, Any]:
    """Run the M2 judge on differing-prediction samples.

    When ``temporal_salient_only`` is True (S8 Step 3c default), the
    judge scope is restricted to the temporal-salient subset
    (``temporal-reasoning`` + ``knowledge-update`` + ``multi-session``).
    Pre-registered in ``docs/S8-PREREGISTRATION.md`` §4.
    """
    samples = _load_differing_samples(
        source_run, temporal_salient_only=temporal_salient_only
    )
    cache_dir = _judge_cache_dir(source_run)
    results: list[dict[str, Any]] = []
    counts = {"a": 0, "b": 0, "tie": 0, "parse_error": 0}
    for sample in samples:
        payload = {
            "judge_model": judge_model,
            "question": sample["question"],
            "gold": sample["gold"],
            "full_pred": sample["full_pred"],
            "etec_pred": sample["etec_pred"],
        }
        cached = _judge_get(cache_dir, payload)
        if cached is not None:
            raw = cached.get("output", "")
        else:
            messages = [
                {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": JUDGE_USER_TEMPLATE.format(
                        question=sample["question"],
                        gold=sample["gold"],
                        full_pred=sample["full_pred"],
                        etec_pred=sample["etec_pred"],
                    ),
                },
            ]
            try:
                raw = _post_judge(
                    judge_base_url, judge_api_key, judge_model, messages, timeout_s
                )
            except RuntimeError as exc:
                print(
                    f"[m2] judge call failed for {sample['question_id']}: {exc}",
                    file=sys.stderr,
                )
                raw = ""
            _judge_set(cache_dir, payload, raw)
        parsed = _parse_judge_response(raw)
        counts[parsed["less_stale"]] = counts.get(parsed["less_stale"], 0) + 1
        results.append(
            {
                "question_id": sample["question_id"],
                "full_em": sample["full_em"],
                "etec_em": sample["etec_em"],
                "full_pred": sample["full_pred"],
                "etec_pred": sample["etec_pred"],
                "judge_less_stale": parsed["less_stale"],
                "judge_reason": parsed["reason"],
                "judge_raw": parsed.get("raw", raw),
            }
        )
    total = len(results)
    return {
        "judge_model": judge_model,
        "reader_model": "mimo-v2.5",
        "judge_is_reader": judge_model == "mimo-v2.5",
        "n_differing": total,
        "counts": counts,
        "full_less_stale_count": counts["a"],
        "etec_less_stale_count": counts["b"],
        "tie_count": counts["tie"],
        "parse_error_count": counts["parse_error"],
        # S8 Step 3c: scope metadata so the report can state which
        # question_types were judged (temporal-salient subset by default).
        "temporal_salient_only": temporal_salient_only,
        "judge_scope": (
            "temporal-salient subset (temporal-reasoning + knowledge-"
            "update + multi-session)"
            if temporal_salient_only
            else "all question_types (legacy S3 §4 scope)"
        ),
        "results": results,
    }


def build_report(source_run: Path, result: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# S3 Step 4: M2 stale-memory judge")
    lines.append("")
    lines.append(f"- **Source run**: `{source_run}`")
    lines.append(f"- **Judge model**: `{result['judge_model']}` (reader is `mimo-v2.5`)")
    lines.append(
        f"- **Judge ≠ reader**: `{not result['judge_is_reader']}` "
        "(spec N8 / B4; AGENTS.md cached judge requirement)"
    )
    lines.append(
        f"- **Differing-prediction samples judged**: {result['n_differing']}"
    )
    lines.append(
        f"- **Judge scope**: {result.get('judge_scope', 'n/a')} "
        "(S8 Step 3c pre-registration: temporal-salient subset only; "
        "see docs/S8-PREREGISTRATION.md §4)"
    )
    lines.append("")
    lines.append("## Stale/fresh verdict")
    lines.append("")
    n = max(result["n_differing"], 1)
    lines.append(
        f"- **full (WITH SUPERSEDE) less stale**: {result['full_less_stale_count']} "
        f"({result['full_less_stale_count'] / n * 100:.1f}%)"
    )
    lines.append(
        f"- **event_no_etec (WITHOUT SUPERSEDE) less stale**: "
        f"{result['etec_less_stale_count']} "
        f"({result['etec_less_stale_count'] / n * 100:.1f}%)"
    )
    lines.append(
        f"- **tie**: {result['tie_count']} "
        f"({result['tie_count'] / n * 100:.1f}%)"
    )
    if result["parse_error_count"]:
        lines.append(
            f"- **parse errors**: {result['parse_error_count']} "
            f"({result['parse_error_count'] / n * 100:.1f}%)"
        )
    lines.append("")
    lines.append("## Per-sample verdicts")
    lines.append("")
    lines.append(
        "| question_id | full EM | etec EM | less_stale | full_pred | "
        "etec_pred | reason |"
    )
    lines.append("|---|---|---|---|---|---|---|")
    for r in result["results"]:
        fp = r["full_pred"].replace("|", "\\|").replace("\n", " ")[:40]
        ep = r["etec_pred"].replace("|", "\\|").replace("\n", " ")[:40]
        reason = r["judge_reason"].replace("|", "\\|").replace("\n", " ")[:60]
        lines.append(
            f"| {r['question_id']} | {r['full_em']} | {r['etec_em']} | "
            f"{r['judge_less_stale']} | {fp} | {ep} | {reason} |"
        )
    lines.append("")
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="S3 Step 4: M2 stale-memory judge (minimax-m3 ≠ mimo-v2.5)."
    )
    parser.add_argument(
        "--source-run",
        type=Path,
        default=DEFAULT_SOURCE_RUN,
        help=f"v2 run directory (default: {DEFAULT_SOURCE_RUN})",
    )
    parser.add_argument(
        "--judge-model",
        type=str,
        default=DEFAULT_JUDGE_MODEL,
        help=f"Judge model id (default: {DEFAULT_JUDGE_MODEL})",
    )
    parser.add_argument(
        "--judge-base-url",
        type=str,
        default=None,
        help="Judge API base URL. Default: $ARK_BASE_URL",
    )
    parser.add_argument(
        "--judge-api-key-env",
        type=str,
        default="ARK_API_KEY",
        help="Environment variable holding the judge API key (default: ARK_API_KEY)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write the markdown report to this path.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=60.0,
        help="Per-judge-call timeout in seconds (default: 60).",
    )
    parser.add_argument(
        "--include-all-types",
        action="store_true",
        help=(
            "S8 Step 3c override: include single-session-* in the judge "
            "scope (legacy S3 §4 behaviour). Default is temporal-salient "
            "only (temporal-reasoning + knowledge-update + multi-session) "
            "per docs/S8-PREREGISTRATION.md §4."
        ),
    )
    args = parser.parse_args(argv)

    base_url = args.judge_base_url or os.environ.get("ARK_BASE_URL")
    api_key = os.environ.get(args.judge_api_key_env)
    if not base_url:
        print("error: ARK_BASE_URL not set", file=sys.stderr)
        return 1
    if not api_key:
        print(
            f"error: {args.judge_api_key_env} not set", file=sys.stderr
        )
        return 1
    if not args.source_run.exists():
        print(f"error: source run {args.source_run} not found", file=sys.stderr)
        return 1

    result = run_judge(
        args.source_run,
        args.judge_model,
        base_url,
        api_key,
        args.timeout,
        temporal_salient_only=not args.include_all_types,
    )
    json_path = args.source_run / "m2_judge_report.json"
    json_path.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    report = build_report(args.source_run, result)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report, encoding="utf-8")
        print(f"M2 judge report written to {args.output}", file=sys.stderr)
    else:
        out_md = args.source_run / "m2_judge_report.md"
        out_md.write_text(report, encoding="utf-8")
        print(f"M2 judge report written to {out_md}", file=sys.stderr)
    print(
        f"full less stale: {result['full_less_stale_count']}, "
        f"etec less stale: {result['etec_less_stale_count']}, "
        f"tie: {result['tie_count']}, "
        f"parse errors: {result['parse_error_count']}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
