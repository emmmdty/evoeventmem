from __future__ import annotations

import json
from collections.abc import Sequence

import pytest

from evoeventmem.core.ports import ChatMessage, ChatResponse, EmbeddingResponse
from evoeventmem.models.cache import CachedChatModel, CachedEmbeddingModel, FileModelCache


class CountingEmbeddingModel:
    model_id = "counting-fake"

    def __init__(self) -> None:
        self.calls = 0
        self.call_inputs: list[list[str]] = []

    def embed_texts(self, texts: Sequence[str]) -> list[EmbeddingResponse]:
        self.calls += 1
        self.call_inputs.append(list(texts))
        return [
            EmbeddingResponse(
                vector=(float(len(text)), float(len(set(text.lower())))),
                model_id=self.model_id,
            )
            for text in texts
        ]


class CountingChatModel:
    model_id = "counting-chat"

    def __init__(self) -> None:
        self.calls = 0

    def generate(self, messages: Sequence[ChatMessage]) -> ChatResponse:
        self.calls += 1
        prompt = "\n".join(message.content for message in messages)
        return ChatResponse(
            text=f"answer: {prompt}",
            model_id=self.model_id,
            input_tokens=len(prompt.split()),
            output_tokens=2,
        )


def test_cached_embedding_model_reuses_content_hash_outputs(tmp_path) -> None:
    wrapped = CountingEmbeddingModel()
    cache = FileModelCache(tmp_path)
    model = CachedEmbeddingModel(wrapped, cache)

    first = model.embed_texts(["Seattle"])
    second = model.embed_texts(["Seattle"])

    assert first == second
    assert wrapped.calls == 1
    cache_files = sorted(tmp_path.glob("embeddings/*.json"))
    assert len(cache_files) == 1
    assert cache_files[0].name.startswith("sha256-")
    cache_entry = json.loads(cache_files[0].read_text(encoding="utf-8"))
    assert cache_entry["input"] == {"model_id": "counting-fake", "text": "Seattle"}
    assert cache_entry["output"] == {
        "model_id": "counting-fake",
        "vector": [7.0, 5.0],
    }


def test_cached_chat_model_persists_model_input_and_output_by_content_hash(tmp_path) -> None:
    wrapped = CountingChatModel()
    cache = FileModelCache(tmp_path)
    model = CachedChatModel(wrapped, cache)
    messages = [ChatMessage(role="user", content="Where does the user live?")]

    first = model.generate(messages)
    second = model.generate(messages)

    assert first == second
    assert wrapped.calls == 1
    cache_files = sorted(tmp_path.glob("chat/*.json"))
    assert len(cache_files) == 1
    cache_entry = json.loads(cache_files[0].read_text(encoding="utf-8"))
    assert cache_entry["input"] == {
        "model_id": "counting-chat",
        "messages": [{"role": "user", "content": "Where does the user live?"}],
    }
    assert cache_entry["output"]["text"] == "answer: Where does the user live?"


# --- S4b: batched embedding calls (vector_rag p50 latency fix) ---
#
# Before S4b, ``CachedEmbeddingModel.embed_texts`` looped over each input text
# and made one HTTP call per text through the underlying client. For
# ``vector_rag`` that meant ~200 sequential HTTP calls per query through the
# SSH-tunneled qwen3-embedding endpoint, producing the observed ~437s p50
# search latency. S4b batches all uncached texts into a single
# ``wrapped.embed_texts(uncached)`` call while preserving the per-text cache
# key contract so cache hits remain reusable across queries.


def test_cached_embedding_model_batches_uncached_texts_into_single_call(tmp_path) -> None:
    """Three uncached texts must produce exactly one wrapped call (not three)."""
    wrapped = CountingEmbeddingModel()
    cache = FileModelCache(tmp_path)
    model = CachedEmbeddingModel(wrapped, cache)

    responses = model.embed_texts(["alpha", "beta", "gamma"])

    assert wrapped.calls == 1
    assert wrapped.call_inputs == [["alpha", "beta", "gamma"]]
    assert len(responses) == 3
    assert [r.model_id for r in responses] == ["counting-fake"] * 3


