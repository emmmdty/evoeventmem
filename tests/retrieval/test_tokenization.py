from __future__ import annotations

from evoeventmem.core.ports import ChatMessage
from evoeventmem.tokenization import DeterministicTokenEstimator, TokenEstimate


def _messages() -> list[ChatMessage]:
    return [
        ChatMessage(role="system", content="Use cited evidence."),
        ChatMessage(role="user", content="Question: 你好？"),
    ]


def test_estimator_counts_complete_messages() -> None:
    estimator = DeterministicTokenEstimator(name="test", version="v1")
    estimate = estimator.count_messages(_messages())
    assert estimate.estimator_name == "test"
    assert estimate.estimator_version == "v1"
    assert estimate.message_overhead_tokens > 0
    assert estimate.total_tokens >= estimate.content_tokens
    assert estimate.total_tokens == estimate.content_tokens + estimate.message_overhead_tokens


def test_estimator_exposes_declared_identity() -> None:
    estimator = DeterministicTokenEstimator(name="t5-smoke", version="2026.08")
    assert estimator.name == "t5-smoke"
    assert estimator.version == "2026.08"


def test_estimator_handles_unicode_and_punctuation() -> None:
    estimator = DeterministicTokenEstimator(name="test", version="v1")
    estimate = estimator.count_messages(
        [ChatMessage(role="user", content="你好，世界！ Hello, world! 12345")]
    )
    assert estimate.content_tokens > 0
    assert estimator.count_messages(
        [ChatMessage(role="user", content="你好，世界！")]
    ).content_tokens > 0


def test_estimator_counts_empty_messages() -> None:
    estimator = DeterministicTokenEstimator(name="test", version="v1")
    estimate = estimator.count_messages([])
    assert estimate.content_tokens == 0
    assert estimate.message_overhead_tokens == 0
    assert estimate.total_tokens == 0


def test_estimator_is_deterministic_and_repeatable() -> None:
    estimator = DeterministicTokenEstimator(name="test", version="v1")
    first = estimator.count_messages(_messages())
    second = estimator.count_messages(_messages())
    assert first == second


def test_estimator_returns_typed_estimate() -> None:
    estimate = DeterministicTokenEstimator(name="test", version="v1").count_messages(
        _messages()
    )
    assert isinstance(estimate, TokenEstimate)