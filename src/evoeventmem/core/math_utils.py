from __future__ import annotations

import math
from collections.abc import Iterable, Sequence

from evoeventmem.domain.models import EvidenceRef


def evidence_key(evidence: EvidenceRef) -> tuple[str, str, str | None]:
    return (evidence.source_type, evidence.source_id, evidence.locator)


def jaccard(left: set[str], right: set[str]) -> float:
    if not left and not right:
        return 0.0
    union = left | right
    if not union:
        return 0.0
    return len(left & right) / len(union)


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    if len(a) != len(b):
        return 0.0
    a_norm = math.sqrt(sum(v * v for v in a))
    b_norm = math.sqrt(sum(v * v for v in b))
    if a_norm == 0.0 or b_norm == 0.0:
        return 0.0
    dot_product = sum(
        av * bv for av, bv in zip(a, b, strict=True)
    )
    return dot_product / (a_norm * b_norm)


def unique_evidence(refs: Iterable[EvidenceRef]) -> list[EvidenceRef]:
    seen: set[tuple[str, str, str | None]] = set()
    unique: list[EvidenceRef] = []
    for ref in refs:
        key = evidence_key(ref)
        if key in seen:
            continue
        seen.add(key)
        unique.append(ref)
    return unique