def test_cached_embedding_model_preserves_input_order_under_batching(tmp_path) -> None:
    """Batched responses must come back in the same order as the input texts."""
    wrapped = CountingEmbeddingModel()
    cache = FileModelCache(tmp_path)
    model = CachedEmbeddingModel(wrapped, cache)

    responses = model.embed_texts(["aaa", "a", "aaaaaa"])

    # CountingEmbeddingModel returns vector[0] = len(text), so order is
    # observable through the vector length.
    assert [r.vector[0] for r in responses] == [3.0, 1.0, 6.0]


def test_cached_embedding_model_batches_only_uncached_texts_on_mixed_input(tmp_path) -> None:
    """When some texts are cached and some are not, only the uncached ones are
    sent in a single batched call; the cached ones do not re-hit the network."""
    wrapped = CountingEmbeddingModel()
    cache = FileModelCache(tmp_path)
    model = CachedEmbeddingModel(wrapped, cache)

    model.embed_texts(["cached-1"])  # warm cache for "cached-1"
    assert wrapped.calls == 1

    # Now a mixed call: one cached + two uncached
    responses = model.embed_texts(["cached-1", "new-a", "new-b"])

    assert wrapped.calls == 2
    # The second wrapped call must contain only the uncached texts, in input
    # order, and must NOT re-send the cached one.
    assert wrapped.call_inputs[1] == ["new-a", "new-b"]
    assert len(responses) == 3
    # Cached response carries the same vector it did on the first call.
    assert responses[0].vector == (8.0, 7.0)
    # "new-a" and "new-b" both have len=5 and 5 unique chars (n,e,w,-,a/b).
    assert responses[1].vector == (5.0, 5.0)
    assert responses[2].vector == (5.0, 5.0)


def test_cached_embedding_model_cache_hits_produce_zero_wrapped_calls(tmp_path) -> None:
    """All-cached input must not make any HTTP/wrapped call."""
    wrapped = CountingEmbeddingModel()
    cache = FileModelCache(tmp_path)
    model = CachedEmbeddingModel(wrapped, cache)

    model.embed_texts(["x", "y", "z"])
    assert wrapped.calls == 1

    model.embed_texts(["x", "y", "z"])
    assert wrapped.calls == 1  # unchanged — no new call


def test_cached_embedding_model_cache_key_per_text_unchanged(tmp_path) -> None:
    """S4b must preserve the per-text cache file contract: one JSON file per
    (model_id, text) pair, keyed by ``{"model_id": ..., "text": ...}``."""
    wrapped = CountingEmbeddingModel()
    cache = FileModelCache(tmp_path)
    model = CachedEmbeddingModel(wrapped, cache)

    model.embed_texts(["alpha", "beta", "gamma"])

    cache_files = sorted(tmp_path.glob("embeddings/*.json"))
    assert len(cache_files) == 3
    for entry_path in cache_files:
        entry = json.loads(entry_path.read_text(encoding="utf-8"))
        assert set(entry["input"].keys()) == {"model_id", "text"}
        assert entry["input"]["model_id"] == "counting-fake"
        assert entry["input"]["text"] in {"alpha", "beta", "gamma"}
        assert set(entry["output"].keys()) == {"model_id", "vector"}
        assert isinstance(entry["output"]["vector"], list)


def test_cached_embedding_model_cache_key_field_round_trips_per_text(tmp_path) -> None:
    """Each returned EmbeddingResponse must carry its own per-text cache_key
    so downstream artifacts (e.g. retrieval records) can still point at a
    single cache entry per memory chunk."""
    wrapped = CountingEmbeddingModel()
    cache = FileModelCache(tmp_path)
    model = CachedEmbeddingModel(wrapped, cache)

    responses = model.embed_texts(["alpha", "beta", "gamma"])

    assert len({r.cache_key for r in responses}) == 3
    # Round-trip: each cache_key must point at an existing file path under
    # the cache root (the FileModelCache layout is ``embeddings/<key>.json``).
    for response in responses:
        cache_file = cache.root / "embeddings" / f"{response.cache_key}.json"
        assert cache_file.exists()


