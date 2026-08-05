"""Shared fixtures: a synthetic LoCoMo dataset and a synthetic valid run."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

METHODS = (
    "no_memory",
    "full_context",
    "session_summary",
    "vector_rag",
    "event_no_etec",
    "etec",
    "full",
)
MEMORY_METHODS = frozenset({"vector_rag", "event_no_etec", "etec", "full"})


def hash_json(payload: dict) -> str:
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def build_dataset(path: Path, *, sample_id: str = "s1", question_count: int = 60) -> None:
    turns = [
        {"dia_id": f"D{i}:{j}", "speaker": "A" if j % 2 == 0 else "B", "text": f"turn text {j}"}
        for i in range(3)
        for j in range(4)
    ]
    conversation: dict = {"speaker_a": "A", "speaker_b": "B"}
    for i in range(3):
        conversation[f"session_{i}"] = [turns[i * 4 + j] for j in range(4)]
        conversation[f"session_{i}_date_time"] = f"12:00 AM on 0{i + 1} January, 2023"
    questions = []
    for index in range(question_count):
        if index % 5 == 4:
            question = {
                "question": f"Unanswerable question {index}?",
                "evidence": [f"D{index % 3}:{index % 4}"],
                "category": 5,
                "adversarial_answer": "no info",
            }
        else:
            question = {
                "question": f"What is the color of house {index}?",
                "answer": "blue house",
                "evidence": [f"D{index % 3}:{index % 4}"],
                "category": index % 3 + 1,
            }
        questions.append(question)
    dataset = [
        {
            "sample_id": sample_id,
            "conversation": conversation,
            "qa": questions,
            "session_summary": {
                f"session_{i}_summary": f"summary of session {i} about blue house" for i in range(3)
            },
        }
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dataset, indent=2), encoding="utf-8")


def build_run(
    run_dir: Path,
    *,
    dataset_path: str = "data/synthetic/locomo.json",
    max_input_tokens: int = 4096,
    embedding_model_id: str = "qwen3-embedding-0.6b",
    sample_limit: int | None = None,
    question_count: int = 60,
) -> dict:
    config = {
        "schema_version": "locomo.config.v1",
        "run_id_prefix": "synthetic",
        "dataset_path": dataset_path,
        "methods": list(METHODS),
        "provider": "deterministic_fake",
        "embedding_provider": "deterministic_fake",
        "chat_model_id": "deterministic-local-fake",
        "embedding_model_id": embedding_model_id,
        "max_input_tokens": max_input_tokens,
        "max_candidates_per_source": 128,
        "max_items_per_source": 8,
        "sample_limit": sample_limit,
        "live_provider": None,
    }
    run_dir.mkdir(parents=True, exist_ok=True)
    build_dataset(run_dir / dataset_path, question_count=question_count)
    config["dataset_path"] = str(dataset_path)
    summary = {
        "schema_version": "locomo.summary.v1",
        "run_id": "synthetic-run",
        "created_at": "2026-08-05T00:00:00Z",
        "git_commit": "deadbeef",
        "git_dirty": False,
        "config_hash": hash_json(config),
        "dataset_hash": "sha256:synthetic",
        "dataset_path": dataset_path,
        "chat_model_id": "deterministic-local-fake",
        "embedding_model_id": embedding_model_id,
        "embedding_provider": "deterministic_fake",
        "reader_thinking": "n/a",
        "reader_format_directive": "Answer with only the exact answer, no explanation.",
        "extraction_prompt_version": "rule.v1",
        "retrieval_policy_name": "qemr-weight-profiles.v1",
        "router_policy_name": "query-router.rules.v1",
        "consolidation_policy_name": "etec-rule-weighted.v1",
        "reference_time_source": "last_session_timestamp",
        "evidence_mapping": "official_dia_ids_from_turn_refs",
        "structural_match_f1_threshold": 0.6,
        "max_input_tokens": max_input_tokens,
        "max_candidates_per_source": 128,
        "max_items_per_source": 8,
        "sample_validation": {
            "expected_sample_count": 1,
            "completed_sample_count": 1,
            "missing_sample_ids": [],
            "duplicate_sample_ids": [],
            "valid": True,
        },
        "question_validation": {
            "expected_question_count": question_count,
            "completed_question_count": question_count,
            "missing_question_ids": [],
            "duplicate_question_ids": [],
            "valid": True,
        },
        "methods": {},
        "event_structure": {},
    }
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "config.json").write_text(json.dumps(config, indent=2), encoding="utf-8")
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return config


def build_question_artifacts(run_dir: Path, *, question_count: int = 60) -> None:
    sample_path = run_dir / "samples"
    sample_path.mkdir(parents=True, exist_ok=True)
    per_question: dict[str, dict] = {}
    for index in range(question_count):
        question_id = f"s1:qa:{index}"
        adversarial = index % 5 == 4
        gold = None if adversarial else "blue house"
        if adversarial:
            prediction = "I do not know"
            exact_match = 0.0
        elif index % 10 == 0:
            prediction = "blue house"
            exact_match = 1.0
        else:
            prediction = "green car"
            exact_match = 0.0
        context_words = "some unrelated words here" if index % 4 in (1, 2) else "blue house words"
        per_question[question_id] = {
            "question_id": question_id,
            "sample_id": "s1",
            "category": str(index % 3 + 1) if not adversarial else "5",
            "gold_answer": gold,
            "prediction": prediction,
            "exact_match": exact_match,
            "token_f1": exact_match,
            "input_tokens": 100 + index,
            "context_text": context_words,
            "context_truncated": False,
            "predicted_evidence": [] if not adversarial else ["D0:1"],
            "gold_evidence": ["D0:1"] if not adversarial else [],
        }
    for method in METHODS:
        predictions: list[dict] = []
        samples: list[dict] = []
        retrievals: list[dict] = []
        for question_id, question in per_question.items():
            predictions.append(
                {
                    "dataset": "locomo",
                    "sample_id": "s1",
                    "question_id": question_id,
                    "prediction": question["prediction"],
                    "evidence": [],
                    "latency_ms": 1.0,
                    "input_tokens": question["input_tokens"],
                    "output_tokens": 5,
                    "metadata": {
                        "method": method,
                        "question_type": question["category"],
                        "category": question["category"],
                        "model_cache": {"chat_cache_key": "sha256-x"},
                    },
                }
            )
            samples.append(
                {
                    "schema_version": 1,
                    "dataset": "locomo",
                    "sample_id": "s1",
                    "question_id": question_id,
                    "exact_match": question["exact_match"],
                    "token_f1": question["token_f1"],
                    "evidence_precision": 0.0,
                    "evidence_recall": 0.0,
                    "evidence_f1": 0.0,
                    "latency_ms": 1.0,
                    "input_tokens": question["input_tokens"],
                    "output_tokens": 5,
                }
            )
            if method in MEMORY_METHODS:
                retrievals.append(
                    {
                        "dataset": "locomo",
                        "sample_id": "s1",
                        "question_id": question_id,
                        "intent": "semantic",
                        "strategy": "qemr",
                        "packed_items": [
                            {
                                "memory_id": f"m-{question_id}",
                                "content": question["context_text"],
                                "final_score": 0.9,
                                "component_scores": {"dense": 0.9},
                                "token_count": len(question["context_text"].split()),
                                "historical": False,
                                "reason": "packed under token budget",
                                "evidence_refs": [],
                            }
                        ],
                    }
                )
        method_dir = run_dir / method
        method_dir.mkdir(parents=True, exist_ok=True)
        _write_jsonl(method_dir / "predictions.jsonl", predictions)
        _write_jsonl(method_dir / "samples.jsonl", samples)
        if method in MEMORY_METHODS:
            _write_jsonl(method_dir / "retrieval.jsonl", retrievals)
    _write_jsonl(sample_path / "s1.json", [per_question])


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n",
        encoding="utf-8",
    )


@pytest.fixture
def synthetic_run(tmp_path: Path) -> Path:
    run_dir = tmp_path / "runs" / "synthetic"
    build_run(run_dir)
    build_question_artifacts(run_dir)
    return run_dir
