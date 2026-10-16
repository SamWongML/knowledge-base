"""Model providers (embeddings, rerankers, chat LLMs) behind small async interfaces.

Use :func:`get_embedder`, :func:`get_reranker` and :func:`get_llm` everywhere; they read
settings, wrap remote calls in a circuit breaker and are cached per process.
"""

from kb.providers.base import (
    LLM,
    ChatMessage,
    CircuitOpenError,
    Embedder,
    ProviderError,
    Reranker,
)
from kb.providers.registry import get_embedder, get_llm, get_reranker, reset_providers

__all__ = [
    "LLM",
    "ChatMessage",
    "CircuitOpenError",
    "Embedder",
    "ProviderError",
    "Reranker",
    "get_embedder",
    "get_llm",
    "get_reranker",
    "reset_providers",
]
