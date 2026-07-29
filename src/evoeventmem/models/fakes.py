from __future__ import annotations

import math
import re
from collections.abc import Sequence

from evoeventmem.core.ports import ChatMessage, ChatResponse, EmbeddingResponse


class DeterministicFakeChatModel:
    def __init__(self, model_id: str = "deterministic-local-fake") -> None:
        self.model_id = model_id

    def generate(self, messages: Sequence[ChatMessage]) -> ChatResponse:
        prompt = "\n".join(message.content for message in messages)
        normalized_prompt = prompt.lower()
        if "moved to seattle" in normalized_prompt:
            text = "Seattle"
        elif "live in austin" in normalized_prompt:
            text = "Austin"
        elif "support group yesterday" in normalized_prompt:
            text = "7 May 2023"
        else:
            text = ""
        return ChatResponse(
            text=text,
            model_id=self.model_id,
            input_tokens=_count_tokens(prompt),
            output_tokens=_count_tokens(text),
        )


class DeterministicFakeEmbeddingModel:
    def __init__(self, model_id: str = "deterministic-local-embedding") -> None:
        self.model_id = model_id

    def embed_texts(self, texts: Sequence[str]) -> list[EmbeddingResponse]:
        return [
            EmbeddingResponse(vector=_keyword_vector(text), model_id=self.model_id)
            for text in texts
        ]


def _keyword_vector(text: str) -> tuple[float, ...]:
    tokens = set(re.findall(r"[a-z0-9]+", text.lower()))
    aliases = {
        "live": {"live", "lives", "living", "moved"},
        "seattle": {"seattle", "city", "current", "currently"},
        "austin": {"austin"},
        "support": {"support", "group", "lgbtq"},
        "date": {"when", "yesterday", "date", "may", "2023"},
    }
    values = [1.0 if tokens & alias_tokens else 0.0 for alias_tokens in aliases.values()]
    values.append(math.sqrt(float(len(tokens))) / 10.0)
    return tuple(values)


def _count_tokens(text: str) -> int:
    return len(text.split())
