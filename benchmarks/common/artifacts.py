from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

ARTIFACT_SCHEMA_VERSION = 1


class EvidencePrediction(BaseModel):
    source_type: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    locator: str | None = None
    quote: str | None = None


class PredictionRecord(BaseModel):
    schema_version: int = ARTIFACT_SCHEMA_VERSION
    dataset: str = Field(min_length=1)
    sample_id: str = Field(min_length=1)
    question_id: str = Field(min_length=1)
    prediction: str
    evidence: list[EvidencePrediction] = Field(default_factory=list)
    latency_ms: float = Field(ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RunMetadata(BaseModel):
    schema_version: int = ARTIFACT_SCHEMA_VERSION
    run_id: str = Field(min_length=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    model_id: str = Field(min_length=1)
    config_hash: str = Field(min_length=1)
    git_commit: str = Field(min_length=1)
    dataset_fingerprint: str = Field(min_length=1)
    metrics_version: str = "deterministic-v1"
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("created_at")
    @classmethod
    def require_aware_created_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("created_at must be timezone-aware")
        return value


class SampleEvaluation(BaseModel):
    schema_version: int = ARTIFACT_SCHEMA_VERSION
    dataset: str = Field(min_length=1)
    sample_id: str = Field(min_length=1)
    question_id: str = Field(min_length=1)
    exact_match: float = Field(ge=0, le=1)
    token_f1: float = Field(ge=0, le=1)
    evidence_precision: float = Field(ge=0, le=1)
    evidence_recall: float = Field(ge=0, le=1)
    evidence_f1: float = Field(ge=0, le=1)
    latency_ms: float = Field(ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)


class RunSummary(BaseModel):
    schema_version: int = ARTIFACT_SCHEMA_VERSION
    metadata: RunMetadata
    sample_count: int = Field(ge=0)
    exact_match: float = Field(ge=0, le=1)
    token_f1: float = Field(ge=0, le=1)
    evidence_precision: float = Field(ge=0, le=1)
    evidence_recall: float = Field(ge=0, le=1)
    evidence_f1: float = Field(ge=0, le=1)
    total_latency_ms: float = Field(ge=0)
    total_input_tokens: int | None = Field(default=None, ge=0)
    total_output_tokens: int | None = Field(default=None, ge=0)
    predictions_path: str
    samples_path: str


def write_json_write_once(path: Path, payload: BaseModel | dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serializable = payload.model_dump(mode="json") if isinstance(payload, BaseModel) else payload
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            json.dump(serializable, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.link(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def write_jsonl_write_once(path: Path, records: Iterable[BaseModel | dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            for record in records:
                serializable = (
                    record.model_dump(mode="json") if isinstance(record, BaseModel) else record
                )
                handle.write(json.dumps(serializable, sort_keys=True))
                handle.write("\n")
        os.link(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def current_git_commit() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return "unknown"
    return result.stdout.strip() or "unknown"
