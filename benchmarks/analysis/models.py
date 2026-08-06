"""Dataset-neutral analysis row contract (consumer of B-ARTIFACT).

``AnalysisRow`` normalizes LongMemEval and LoCoMo finalized runs into one row
schema for Workstream C. Producer fields are defined by Workstream B in
``benchmarks.common.artifacts`` and are imported, never redefined.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from benchmarks.common.artifacts import (
    ConsolidationAction,
    PolicyVersions,
    SourceFailure,
)


class AnalysisRow(BaseModel):
    """One dataset-neutral per-question analysis row.

    LongMemEval and LoCoMo loaders normalize into this schema without forcing
    dataset-specific methods (such as ``session_summary``) onto LongMemEval.
    Compatibility is enforced within each dataset/method comparison and within
    each paired ablation family, not across datasets.
    """

    # Identifiers.
    dataset: str = Field(min_length=1)
    sample_id: str = Field(min_length=1)
    question_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    method: str = Field(min_length=1)
    category: str = Field(min_length=1)

    # Predictions and gold answers.
    prediction: str
    gold_answer: str | None = None

    # QA / evidence metrics.
    exact_match: float = Field(ge=0, le=1)
    token_f1: float = Field(ge=0, le=1)
    evidence_precision: float = Field(ge=0, le=1)
    evidence_recall: float = Field(ge=0, le=1)
    evidence_f1: float = Field(ge=0, le=1)

    # Budget / binding fields.
    content_tokens: int = Field(ge=0)
    prompt_overhead_tokens: int = Field(ge=0)
    total_input_tokens: int = Field(ge=0)
    packing_bound: bool = False

    # Retrieval / fallback / exclusion data.
    source_failures: list[SourceFailure] = Field(default_factory=list)
    packed_item_count: int = Field(ge=0)

    # Normalized retrieval/extraction traces (filled by the loaders).
    context_text: str = ""
    intent: str | None = None
    candidate_count: int | None = None
    exclusion_reasons: list[str] = Field(default_factory=list)
    extraction_rejection_reasons: list[str] = Field(default_factory=list)

    # Consolidation actions (B-owned enum).
    consolidation_actions: list[ConsolidationAction] = Field(default_factory=list)

    # Model / policy hashes.
    reader_model: str = Field(min_length=1)
    extractor_model: str = Field(min_length=1)
    embedding_model: str = Field(min_length=1)
    tokenizer: str = Field(min_length=1)
    policy_versions: PolicyVersions

    # Config / run hashes.
    config_hash: str = Field(min_length=1)
    git_commit: str = Field(min_length=1)
    manifest_hash: str = Field(min_length=1)

    # Artifact locations.
    predictions_path: str = Field(min_length=1)
    samples_path: str = Field(min_length=1)