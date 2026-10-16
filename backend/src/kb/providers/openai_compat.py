"""Clients for OpenAI-compatible local model servers (llama.cpp, Ollama, LM Studio, MLX)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from kb.providers.base import (
    LLM,
    ChatMessage,
    CircuitBreaker,
    Embedder,
    ProviderError,
    Reranker,
)


def _headers(api_key: str | None) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}"} if api_key else {}


class _Remote:
    def __init__(self, base_url: str, api_key: str | None, timeout: float, breaker: CircuitBreaker):
        self.client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers=_headers(api_key),
            timeout=httpx.Timeout(timeout, connect=3.0),
            limits=httpx.Limits(max_connections=32, max_keepalive_connections=8),
        )
        self.breaker = breaker

    async def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.breaker.check()
        try:
            resp = await self.client.post(path, json=payload)
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            self.breaker.failure()
            raise ProviderError(f"{self.breaker.name}: {exc}") from exc
        self.breaker.success()
        return data

    async def aclose(self) -> None:
        await self.client.aclose()


class OpenAIEmbedder(Embedder):
    """``POST {base}/embeddings``. Vectors are L2-normalised and truncated to ``dim`` (MRL)."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        dim: int,
        api_key: str | None = None,
        batch_size: int = 32,
        query_instruction: str = "",
        timeout: float = 30.0,
        breaker: CircuitBreaker | None = None,
    ) -> None:
        self.name = f"embedder:{model}"
        self.model = model
        self.dim = dim
        self.batch_size = batch_size
        self.query_instruction = query_instruction
        self._remote = _Remote(base_url, api_key, timeout, breaker or CircuitBreaker(self.name))

    def _fit(self, vec: list[float]) -> list[float]:
        if len(vec) < self.dim:
            raise ProviderError(f"embedding has {len(vec)} dims, expected >= {self.dim}")
        vec = vec[: self.dim]
        norm = sum(v * v for v in vec) ** 0.5 or 1.0
        return [v / norm for v in vec]

    async def _embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), self.batch_size):
            batch = texts[i : i + self.batch_size]
            data = await self._remote.post("/embeddings", {"model": self.model, "input": batch})
            items = sorted(data["data"], key=lambda d: d.get("index", 0))
            out.extend(self._fit(item["embedding"]) for item in items)
        return out

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return await self._embed(texts)

    async def embed_query(self, text: str) -> list[float]:
        return (await self._embed([self.query_instruction + text]))[0]


class LlamaCppReranker(Reranker):
    """llama-server ``--reranking`` (``/v1/rerank``, Jina/Cohere-compatible shape).

    Works with any server exposing the same API (Infinity, TEI, vLLM, Jina, Cohere).
    """

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str | None = None,
        timeout: float = 30.0,
        breaker: CircuitBreaker | None = None,
    ) -> None:
        self.name = f"reranker:{model}"
        self.model = model
        self._remote = _Remote(base_url, api_key, timeout, breaker or CircuitBreaker(self.name))

    async def rerank(self, query: str, documents: list[str]) -> list[float]:
        if not documents:
            return []
        data = await self._remote.post(
            "/rerank",
            {"model": self.model, "query": query, "documents": documents, "top_n": len(documents)},
        )
        scores = [0.0] * len(documents)
        for item in data.get("results", []):
            scores[item["index"]] = float(item.get("relevance_score", item.get("score", 0.0)))
        return scores


class OpenAIChat(LLM):
    """``POST {base}/chat/completions`` with optional JSON-schema constrained output."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str | None = None,
        timeout: float = 120.0,
        breaker: CircuitBreaker | None = None,
    ) -> None:
        self.name = f"llm:{model}"
        self.model = model
        self._remote = _Remote(base_url, api_key, timeout, breaker or CircuitBreaker(self.name))

    def _payload(
        self,
        messages: list[ChatMessage],
        max_tokens: int,
        temperature: float | None,
        model: str | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model or self.model,
            "messages": messages,
            "max_tokens": max_tokens,
        }
        if temperature is not None:
            payload["temperature"] = temperature
        return payload

    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        max_tokens: int = 1024,
        temperature: float | None = None,
        json_schema: dict[str, Any] | None = None,
        model: str | None = None,
    ) -> str:
        payload = self._payload(messages, max_tokens, temperature, model)
        if json_schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "output", "schema": json_schema, "strict": True},
            }
        data = await self._remote.post("/chat/completions", payload)
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError) as exc:
            raise ProviderError(f"{self.name}: malformed response") from exc

    async def stream(
        self,
        messages: list[ChatMessage],
        *,
        max_tokens: int = 1024,
        temperature: float | None = None,
        model: str | None = None,
    ) -> AsyncIterator[str]:
        remote = self._remote
        remote.breaker.check()
        payload = self._payload(messages, max_tokens, temperature, model) | {"stream": True}
        try:
            async with remote.client.stream("POST", "/chat/completions", json=payload) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    chunk = line[5:].strip()
                    if chunk == "[DONE]":
                        break
                    try:
                        delta = json.loads(chunk)["choices"][0]["delta"].get("content")
                    except (ValueError, KeyError, IndexError):
                        continue
                    if delta:
                        yield delta
        except httpx.HTTPError as exc:
            remote.breaker.failure()
            raise ProviderError(f"{self.name}: {exc}") from exc
        remote.breaker.success()
