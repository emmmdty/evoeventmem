from __future__ import annotations

import json
from collections.abc import Sequence

from evoeventmem.core.ports import ChatMessage, ChatResponse, EmbeddingResponse
from evoeventmem.models.cache import CachedChatModel, CachedEmbeddingModel, FileModelCache


class CountingEmbeddingModel:
    model_id = "counting-fake"

    def __init__(self) -> None:
        self.calls = 0

    def embed_texts(self, texts: Sequence[str]) -> list[EmbeddingResponse]:
        self.calls += 1
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
