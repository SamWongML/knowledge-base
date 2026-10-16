"""Claude (default: Haiku 5.5) as an optional cloud LLM for reranking, context generation and answers."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import anthropic

from kb.providers.base import LLM, ChatMessage, CircuitBreaker, ProviderError


class AnthropicLLM(LLM):
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout: float = 60.0,
        breaker: CircuitBreaker | None = None,
    ) -> None:
        self.name = f"anthropic:{model}"
        self.model = model
        self.client = anthropic.AsyncAnthropic(api_key=api_key, timeout=timeout, max_retries=2)
        self.breaker = breaker or CircuitBreaker(self.name)

    @staticmethod
    def _split(messages: list[ChatMessage]) -> tuple[str, list[dict[str, Any]]]:
        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        rest = [
            {"role": m["role"], "content": m["content"]} for m in messages if m["role"] != "system"
        ]
        return system, rest

    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        max_tokens: int = 1024,
        temperature: float | None = None,  # ignored: Haiku 5.5 rejects non-default sampling
        json_schema: dict[str, Any] | None = None,
        model: str | None = None,
    ) -> str:
        self.breaker.check()
        system, rest = self._split(messages)
        kwargs: dict[str, Any] = {
            "model": model or self.model,
            "max_tokens": max(max_tokens, 1024),
            "messages": rest,
            # Retrieval helpers are simple, latency-sensitive calls.
            "output_config": {"effort": "low"},
        }
        if system:
            # Stable system prompts are cached across calls (e.g. one document, many chunks).
            kwargs["system"] = [
                {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
            ]
        if json_schema is not None:
            kwargs["output_config"]["format"] = {"type": "json_schema", "schema": json_schema}
        try:
            resp = await self.client.messages.create(**kwargs)
        except anthropic.APIError as exc:
            self.breaker.failure()
            raise ProviderError(f"{self.name}: {exc}") from exc
        self.breaker.success()
        if resp.stop_reason == "refusal":
            raise ProviderError(f"{self.name}: refused")
        return "".join(b.text for b in resp.content if b.type == "text")

    async def stream(
        self,
        messages: list[ChatMessage],
        *,
        max_tokens: int = 2048,
        temperature: float | None = None,
        model: str | None = None,
    ) -> AsyncIterator[str]:
        self.breaker.check()
        system, rest = self._split(messages)
        kwargs: dict[str, Any] = {
            "model": model or self.model,
            "max_tokens": max(max_tokens, 1024),
            "messages": rest,
            "output_config": {"effort": "low"},
        }
        if system:
            kwargs["system"] = system
        try:
            async with self.client.messages.stream(**kwargs) as stream:
                async for text in stream.text_stream:
                    yield text
        except anthropic.APIError as exc:
            self.breaker.failure()
            raise ProviderError(f"{self.name}: {exc}") from exc
        self.breaker.success()
