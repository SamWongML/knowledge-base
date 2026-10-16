from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any, Literal, TypedDict


class ProviderError(RuntimeError):
    """A model server failed or returned something unusable."""


class CircuitOpenError(ProviderError):
    """The provider failed repeatedly and is temporarily skipped (degraded mode)."""


class ChatMessage(TypedDict):
    role: Literal["system", "user", "assistant"]
    content: str


class CircuitBreaker:
    """Classic three-state breaker so a dead model server costs one timeout, not one per request."""

    def __init__(self, name: str, failures: int = 3, reset_s: float = 30.0) -> None:
        self.name = name
        self.max_failures = failures
        self.reset_s = reset_s
        self._failures = 0
        self._opened_at: float | None = None

    @property
    def is_open(self) -> bool:
        if self._opened_at is None:
            return False
        # Half-open after the reset window: let one call through.
        return time.monotonic() - self._opened_at < self.reset_s

    def check(self) -> None:
        if self.is_open:
            raise CircuitOpenError(f"{self.name} is unavailable (circuit open)")

    def success(self) -> None:
        self._failures = 0
        self._opened_at = None

    def failure(self) -> None:
        self._failures += 1
        if self._failures >= self.max_failures:
            self._opened_at = time.monotonic()


class Embedder(ABC):
    name: str = "embedder"
    dim: int

    @abstractmethod
    async def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    @abstractmethod
    async def embed_query(self, text: str) -> list[float]: ...

    async def health(self) -> bool:
        await self.embed_query("ping")
        return True


class Reranker(ABC):
    name: str = "reranker"

    @abstractmethod
    async def rerank(self, query: str, documents: list[str]) -> list[float]:
        """Return one relevance score per document, same order, higher = more relevant."""

    async def health(self) -> bool:
        await self.rerank("ping", ["pong"])
        return True


class LLM(ABC):
    name: str = "llm"
    model: str

    @abstractmethod
    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        max_tokens: int = 1024,
        temperature: float | None = None,
        json_schema: dict[str, Any] | None = None,
        model: str | None = None,
    ) -> str:
        """Return the full completion. With ``json_schema`` the reply is a JSON document."""

    @abstractmethod
    def stream(
        self,
        messages: list[ChatMessage],
        *,
        max_tokens: int = 1024,
        temperature: float | None = None,
        model: str | None = None,
    ) -> AsyncIterator[str]:
        """Yield text deltas."""

    async def health(self) -> bool:
        await self.complete([{"role": "user", "content": "Reply with OK."}], max_tokens=4)
        return True
