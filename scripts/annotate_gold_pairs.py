"""Generate the gold-pair annotation review sheet (O09 Eval A, spec §4.2).

For each requested LongMemEval question id, emit one JSONL record with the
question text, official answer, ``answer_session_ids``, every haystack session
with its full turn texts (raw turn ids following the normalization scheme
``<session_id>:<index>`` over non-empty turns), and candidate ``t_q`` values.
The human annotator fills the blank ``gold`` fields; ``benchmarks.mechanism.gold``
then validates the annotated pairs.

Usage::

    uv run python scripts/annotate_gold_pairs.py \
        --dataset data/raw/longmemeval/longmemeval_s_cleaned.json \
        --question-ids <32 ids> \
        --out runs/mechanism/gold/review_sheet.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from benchmarks.mechanism.gold import (
    GOLD_REVIEW_SHEET_SCHEMA_VERSION,
    iter_raw_records,
)


def _parse_datetime(value: str) -> datetime | None:
    for fmt in (
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%Y-%m-%d %H:%M:%S",
        "%Y/%m/%d (%a) %H:%M",
    ):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=UTC)
        except ValueError:
            pass
    return None


def build_review_record(record: dict[str, Any]) -> dict[str, Any]:
    session_ids = [str(item) for item in record.get("haystack_session_ids", [])]
    session_dates = [str(item) for item in record.get("haystack_dates", [])]
    sessions = record.get("haystack_sessions", [])
    question_date = record.get("question_date")
    t_q_candidates: list[dict[str, str]] = []
    if question_date:
        t_q_candidates.append({"label": "question_date", "value": str(question_date)})
    answer_session_ids = [str(item) for item in record.get("answer_session_ids", [])]
    for session_id, session_date in zip(session_ids, session_dates, strict=False):
        if session_id in answer_session_ids:
            t_q_candidates.append(
                {"label": f"answer_session_first_turn:{session_id}", "value": session_date}
            )

    normalized_sessions: list[dict[str, Any]] = []
    for session_id, session_date, turns in zip(session_ids, session_dates, sessions, strict=False):
        turn_rows: list[dict[str, Any]] = []
        if isinstance(turns, list):
            for index, turn in enumerate(turns):
                if not isinstance(turn, dict):
                    continue
                content = str(turn.get("content") or "").strip()
                if not content:
                    continue
                turn_rows.append(
                    {
                        "turn_id": f"{session_id}:{index}",
                        "speaker": str(turn.get("role") or ""),
                        "content": content,
                    }
                )
        normalized_sessions.append(
            {
                "session_id": session_id,
                "date": session_date,
                "is_answer_session": session_id in answer_session_ids,
                "turns": turn_rows,
            }
        )

    return {
        "schema_version": GOLD_REVIEW_SHEET_SCHEMA_VERSION,
        "question_id": record["question_id"],
        "question_type": record.get("question_type"),
        "question": record.get("question"),
        "answer": record.get("answer"),
        "question_date": question_date,
        "answer_session_ids": answer_session_ids,
        "t_q_candidates": t_q_candidates,
        "sessions": normalized_sessions,
        "gold": {
            "subject": "",
            "attribute": "",
            "old_value": "",
            "new_value": "",
            "old_value_turn_ids": [],
            "new_value_turn_ids": [],
            "t_q": "",
            "t_old": "",
            "multi_valued": False,
            "gold_action": "",
            "notes": "",
        },
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate the gold-pair annotation sheet.")
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--question-ids", nargs="+", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    records = {record["question_id"]: record for record in iter_raw_records(args.dataset)}
    missing = sorted(set(args.question_ids) - set(records))
    if missing:
        print(f"question ids not found in dataset: {missing}", file=sys.stderr)
        return 2

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as handle:
        for question_id in args.question_ids:
            record = build_review_record(records[question_id])
            handle.write(
                json.dumps(record, sort_keys=True, ensure_ascii=False)
            )
            handle.write("\n")
    print(f"wrote {len(args.question_ids)} review rows to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
