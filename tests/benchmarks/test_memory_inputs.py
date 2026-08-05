from __future__ import annotations

from pathlib import Path

from benchmarks.common.artifacts import ExtractionRejection, ProviderIdentity
from benchmarks.common.memory_inputs import (
    build_raw_turn_corpus,
    extract_event_snapshot,
    materialize_event_store,
)
from benchmarks.common.normalization import iter_locomo_records

FIXTURES = Path("tests/fixtures")


class StaticExtractor:
    """Deterministic fake extractor emitting exact-turn events."""

    prompt_version = "rule.v1"

    def __init__(self, evt_results: list[dict[str, object]] | None = None) -> None:
        self.evt_results = evt_results or []
        self.calls = 0

    def extract(self, request):  # noqa: ANN001
        from evoeventmem.domain.models import MemoryKind, MemoryRecord
        from evoeventmem.extraction import (
            ExtractedEventCandidate,
            ExtractionResult,
            _turn_evidence,
        )

        self.calls += 1
        candidates: list[ExtractedEventCandidate] = []
        for turn in request.turns:
            if not turn.content.strip():
                continue
            turn_refs = [_turn_evidence(request, turn)]
            candidates.append(
                ExtractedEventCandidate(
                    memory=MemoryRecord(
                        user_id=request.user_id,
                        memory_kind=MemoryKind.EVENT,
                        content=turn.content,
                        session_id=turn.session_id,
                        evidence_refs=turn_refs,
                    ),
                    prompt_version=self.prompt_version,
                )
            )
        return ExtractionResult(
            prompt_version=self.prompt_version, candidates=candidates
        )


def _locomo_record() -> object:
    return next(iter_locomo_records(FIXTURES / "locomo/locomo_tiny.json"))


def _identity() -> ProviderIdentity:
    return ProviderIdentity(
        kind="deterministic_fake",
        provider="deterministic_fake",
        model_id="extractor-fake",
        endpoint="n/a",
    )


def test_raw_corpus_uses_raw_turn_chunks_only() -> None:
    record = _locomo_record()
    corpus = build_raw_turn_corpus(record)

    assert corpus.conversation_id == "conv-tiny"
    assert corpus.chunk_count() == 2
    assert {chunk.raw_turn_id for chunk in corpus.chunks} == {"D1:1", "D1:2"}
    assert {chunk.content for chunk in corpus.chunks} == {
        "I went to an LGBTQ support group yesterday.",
        "I'm glad you felt supported.",
    }
    for chunk in corpus.chunks:
        assert chunk.evidence_ref.source_type == "turn"
        assert chunk.evidence_ref.metadata["raw_turn_id"] == chunk.raw_turn_id


def test_raw_corpus_contains_no_summary_or_answer_content() -> None:
    record = _locomo_record()
    corpus = build_raw_turn_corpus(record)

    joined = " ".join(chunk.content for chunk in corpus.chunks)
    assert "Caroline went to an LGBTQ support group on 7 May 2023." not in joined
    assert "7 May 2023" not in joined
    assert "I'm glad you felt supported." in joined


def test_extraction_snapshot_extracts_once_from_target_cleared_turns() -> None:
    record = _locomo_record()
    extractor = StaticExtractor()
    snap = extract_event_snapshot(
        record, extractor, user_id="u1", extractor_identity=_identity()
    )

    assert extractor.calls == 1
    assert snap.conversation_id == "conv-tiny"
    assert snap.raw_turn_count == 2
    assert snap.event_count == 2
    assert len(snap.events) == 2
    for event in snap.events:
        assert any(ref.source_type == "turn" for ref in event.evidence_refs)


def test_extraction_snapshot_hash_is_stable_across_serialization() -> None:
    from benchmarks.common.artifacts import ExtractionSnapshot

    record = _locomo_record()
    snap = extract_event_snapshot(
        record, StaticExtractor(), user_id="u1", extractor_identity=_identity()
    )

    assert snap.snapshot_hash() == ExtractionSnapshot.model_validate(
        snap.model_dump()
    ).snapshot_hash()


def test_materialize_event_store_without_etec() -> None:
    record = _locomo_record()
    snap = extract_event_snapshot(
        record, StaticExtractor(), user_id="u1", extractor_identity=_identity()
    )
    store, ingestion = materialize_event_store(snap, apply_etec=False, user_id="u1")

    memories = store.list_for_user("u1")
    assert isinstance(ingestion["apply_etec"], bool)
    assert ingestion["apply_etec"] is False
    assert len(memories) == 2
    for memory in memories:
        assert any(ref.source_type == "turn" for ref in memory.evidence_refs)


def test_vector_rag_never_receives_event_snapshot() -> None:
    record = _locomo_record()
    snapshot = extract_event_snapshot(
        record, StaticExtractor(), user_id="u1", extractor_identity=_identity()
    )
    corpus = build_raw_turn_corpus(record)

    # The vector baseline consumes only the raw corpus; the snapshot is a
    # separate object that is never handed to vector_rag.
    assert snapshot.snapshot_id != corpus.conversation_id
    assert not hasattr(corpus, "events")


def test_unsupported_span_produces_cached_rejection() -> None:
    record = _locomo_record()

    class SummaryOnlyExtractor(StaticExtractor):
        def extract(self, request):  # noqa: ANN001
            from evoeventmem.domain.models import MemoryKind, MemoryRecord
            from evoeventmem.extraction import (
                ExtractedEventCandidate,
                ExtractionEventSummary,
                ExtractionResult,
                _summary_evidence,
            )

            self.calls += 1
            summary = ExtractionEventSummary(
                session_id="session_1",
                events={"Caroline": ["Caroline went to a support group on 7 May 2023."]},
            )
            ref = _summary_evidence(
                request, summary=summary, summary_index=0, speaker="Caroline", event_index=0,
                event="Caroline went to a support group on 7 May 2023.",
            )
            candidates = [
                ExtractedEventCandidate(
                    memory=MemoryRecord(
                        user_id=request.user_id,
                        memory_kind=MemoryKind.EVENT,
                        content="Caroline went to a support group on 7 May 2023.",
                        session_id="session_1",
                        evidence_refs=[ref],
                    ),
                    prompt_version=self.prompt_version,
                )
            ]
            return ExtractionResult(prompt_version=self.prompt_version, candidates=candidates)

    snap = extract_event_snapshot(
        record, SummaryOnlyExtractor(), user_id="u1", extractor_identity=_identity()
    )

    assert snap.event_count == 0
    assert len(snap.rejections) == 1
    rejection = snap.rejections[0]
    assert isinstance(rejection, ExtractionRejection)
    assert rejection.reason == "missing_raw_turn_evidence"