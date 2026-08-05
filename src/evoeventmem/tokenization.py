"""Deterministic reader-message token estimation (contract A-TOKEN).

This module owns the public ``TokenEstimator`` surface consumed by retrieval
budget packing. It is intentionally deterministic and free of any model vendor
dependency so that budget accounting is reproducible across runs.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from evoeventmem.core.ports import ChatMessage

# Tokens reserved for a single chat message's structural overhead (role marker,
# surrounding delimiters). Always positive so a message contributes more than
# its bare content.
_MESSAGE_OVERHEAD = 4
# CJK characters are treated as single tokens; latin/digit runs are split on
# word boundaries and punctuation so estimation is Unicode-aware.
_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]")
_WORD_RE = re.compile(r"[A-Za-z0-9]+")


@dataclass(frozen=True)
class TokenEstimate:
    """Token accounting for a complete sequence of reader messages."""

    content_tokens: int
    message_overhead_tokens: int
    total_tokens: int
    estimator_name: str
    estimator_version: str


def _count_content_tokens(text: str) -> int:
    """Deterministic Unicode/punctuation-aware content token count."""
    if not text:
        return 0
    cjk = len(_CJK_RE.findall(text))
    non_cjk = _CJK_RE.sub("", text)
    words = len(_WORD_RE.findall(non_cjk))
    punctuation = sum(1 for char in non_cjk if not char.isspace() and not char.isalnum())
    return cjk + words + punctuation


class DeterministicTokenEstimator:
    """Frozen, deterministic estimator usable as the shared budget tokenizer."""

    def __init__(self, name: str, version: str) -> None:
        if not name:
            raise ValueError("estimator name must be non-empty")
        if not version:
            raise ValueError("estimator version must be non-empty")
        self.name = name
        self.version = version

    def count_messages(self, messages: Sequence[ChatMessage]) -> TokenEstimate:
        """Count complete messages, including per-message overhead.

        Every message is counted as a unit: ``content_tokens`` aggregates bare
        content tokens, ``message_overhead_tokens`` is positive whenever at
        least one message is present, and ``total_tokens`` is their sum.
        """
        content_tokens = sum(_count_content_tokens(message.content) for message in messages)
        message_overhead_tokens = _MESSAGE_OVERHEAD * len(messages)
        return TokenEstimate(
            content_tokens=content_tokens,
            message_overhead_tokens=message_overhead_tokens,
            total_tokens=content_tokens + message_overhead_tokens,
            estimator_name=self.name,
            estimator_version=self.version,
        )


__all__ = [
    "DeterministicTokenEstimator",
    "TokenEstimate",
]