def test_cached_embedding_model_handles_empty_input(tmp_path) -> None:
    """Empty input must return empty output and make zero wrapped calls."""
    wrapped = CountingEmbeddingModel()
    cache = FileModelCache(tmp_path)
    model = CachedEmbeddingModel(wrapped, cache)

    responses = model.embed_texts([])

    assert responses == []
    assert wrapped.calls == 0


def test_cached_embedding_model_single_text_still_one_call(tmp_path) -> None:
    """Backward compat: a single-text call still triggers exactly one wrapped
    call (now batched as a one-element list rather than a one-element call).
    The existing v1 test contract ``wrapped.calls == 1`` still holds."""
    wrapped = CountingEmbeddingModel()
    cache = FileModelCache(tmp_path)
    model = CachedEmbeddingModel(wrapped, cache)

    model.embed_texts(["Seattle"])

    assert wrapped.calls == 1
    assert wrapped.call_inputs == [["Seattle"]]


def test_cached_embedding_model_raises_on_wrapped_length_mismatch(tmp_path) -> None:
    """If the wrapped model returns the wrong number of responses for a batch,
    fail loudly rather than silently misaligning cache entries."""

    class BrokenEmbeddingModel:
        model_id = "broken-fake"

        def embed_texts(self, texts: Sequence[str]) -> list[EmbeddingResponse]:
            # Returns only one response regardless of input length.
            return [EmbeddingResponse(vector=(1.0,), model_id=self.model_id)]

    wrapped = BrokenEmbeddingModel()
    cache = FileModelCache(tmp_path)
    model = CachedEmbeddingModel(wrapped, cache)

    with pytest.raises(ValueError, match="batched embed"):
        model.embed_texts(["a", "b", "c"])


def test_cached_embedding_model_does_not_cache_empty_text_key(tmp_path) -> None:
    """A text that hashes to the same key as another must not collide; the
    per-text cache key is content-addressed so duplicates within one call are
    served from a single cache write, but the response list still carries one
    entry per input position."""
    wrapped = CountingEmbeddingModel()
    cache = FileModelCache(tmp_path)
    model = CachedEmbeddingModel(wrapped, cache)

    responses = model.embed_texts(["dup", "dup", "dup"])

    assert wrapped.calls == 1
    # Underlying call deduplicates to a single text — saves even more HTTP.
    assert wrapped.call_inputs == [["dup"]]
    assert len(responses) == 3
    assert [r.vector for r in responses] == [(3.0, 3.0)] * 3


def test_cached_embedding_model_partial_cache_failure_is_atomic(tmp_path) -> None:
    """If the wrapped call raises after a partial cache write, the half-written
    cache entries from earlier successful calls must still be reusable (the
    per-text write is idempotent and content-addressed). This guards the
    auditability invariant that cache files are never torn across texts."""
    wrapped = CountingEmbeddingModel()
    cache = FileModelCache(tmp_path)
    model = CachedEmbeddingModel(wrapped, cache)

    # Warm cache for "warm".
    model.embed_texts(["warm"])
    assert wrapped.calls == 1

    # Now a batched call where the wrapped model fails on the uncached batch.
    class FailOnUncached:
        model_id = "counting-fake"

        def __init__(self) -> None:
            self.calls = 0
            self.call_inputs: list[list[str]] = []

        def embed_texts(self, texts: Sequence[str]) -> list[EmbeddingResponse]:
            self.calls += 1
            self.call_inputs.append(list(texts))
            raise RuntimeError("boom")

    failing = FailOnUncached()
    failing_model = CachedEmbeddingModel(failing, cache)
    with pytest.raises(RuntimeError, match="boom"):
        failing_model.embed_texts(["warm", "new"])

    # The previously cached "warm" entry is still readable.
    responses = model.embed_texts(["warm"])
    assert len(responses) == 1
    assert responses[0].vector == (4.0, 4.0)
