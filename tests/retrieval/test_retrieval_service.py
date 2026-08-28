from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from evoeventmem.retrieval import (
    QEMRRetrievalResult,
    RetrievalService,
    RetrievalStrategy,
)


def _make_result(query: str = "test", user_id: str = "u1") -> QEMRRetrievalResult:
    """Create a minimal QEMRRetrievalResult."""
    return QEMRRetrievalResult(
        query=query,
        user_id=user_id,
        intent="semantic",
        strategy=RetrievalStrategy.QEMR,
        budget_tokens=1024,
        total_tokens=0,
    )


def _make_mock_harness(results: list[QEMRRetrievalResult] | None = None) -> MagicMock:
    """Create a mock RetrievalHarness with configurable return values."""
    harness = MagicMock()
    if results is None:
        harness.retrieve.return_value = _make_result()
    else:
        harness.retrieve.side_effect = results
    return harness


def test_retrieve_returns_harness_result() -> None:
    """Happy path: retrieve() delegates to harness and returns its result."""
    expected = _make_result(query="hello")
    harness = MagicMock()
    harness.retrieve.return_value = expected
    service = RetrievalService(harness)

    result = service.retrieve("hello", user_id="u1")

    assert result is expected
    harness.retrieve.assert_called_once_with(
        "hello",
        user_id="u1",
        tenant_id=None,
        strategy=RetrievalStrategy.QEMR,
        budget_tokens=None,
        reference_time=None,
        controls=None,
    )


def test_retrieve_records_result() -> None:
    """retrieve() appends result to internal list."""
    r1 = _make_result(query="q1")
    r2 = _make_result(query="q2")
    harness = _make_mock_harness([r1, r2])
    service = RetrievalService(harness)

    service.retrieve("q1", user_id="u1")
    service.retrieve("q2", user_id="u2")

    results = service.list_results()
    assert len(results) == 2
    assert results[0].query == "q1"
    assert results[1].query == "q2"


def test_list_results_initially_empty() -> None:
    """list_results() returns empty list when no queries executed."""
    harness = _make_mock_harness()
    service = RetrievalService(harness)

    assert service.list_results() == []


def test_retrieve_propagates_harness_error() -> None:
    """Exceptions from harness propagate through retrieve()."""
    harness = _make_mock_harness()
    harness.retrieve.side_effect = RuntimeError("harness failure")
    service = RetrievalService(harness)

    with pytest.raises(RuntimeError, match="harness failure"):
        service.retrieve("q", user_id="u1")


def test_export_jsonl_empty() -> None:
    """export_jsonl() returns empty list when no results recorded."""
    harness = _make_mock_harness()
    service = RetrievalService(harness)

    assert service.export_jsonl() == []


def test_export_jsonl_contains_all_results() -> None:
    """export_jsonl() dumps all recorded results as JSON dicts."""
    r1 = _make_result(query="q1")
    r2 = _make_result(query="q2")
    harness = _make_mock_harness([r1, r2])
    service = RetrievalService(harness)

    service.retrieve("q1", user_id="u1")
    service.retrieve("q2", user_id="u2")

    jsonl = service.export_jsonl()
    assert len(jsonl) == 2
    assert jsonl[0]["query"] == "q1"
    assert jsonl[1]["query"] == "q2"


def test_retrieve_passes_all_parameters() -> None:
    """retrieve() forwards all optional parameters to harness."""
    from datetime import UTC, datetime

    harness = _make_mock_harness()
    service = RetrievalService(harness)
    ref_time = datetime(2025, 1, 1, tzinfo=UTC)

    service.retrieve(
        "q",
        user_id="u1",
        tenant_id="t1",
        strategy=RetrievalStrategy.FIXED_HYBRID,
        budget_tokens=512,
        reference_time=ref_time,
    )

    harness.retrieve.assert_called_once_with(
        "q",
        user_id="u1",
        tenant_id="t1",
        strategy=RetrievalStrategy.FIXED_HYBRID,
        budget_tokens=512,
        reference_time=ref_time,
        controls=None,
    )
