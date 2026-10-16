"""Deterministic, dependency-free providers for tests, CI and demo mode.

The fake embedder hashes word unigrams and bigrams into a fixed-size vector, so texts
sharing vocabulary land close together: good enough to exercise the whole pipeline.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import re
from collections.abc import AsyncIterator
from typing import Any

from kb.providers.base import LLM, ChatMessage, Embedder, Reranker

_WORD = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> list[str]:
    words = _WORD.findall(text.lower())
    return words + [f"{a}_{b}" for a, b in itertools.pairwise(words)]


def _bucket(token: str, dim: int) -> tuple[int, float]:
    h = hashlib.blake2b(token.encode(), digest_size=8).digest()
    idx = int.from_bytes(h[:4], "little") % dim
    sign = 1.0 if h[4] & 1 else -1.0
    return idx, sign


class FakeEmbedder(Embedder):
    name = "embedder:fake"

    def __init__(self, dim: int = 1024) -> None:
        self.dim = dim

    def _vec(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for tok in _tokens(text):
            idx, sign = _bucket(tok, self.dim)
            vec[idx] += sign
        norm = sum(v * v for v in vec) ** 0.5
        if norm == 0:
            vec[0] = 1.0
            return vec
        return [v / norm for v in vec]

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    async def embed_query(self, text: str) -> list[float]:
        return self._vec(text)


class FakeReranker(Reranker):
    """Scores by query-term overlap (a crude but deterministic cross-encoder stand-in)."""

    name = "reranker:fake"

    async def rerank(self, query: str, documents: list[str]) -> list[float]:
        q = set(_WORD.findall(query.lower()))
        if not q:
            return [0.0] * len(documents)
        out = []
        for doc in documents:
            d = set(_WORD.findall(doc.lower()))
            out.append(len(q & d) / len(q))
        return out


class FakeLLM(LLM):
    """Returns schema-shaped JSON for structured calls and an extractive answer otherwise."""

    name = "llm:fake"
    model = "fake"

    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        max_tokens: int = 1024,
        temperature: float | None = None,
        json_schema: dict[str, Any] | None = None,
        model: str | None = None,
    ) -> str:
        if json_schema is not None:
            return json.dumps(_example(json_schema, messages[-1]["content"]))
        return _extractive(messages[-1]["content"])

    async def stream(
        self,
        messages: list[ChatMessage],
        *,
        max_tokens: int = 1024,
        temperature: float | None = None,
        model: str | None = None,
    ) -> AsyncIterator[str]:
        text = _extractive(messages[-1]["content"])
        for word in text.split(" "):
            yield word + " "


def _extractive(prompt: str) -> str:
    cited = re.findall(r"\[(\d+)\]", prompt)
    marker = f" [{cited[0]}]" if cited else ""
    return f"Based on the knowledge base, here is what I found{marker}."


def _example(schema: dict[str, Any], prompt: str) -> Any:
    kind = schema.get("type")
    if "enum" in schema:
        return schema["enum"][0]
    if kind == "object":
        props = schema.get("properties", {})
        return {k: _example(v, prompt) for k, v in props.items()}
    if kind == "array":
        item = schema.get("items", {"type": "string"})
        return [_example(item, prompt)] if schema.get("minItems", 0) > 0 else []
    if kind == "string":
        return prompt[:80]
    if kind in ("number", "integer"):
        return 0
    if kind == "boolean":
        return True
    return None
