from __future__ import annotations

from collections.abc import Sequence

from evoeventmem.core.ports import EmbeddingResponse
from evoeventmem.models.cache import CachedEmbeddingModel, FileModelCache


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
