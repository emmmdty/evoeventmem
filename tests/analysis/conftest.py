"""Shared fixtures: a synthetic LoCoMo dataset and a synthetic valid run.

Two fixture families coexist:

- Legacy ``build_run`` / ``build_question_artifacts``: the old LoCoMo-only
  ``summary.json`` / ``config.json`` run tree used by the legacy validators.
- ``build_synthetic_*`` (schema ``analysis.synthetic.v1``): B-schema-valid
  finalized run trees (``manifest.json`` + ``finalized/FINALIZED.json`` +
  per-method derived artifacts) used by the dataset-neutral C2+ consumers.
  Every file and hash is deterministic (fixed timestamps, no randomness).
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from benchmarks.common.artifacts import (
    AblationRunManifest,
    ArtifactClass,
    BudgetSpec,
    ConsolidationAction,
    ConsolidationRecord,
    EvidencePrediction,
    EvidenceRecord,
    ExtractionSnapshot,
    FinalizationRecord,
    GitState,
    PolicyVersions,
    PredictionRecord,
    ProviderIdentity,
    RunManifest,
    SampleEvaluation,
    TokenizerIdentity,
    required_file_paths,
    required_hash,
    write_json_write_once,
    write_jsonl_write_once,
)

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

# C2 dataset-neutral fixture constants --------------------------------------- #

LME_METHODS = (
    "no_memory",
    "full_context",
    "vector_rag",
    "event_no_etec",
    "etec",
    "full",
)
LOCOMO_METHODS = (
    "no_memory",
    "full_context",
    "session_summary",
    "vector_rag",
    "event_no_etec",
    "etec",
    "full",
)
CONTEXT_METHODS = frozenset({"no_memory", "full_context", "session_summary"})

METHOD_BASE_RATES: dict[str, float] = {
    "no_memory": 0.0,
    "full_context": 0.30,
    "session_summary": 0.55,
    "vector_rag": 0.40,
    "event_no_etec": 0.50,
    "etec": 0.60,
    "full": 0.75,
}
LOCOMO_CATEGORY_NAMES = {
    "1": "single-hop",
    "2": "multi-hop-reasoning",
    "3": "temporal-reasoning",
    "4": "open-domain-knowledge",
    "5": "adversarial",
}
LME_ABILITY_NAMES = (
    "information-extraction",
    "multi-session-reasoning",
    "knowledge-update",
    "temporal-reasoning",
    "abstention",
)
FIXED_FINALIZED_AT = datetime(2026, 8, 5, 12, 0, 0, tzinfo=UTC)
FIXED_SNAPSHOT_AT = datetime(2026, 8, 5, 12, 0, 0, tzinfo=UTC)
FIXED_GIT_COMMIT = "0c20848"
FIXED_CONFIG_HASH = "sha256:config-a"
ABLATION_FACTORS = (
    "evidence_policy",
    "temporal_source",
    "graph_source",
    "routing",
    "weights",
    "budget",
)
# B-style arm names: the base arm is an AblationRunManifest with the
# changed_factor "base"; the budget factor has two settings (2+ budget arms).
ABLATION_ARM_NAMES = (
    "base",
    "evidence",
    "temporal",
    "graph",
    "router",
    "weights",
    "budget_384",
    "budget_512",
)
FACTOR_BY_ARM: dict[str, str] = {
    "base": "base",
    "evidence": "evidence_policy",
    "temporal": "temporal_source",
    "graph": "graph_source",
    "router": "routing",
    "weights": "weights",
    "budget_384": "budget",
    "budget_512": "budget",
}
FACTOR_ARMS = tuple(name for name in ABLATION_ARM_NAMES if name != "base")
BUDGET_SETTINGS = ("budget_384", "budget_512")


def _method_seed(method: str) -> int:
    return sum(ord(char) for char in method) % 5


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return f"sha256:{digest}"


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    write_jsonl_write_once(path, rows)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    write_json_write_once(path, payload)


def provider(model_id: str, *, version: str = "v1") -> ProviderIdentity:
    return ProviderIdentity(
        kind="http",
        provider="deterministic_fake",
        model_id=model_id,
        version=version,
        endpoint="http://fake",
    )


def policy_versions() -> PolicyVersions:
    return PolicyVersions(
        extraction="shared-snapshot.v1",
        router="query-router.rules.v1",
        retrieval="qemr-weight-profiles.v1",
        consolidation="etec.v1",
    )


def question_plan(
    dataset: str,
    sample_ids: list[str],
    question_count: int,
) -> list[dict]:
    """Deterministic per-question descriptors (question/sample IDs, category,
    gold answer, adversarial flag) for one dataset."""
    plan: list[dict] = []
    if dataset == "longmemeval":
        for index, sample_id in enumerate(sample_ids):
            ability = LME_ABILITY_NAMES[index % len(LME_ABILITY_NAMES)]
            adversarial = ability == "abstention"
            plan.append(
                {
                    "question_id": sample_id,
                    "sample_id": sample_id,
                    "raw_category": ability,
                    "category": ability,
                    "gold_answer": None
                    if adversarial
                    else ("blue house", "red car", "green truck")[index % 3],
                    "adversarial": adversarial,
                    "index": index,
                }
            )
    else:
        sample_id = sample_ids[0]
        for index in range(question_count):
            raw_category = str(index % 5 + 1)
            adversarial = raw_category == "5"
            plan.append(
                {
                    "question_id": f"{sample_id}:qa:{index}",
                    "sample_id": sample_id,
                    "raw_category": raw_category,
                    "category": LOCOMO_CATEGORY_NAMES[raw_category],
                    "gold_answer": None
                    if adversarial
                    else ("blue house", "red car", "green truck")[index % 3],
                    "adversarial": adversarial,
                    "index": index,
                }
            )
    return plan


def build_synthetic_dataset(
    path: Path,
    *,
    dataset: str,
    sample_ids: list[str],
    question_count: int = 24,
) -> dict:
    """Write a dataset file in the format B's normalizers consume."""
    plan = question_plan(dataset, sample_ids, question_count)
    if dataset == "longmemeval":
        samples = []
        for question in plan:
            index = question["index"]
            samples.append(
                {
                    "question_id": question["question_id"],
                    "question": f"What is the color of house {index}?",
                    "answer": question["gold_answer"],
                    "question_type": question["raw_category"],
                    "question_date": f"2023-01-{index % 28 + 1:02d}",
                    "haystack_session_ids": [f"lme_session_{s}" for s in range(3)],
                    "haystack_dates": ["2023-01-01", "2023-02-01", "2023-03-01"],
                    "haystack_sessions": [
                        [
                            {"role": "user", "content": f"turn text {s}:0"},
                            {"role": "assistant", "content": f"answer text {s}:1"},
                        ]
                        for s in range(3)
                    ],
                    "answer_session_ids": ["lme_session_1"],
                }
            )
    else:
        turns = [
            {
                "dia_id": f"D{i}:{j}",
                "speaker": "A" if j % 2 == 0 else "B",
                "text": f"turn text {i}:{j}",
            }
            for i in range(3)
            for j in range(4)
        ]
        conversation: dict = {"speaker_a": "A", "speaker_b": "B"}
        for i in range(3):
            conversation[f"session_{i}"] = [turns[i * 4 + j] for j in range(4)]
            conversation[f"session_{i}_date_time"] = f"12:00 AM on 0{i + 1} January, 2023"
        questions = []
        for question in plan:
            qa: dict = {
                "question": f"What is the color of house {question['index']}?",
                "evidence": ["D0:0", "D1:1"],
                "category": question["raw_category"],
            }
            if question["adversarial"]:
                qa["adversarial_answer"] = "no info"
            else:
                qa["answer"] = question["gold_answer"]
            questions.append(qa)
        samples = [
            {
                "sample_id": sample_ids[0],
                "conversation": conversation,
                "qa": questions,
                "session_summary": {
                    f"session_{i}_summary": f"summary of session {i} about blue house"
                    for i in range(3)
                },
                "event_summary": {
                    f"events_session_{i}": {
                        "date": f"0{i + 1} January, 2023",
                        "A": ["event summary text"],
                        "B": ["other summary text"],
                    }
                    for i in range(3)
                },
                "observation": {"summary": "observation text"},
            }
        ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(samples, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"dataset_path": path, "dataset_hash": _hash_file(path), "plan": plan}


def _correct(
    method: str,
    plan: list[dict],
    index: int,
    rates: dict[str, float] | None = None,
) -> bool:
    effective = {**METHOD_BASE_RATES, **(rates or {})}
    rate = effective[method]
    return (index + _method_seed(method)) % 10 < int(rate * 10)


def build_synthetic_run(
    run_dir: Path,
    *,
    dataset: str,
    methods: tuple[str, ...] | None = None,
    sample_ids: list[str] | None = None,
    question_count: int = 24,
    run_id: str | None = None,
    artifact_class: ArtifactClass = ArtifactClass.PUBLICATION,
    scope: str = "full",
    git_dirty: bool = False,
    reader_model_id: str = "reader-model-a",
    extractor_model_id: str = "extractor-model-a",
    embedding_model_id: str = "embedding-model-a",
    tokenizer_name: str = "estimator-a",
    tokenizer_version: str = "v1",
    max_input_tokens: int = 4096,
    max_items_per_source: int = 8,
    max_candidates_per_source: int = 128,
    config_hash: str = FIXED_CONFIG_HASH,
    policies: PolicyVersions | None = None,
    git_commit: str = FIXED_GIT_COMMIT,
    dataset_path_rel: str = "data/synthetic/dataset.json",
    metadata: dict | None = None,
    method_rates: dict[str, float] | None = None,
) -> dict:
    """Build a B-schema-valid finalized run tree (schema analysis.synthetic.v1)."""
    if dataset == "longmemeval":
        methods = tuple(methods or LME_METHODS)
        sample_ids = sample_ids or [f"lme_s{i}" for i in range(12)]
    else:
        methods = tuple(methods or LOCOMO_METHODS)
        sample_ids = sample_ids or ["locomo_s0"]

    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    dataset_payload = build_synthetic_dataset(
        run_dir / dataset_path_rel,
        dataset=dataset,
        sample_ids=sample_ids,
        question_count=question_count,
    )
    plan = dataset_payload["plan"]
    expected_sample_ids = list(dict.fromkeys(item["sample_id"] for item in plan))
    expected_question_ids = [item["question_id"] for item in plan]
    run_id = run_id or f"{dataset}-run"
    policies = policies or policy_versions()

    manifest = RunManifest(
        run_id=run_id,
        artifact_class=artifact_class,
        dataset=dataset,
        dataset_path=dataset_path_rel,
        dataset_hash=dataset_payload["dataset_hash"],
        scope=scope,
        methods=list(methods),
        reader=provider(reader_model_id),
        extractor=provider(extractor_model_id),
        embedding=provider(embedding_model_id),
        tokenizer=TokenizerIdentity(name=tokenizer_name, version=tokenizer_version),
        policies=policies,
        budget=BudgetSpec(
            input_tokens=max_input_tokens,
            max_items_per_source=max_items_per_source,
            max_candidates_per_source=max_candidates_per_source,
        ),
        git=GitState(commit=git_commit, dirty=git_dirty),
        config_hash=config_hash,
        expected_sample_ids=expected_sample_ids,
        expected_question_ids=expected_question_ids,
        metadata=metadata or {},
    )
    _write_json(run_dir / "manifest.json", manifest.model_dump(mode="json"))

    snapshots = []
    for sample_id in expected_sample_ids:
        snapshots.append(
            ExtractionSnapshot(
                snapshot_id=f"{dataset}:{sample_id}:snapshot",
                conversation_id=sample_id,
                extractor=provider(extractor_model_id),
                raw_turn_count=12,
                event_count=8,
                rejections=[],
                created_at=FIXED_SNAPSHOT_AT,
            )
        )
    _write_json(
        run_dir / "extraction_snapshot.json", [s.model_dump(mode="json") for s in snapshots]
    )

    (run_dir / "model_cache").mkdir(parents=True, exist_ok=True)
    (run_dir / "model_cache" / "embeddings.json").write_text(
        json.dumps({"model_id": embedding_model_id, "vectors": []}, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    root_retrieval: list[dict] = []
    evidence_rows: list[dict] = []
    for method in methods:
        predictions: list[dict] = []
        samples: list[dict] = []
        retrievals: list[dict] = []
        for question in plan:
            index = question["index"]
            adversarial = question["adversarial"]
            correct = not adversarial and _correct(method, plan, index, method_rates)
            prediction = (
                question["gold_answer"]
                if correct
                else ("I do not know" if adversarial else f"wrong answer {index}")
            )
            exact_match = 1.0 if correct else 0.0
            predictions.append(
                PredictionRecord(
                    dataset=dataset,
                    sample_id=question["sample_id"],
                    question_id=question["question_id"],
                    prediction=prediction,
                    evidence=[]
                    if adversarial
                    else [
                        EvidencePrediction(
                            source_type="raw_turn",
                            source_id="D0:0",
                            locator="D0:0",
                            quote="turn text",
                        )
                    ],
                    latency_ms=1.0,
                    input_tokens=100 + index,
                    output_tokens=5,
                    metadata={
                        "method": method,
                        "question_type": question["raw_category"],
                        "category": question["category"],
                        "model_cache": {"chat_cache_key": "sha256-x"},
                    },
                ).model_dump(mode="json")
            )
            samples.append(
                SampleEvaluation(
                    dataset=dataset,
                    sample_id=question["sample_id"],
                    question_id=question["question_id"],
                    exact_match=exact_match,
                    token_f1=exact_match,
                    evidence_precision=1.0 if correct else 0.0,
                    evidence_recall=1.0 if correct else 0.0,
                    evidence_f1=1.0 if correct else 0.0,
                    latency_ms=1.0,
                    input_tokens=100 + index,
                    output_tokens=5,
                ).model_dump(mode="json")
            )
            if method in MEMORY_METHODS:
                payload = _retrieval_payload(
                    dataset=dataset,
                    sample_id=question["sample_id"],
                    question_id=question["question_id"],
                    method=method,
                    index=index,
                    max_input_tokens=max_input_tokens,
                )
                retrievals.append(payload)
                root_retrieval.append(
                    {
                        "dataset": dataset,
                        "sample_id": question["sample_id"],
                        "question_id": question["question_id"],
                        "method": method,
                        **payload,
                    }
                )
                for item in payload["packed_items"]:
                    for ref in item["evidence_refs"]:
                        evidence_rows.append(
                            EvidenceRecord(
                                question_id=question["question_id"],
                                raw_turn_id=str(ref["raw_turn_id"]),
                                span=str(ref.get("locator") or ""),
                                exact=True,
                            ).model_dump(mode="json")
                        )
        method_dir = run_dir / method
        _write_jsonl(method_dir / "predictions.jsonl", predictions)
        _write_jsonl(method_dir / "samples.jsonl", samples)
        if method in MEMORY_METHODS:
            _write_jsonl(method_dir / "retrieval.jsonl", retrievals)

    _write_jsonl(run_dir / "retrieval.jsonl", root_retrieval)
    _write_jsonl(run_dir / "evidence.jsonl", evidence_rows)
    consolidation_rows = [
        ConsolidationRecord(
            sample_id=sample_id,
            evidence=[],
            action=[
                ConsolidationAction.MERGE,
                ConsolidationAction.SUPERSEDE,
                ConsolidationAction.KEEP,
            ][index % 3],
            resolved_at=FIXED_SNAPSHOT_AT,
        ).model_dump(mode="json")
        for index, sample_id in enumerate(expected_sample_ids)
    ]
    _write_jsonl(run_dir / "consolidation.jsonl", consolidation_rows)

    record = _finalize_synthetic(run_dir, manifest)
    return {
        "run_dir": run_dir,
        "manifest": manifest,
        "manifest_hash": manifest.manifest_hash(),
        "finalization": record,
        "finalization_hash": record.finalization_hash(),
        "dataset": dataset,
        "methods": methods,
        "plan": plan,
        "gold": {
            item["question_id"]: {"answer": item["gold_answer"], "category": item["category"]}
            for item in plan
        },
    }


def _retrieval_payload(
    *,
    dataset: str,
    sample_id: str,
    question_id: str,
    method: str,
    index: int,
    max_input_tokens: int,
    evidence_policy: str = "constrained",
    budget_tokens: int | None = None,
    intent_override: str | None = None,
    extra_exclusions: list[dict] | None = None,
    score_shift: float = 0.0,
    packing_bound: bool | None = None,
) -> dict:
    strategies = {"vector_rag": "fixed_vector", "etec": "fixed_vector"}
    intents = ("semantic", "temporal", "graph", "hybrid")
    budget = budget_tokens or max_input_tokens
    payload: dict = {
        "question_id": question_id,
        "dataset": dataset,
        "sample_id": sample_id,
        "intent": intent_override or intents[index % 4],
        "strategy": strategies.get(method, "qemr"),
        "evidence_policy": evidence_policy,
        "budget_tokens": budget,
        "total_tokens": 100 + index,
        "content_tokens": 60 + index,
        "prompt_overhead_tokens": 40,
        "total_input_tokens_estimate": 100 + index,
        "packing_bound": (packing_bound if packing_bound is not None else index % 13 == 5),
        "candidate_count": 20,
        "exclusion_count": 1 if index % 7 == 3 else 0,
        "exclusions": extra_exclusions
        or ([{"memory_id": "m-temporal", "reason": "temporal_filtered"}] if index % 7 == 3 else []),
        "source_failures": [
            {
                "source": "graph",
                "reason_code": "graph_unavailable",
                "degraded_policy": False,
                "duration_ms": 0.0,
            }
        ]
        if index % 7 == 3
        else [],
        "packed_items": [
            {
                "memory_id": f"m-{method}-{index}-{k}",
                "content": (
                    f"blue house words for question {index}"
                    if index % 3 == 0
                    else f"memory content {k} for question {index}"
                ),
                "final_score": round(0.9 - 0.1 * k + score_shift, 4),
                "component_scores": {"dense": 0.8, "temporal": 0.6, "graph": 0.4},
                "token_count": 20 + k,
                "historical": False,
                "reason": "packed under token budget",
                "evidence_refs": [
                    {
                        "source_type": "raw_turn",
                        "source_id": f"D{index % 3}:{k}",
                        "locator": f"D{index % 3}:{k}",
                        "quote": "turn text",
                        "raw_turn_id": f"D{index % 3}:{k}",
                    }
                ],
            }
            for k in range(3)
        ],
    }
    return payload


def _finalize_synthetic(run_dir: Path, manifest: RunManifest) -> FinalizationRecord:
    required = required_file_paths(run_dir, manifest.artifact_class)
    record = FinalizationRecord(
        artifact_class=manifest.artifact_class,
        manifest_hash=manifest.manifest_hash(),
        required_hashes={path.name: required_hash(path) for path in required},
        completion_counts={"questions": len(manifest.expected_question_ids)},
        finalized_at=FIXED_FINALIZED_AT,
    )
    _write_json(run_dir / "finalized" / "FINALIZED.json", record.model_dump(mode="json"))
    return record


def build_controlled_run(
    run_dir: Path,
    *,
    family_name: str = "controlled-ablations",
    dataset_path_rel: str = "data/synthetic/controlled.json",
    zero_delta: bool = False,
) -> dict:
    """Build a smoke-class controlled ablation family (B-schema-valid)."""
    run_dir = Path(run_dir)
    dataset_payload = build_synthetic_dataset(
        run_dir / dataset_path_rel,
        dataset="locomo",
        sample_ids=["controlled_s0"],
        question_count=12,
    )
    plan = dataset_payload["plan"]
    arm_names = ABLATION_ARM_NAMES
    expected_sample_ids = ["controlled_s0"]
    expected_question_ids = [item["question_id"] for item in plan]

    family_manifest = RunManifest(
        run_id=family_name,
        artifact_class=ArtifactClass.SMOKE,
        dataset="controlled",
        dataset_path=dataset_path_rel,
        dataset_hash=dataset_payload["dataset_hash"],
        scope="full",
        methods=arm_names,
        reader=provider("reader-model-a"),
        extractor=provider("extractor-model-a"),
        embedding=provider("embedding-model-a"),
        tokenizer=TokenizerIdentity(name="estimator-a", version="v1"),
        policies=policy_versions(),
        budget=BudgetSpec(input_tokens=4096, max_items_per_source=8, max_candidates_per_source=128),
        git=GitState(commit=FIXED_GIT_COMMIT, dirty=False),
        config_hash=FIXED_CONFIG_HASH,
        expected_sample_ids=expected_sample_ids,
        expected_question_ids=expected_question_ids,
        metadata={"ablation_id": family_name, "store": "raw"},
    )
    _write_json(run_dir / "manifest.json", family_manifest.model_dump(mode="json"))
    _write_json(run_dir / "extraction_snapshot.json", [])
    _write_jsonl(run_dir / "retrieval.jsonl", [])
    _write_jsonl(run_dir / "evidence.jsonl", [])
    _write_jsonl(run_dir / "consolidation.jsonl", [])
    record = _finalize_synthetic(run_dir, family_manifest)

    base_rows = {
        question["question_id"]: _retrieval_payload(
            dataset="controlled",
            sample_id="controlled_s0",
            question_id=question["question_id"],
            method="full",
            index=question["index"],
            max_input_tokens=4096,
        )
        for question in plan
    }
    arm_infos: dict[str, dict] = {}
    for arm_name in arm_names:
        arm_dir = run_dir / arm_name
        factor = FACTOR_BY_ARM[arm_name]
        if arm_name == "base" or zero_delta:
            arm_rows = dict(base_rows)
        else:
            arm_rows = _factor_rows(plan, base_rows, factor, arm_name)
        arm_infos[arm_name] = _write_ablation_arm(
            arm_dir,
            dataset="controlled",
            family_manifest=family_manifest,
            controlled_run_hash=record.finalization_hash(),
            base_run_hash=record.finalization_hash(),
            arm_name=arm_name,
            factor=factor,
            rows=arm_rows,
            artifact_class=ArtifactClass.SMOKE,
            dataset_path_rel=dataset_path_rel,
            dataset_hash=dataset_payload["dataset_hash"],
        )
    deltas = _compute_deltas(plan, arm_infos, base_rows)
    _write_json(run_dir / "deltas.json", deltas)
    _write_json(
        run_dir / "summary.json",
        {
            "run_id": family_name,
            "dataset": "controlled",
            "arms": {name: info["summary"] for name, info in arm_infos.items()},
        },
    )
    _write_json(run_dir / "arms.json", {name: info["summary"] for name, info in arm_infos.items()})
    return {
        "run_dir": run_dir,
        "manifest": family_manifest,
        "finalization_hash": record.finalization_hash(),
        "plan": plan,
        "arms": {name: info["arm_dir"] for name, info in arm_infos.items()},
        "deltas": deltas,
    }


def _factor_rows(
    plan: list[dict],
    base_rows: dict[str, dict],
    factor: str,
    arm_name: str,
) -> dict[str, dict]:
    """Deterministically alter base arm payloads for one ablation factor."""
    rows: dict[str, dict] = {}
    for question in plan:
        index = question["index"]
        payload = dict(base_rows[question["question_id"]])
        if factor == "evidence_policy":
            payload["evidence_policy"] = "provenance_only"
        elif factor == "temporal_source":
            if index % 2 == 0:
                payload["exclusions"] = [
                    *payload["exclusions"],
                    {"memory_id": "m-temporal", "reason": "temporal_source_removed"},
                ]
                payload["exclusion_count"] += 1
        elif factor == "graph_source":
            if index % 2 == 0:
                payload["exclusions"] = [
                    *payload["exclusions"],
                    {"memory_id": "m-graph", "reason": "graph_source_removed"},
                ]
                payload["exclusion_count"] += 1
        elif factor == "routing":
            payload["intent"] = "temporal" if index % 2 == 0 else payload["intent"]
        elif factor == "weights":
            payload["packed_items"] = [
                {
                    **item,
                    "final_score": round(float(item["final_score"]) - 0.15, 4),
                }
                for item in payload["packed_items"]
            ]
        elif factor == "budget":
            payload["budget_tokens"] = 384 if arm_name == "budget_384" else 512
            payload["packing_bound"] = index % 3 == 0
            payload["exclusions"] = (
                [*payload["exclusions"], {"memory_id": "m-budget", "reason": "budget_exceeded"}]
                if index % 3 == 0
                else payload["exclusions"]
            )
        rows[question["question_id"]] = payload
    return rows


def _write_ablation_arm(
    arm_dir: Path,
    *,
    dataset: str,
    family_manifest: RunManifest,
    controlled_run_hash: str,
    base_run_hash: str,
    arm_name: str,
    factor: str,
    rows: dict[str, dict],
    artifact_class: ArtifactClass,
    dataset_path_rel: str,
    dataset_hash: str,
) -> dict:
    arm_dir = Path(arm_dir)
    arm_dir.mkdir(parents=True, exist_ok=True)
    manifest = AblationRunManifest(
        run_id=arm_name,
        artifact_class=artifact_class,
        dataset=dataset,
        dataset_path=dataset_path_rel,
        dataset_hash=dataset_hash,
        scope="full",
        methods=[arm_name],
        reader=family_manifest.reader,
        extractor=family_manifest.extractor,
        embedding=family_manifest.embedding,
        tokenizer=family_manifest.tokenizer,
        policies=family_manifest.policies,
        budget=family_manifest.budget,
        git=family_manifest.git,
        config_hash=family_manifest.config_hash,
        expected_sample_ids=family_manifest.expected_sample_ids,
        expected_question_ids=family_manifest.expected_question_ids,
        ablation=arm_name,
        controlled_run_hash=controlled_run_hash,
        base_run_hash=base_run_hash,
        changed_factors=[factor],
        metadata={"ablation_id": family_manifest.run_id, "factor": factor, "arm": arm_name},
    )
    _write_json(arm_dir / "manifest.json", manifest.model_dump(mode="json"))
    _write_jsonl(
        arm_dir / "retrieval.jsonl",
        [{**payload, "arm": arm_name} for payload in rows.values()],
    )
    evidence_rows = [
        EvidenceRecord(
            question_id=question_id,
            raw_turn_id=str(item["evidence_refs"][0]["raw_turn_id"]),
            span="D0:0",
            exact=True,
        ).model_dump(mode="json")
        for question_id, payload in rows.items()
        for item in payload["packed_items"]
    ]
    _write_jsonl(arm_dir / "evidence.jsonl", evidence_rows)
    _write_jsonl(arm_dir / "consolidation.jsonl", [])
    _write_json(arm_dir / "extraction_snapshot.json", [])
    summary = {
        "run_id": arm_name,
        "arm": arm_name,
        "factor": factor or "base",
        "artifact_class": artifact_class.value,
        "question_count": len(rows),
        "packing_bound_questions": sum(1 for payload in rows.values() if payload["packing_bound"]),
        "manifest_hash": manifest.manifest_hash(),
    }
    _write_json(arm_dir / "summary.json", summary)
    _finalize_synthetic(arm_dir, manifest)
    return {
        "arm_dir": arm_dir,
        "manifest": manifest,
        "manifest_hash": manifest.manifest_hash(),
        "finalization_hash": _finalization_hash(arm_dir),
        "summary": summary,
    }


def _finalization_hash(run_dir: Path) -> str:
    record = FinalizationRecord.model_validate_json(
        (run_dir / "finalized" / "FINALIZED.json").read_text(encoding="utf-8")
    )
    return record.finalization_hash()


def _compute_deltas(
    plan: list[dict], arm_infos: dict[str, dict], base_rows: dict[str, dict]
) -> dict:
    def fingerprint(payload: dict) -> tuple:
        return (
            payload.get("intent"),
            payload.get("strategy"),
            payload.get("evidence_policy"),
            payload.get("budget_tokens"),
            payload.get("packing_bound"),
            payload.get("exclusion_count"),
            tuple(
                (item["memory_id"], item["final_score"], item["evidence_refs"][0]["raw_turn_id"])
                for item in payload.get("packed_items", [])
            ),
        )

    deltas: dict = {
        "ablation_id": "synthetic",
        "dataset": "synthetic",
        "required_factors": list(ABLATION_FACTORS),
        "arms": {},
    }
    for arm_name, info in arm_infos.items():
        factor = info["summary"]["factor"]
        if factor == "base":
            continue
        arm_rows = _rows_by_question(info["arm_dir"] / "retrieval.jsonl")
        delta_questions = [
            {
                "question_id": question_id,
                "delta": fingerprint(arm_rows[question_id]) != fingerprint(base_rows[question_id]),
                "fields_changed": [],
                "packing_bound": bool(arm_rows[question_id]["packing_bound"]),
            }
            for question_id in sorted(base_rows)
        ]
        deltas["arms"][arm_name] = {
            "factor": factor,
            "delta_question_count": sum(1 for item in delta_questions if item["delta"]),
            "questions": delta_questions,
        }
    return deltas


def _rows_by_question(path: Path) -> dict[str, dict]:
    return {
        row["question_id"]: row
        for row in (json.loads(line) for line in path.read_text(encoding="utf-8").splitlines())
    }


def build_ablation_run(
    run_dir: Path,
    *,
    dataset: str,
    base_run_dir: Path,
    controlled_run_dir: Path,
    family_name: str | None = None,
    zero_delta: bool = False,
) -> dict:
    """Build a publication-class dataset ablation family (B-schema-valid)."""
    base_info = _synthetic_run_info(base_run_dir)
    controlled_info = _synthetic_run_info(controlled_run_dir)
    family_name = family_name or f"{dataset}-ablations"
    run_dir = Path(run_dir)
    dataset_file = base_info["manifest"].dataset_path
    plan = base_info["plan"]
    arm_names = ABLATION_ARM_NAMES

    family_manifest = RunManifest(
        run_id=family_name,
        artifact_class=ArtifactClass.PUBLICATION,
        dataset=dataset,
        dataset_path=dataset_file,
        dataset_hash=base_info["manifest"].dataset_hash,
        scope="full",
        methods=arm_names,
        reader=base_info["manifest"].reader,
        extractor=base_info["manifest"].extractor,
        embedding=base_info["manifest"].embedding,
        tokenizer=base_info["manifest"].tokenizer,
        policies=base_info["manifest"].policies,
        budget=base_info["manifest"].budget,
        git=base_info["manifest"].git,
        config_hash=base_info["manifest"].config_hash,
        expected_sample_ids=base_info["manifest"].expected_sample_ids,
        expected_question_ids=base_info["manifest"].expected_question_ids,
        metadata={
            "ablation_id": family_name,
            "store": "etec",
            "factor_map": {name: FACTOR_BY_ARM[name] for name in arm_names},
        },
    )
    _write_json(run_dir / "manifest.json", family_manifest.model_dump(mode="json"))
    _write_json(run_dir / "extraction_snapshot.json", [])
    _write_jsonl(run_dir / "retrieval.jsonl", [])
    _write_jsonl(run_dir / "evidence.jsonl", [])
    _write_jsonl(run_dir / "consolidation.jsonl", [])
    record = _finalize_synthetic(run_dir, family_manifest)

    base_rows = {
        question["question_id"]: _retrieval_payload(
            dataset=dataset,
            sample_id=question["sample_id"],
            question_id=question["question_id"],
            method="full",
            index=question["index"],
            max_input_tokens=base_info["manifest"].budget.input_tokens,
        )
        for question in plan
    }
    arm_infos: dict[str, dict] = {}
    for arm_name in arm_names:
        factor = FACTOR_BY_ARM[arm_name]
        if arm_name == "base" or zero_delta:
            arm_rows = dict(base_rows)
        else:
            arm_rows = _factor_rows(plan, base_rows, factor, arm_name)
        arm_infos[arm_name] = _write_ablation_arm(
            run_dir / arm_name,
            dataset=dataset,
            family_manifest=family_manifest,
            controlled_run_hash=controlled_info["finalization_hash"],
            base_run_hash=base_info["finalization_hash"],
            arm_name=arm_name,
            factor=factor,
            rows=arm_rows,
            artifact_class=ArtifactClass.PUBLICATION,
            dataset_path_rel=dataset_file,
            dataset_hash=base_info["manifest"].dataset_hash,
        )
    deltas = _compute_deltas(plan, arm_infos, base_rows)
    _write_json(run_dir / "deltas.json", deltas)
    _write_json(
        run_dir / "summary.json",
        {
            "run_id": family_name,
            "dataset": dataset,
            "arms": {name: info["summary"] for name, info in arm_infos.items()},
        },
    )
    _write_json(run_dir / "arms.json", {name: info["summary"] for name, info in arm_infos.items()})
    return {
        "run_dir": run_dir,
        "manifest": family_manifest,
        "finalization_hash": record.finalization_hash(),
        "plan": plan,
        "arms": {name: info["arm_dir"] for name, info in arm_infos.items()},
        "deltas": deltas,
    }


def _synthetic_run_info(run_dir: Path) -> dict:
    manifest = RunManifest.model_validate_json(
        (run_dir / "manifest.json").read_text(encoding="utf-8")
    )
    plan = question_plan(
        manifest.dataset,
        manifest.expected_sample_ids,
        max(len(manifest.expected_question_ids), 1),
    )
    return {"manifest": manifest, "plan": plan, "finalization_hash": _finalization_hash(run_dir)}


def retamper_arm(arm_dir: Path, updates: dict) -> None:
    """Edit an arm manifest and re-seal it so only the cross-reference breaks."""
    from benchmarks.common.artifacts import AblationRunManifest

    manifest_path = Path(arm_dir) / "manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload.update(updates)
    manifest_path.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    _finalize_synthetic(Path(arm_dir), AblationRunManifest.model_validate(payload))


def retamper_arm_rows(arm_dir: Path, mutate) -> None:
    """Rewrite an arm's retrieval.jsonl and re-seal the arm."""
    from benchmarks.common.artifacts import AblationRunManifest

    arm_dir = Path(arm_dir)
    path = arm_dir / "retrieval.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    mutate(rows)
    path.unlink(missing_ok=True)
    write_jsonl_write_once(path, rows)
    payload = json.loads((arm_dir / "manifest.json").read_text(encoding="utf-8"))
    _finalize_synthetic(arm_dir, AblationRunManifest.model_validate(payload))


def retamper_family_manifest(family_dir: Path, updates: dict) -> None:
    """Edit a family manifest and re-seal the family."""
    manifest_path = Path(family_dir) / "manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload.update(updates)
    manifest_path.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    _finalize_synthetic(Path(family_dir), RunManifest.model_validate(payload))


@pytest.fixture
def analysis_fixture(tmp_path: Path) -> dict:
    """Full synthetic finalized input set: two base runs, controlled run, two ablation families."""
    runs_root = tmp_path / "runs"
    lme = build_synthetic_run(
        runs_root / "publication" / "longmemeval",
        dataset="longmemeval",
    )
    locomo = build_synthetic_run(
        runs_root / "publication" / "locomo",
        dataset="locomo",
        reader_model_id="reader-model-x",
        extractor_model_id="extractor-model-x",
        embedding_model_id="embedding-model-x",
        tokenizer_name="estimator-x",
    )
    controlled = build_controlled_run(runs_root / "validation" / "controlled-ablations")
    ablation_lme = build_ablation_run(
        runs_root / "publication" / "ablations" / "longmemeval",
        dataset="longmemeval",
        base_run_dir=lme["run_dir"],
        controlled_run_dir=controlled["run_dir"],
    )
    ablation_locomo = build_ablation_run(
        runs_root / "publication" / "ablations" / "locomo",
        dataset="locomo",
        base_run_dir=locomo["run_dir"],
        controlled_run_dir=controlled["run_dir"],
    )
    return {
        "runs_root": runs_root,
        "longmemeval": lme,
        "locomo": locomo,
        "controlled": controlled,
        "ablations": {"longmemeval": ablation_lme, "locomo": ablation_locomo},
        "source_runs": [lme["run_dir"], locomo["run_dir"]],
        "controlled_run": controlled["run_dir"],
        "ablation_runs": [ablation_lme["run_dir"], ablation_locomo["run_dir"]],
    }


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


@pytest.fixture
def synthetic_run(tmp_path: Path) -> Path:
    run_dir = tmp_path / "runs" / "synthetic"
    build_run(run_dir)
    build_question_artifacts(run_dir)
    return run_dir
