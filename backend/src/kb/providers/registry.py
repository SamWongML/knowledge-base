from __future__ import annotations

from functools import cache

from kb.config import Settings, get_settings
from kb.providers.base import LLM, CircuitBreaker, Embedder, ProviderError, Reranker
from kb.providers.fake import FakeEmbedder, FakeLLM, FakeReranker


def _secret(value) -> str | None:  # type: ignore[no-untyped-def]
    return value.get_secret_value() if value is not None else None


def _breaker(name: str, s: Settings) -> CircuitBreaker:
    return CircuitBreaker(name, failures=s.breaker_failures, reset_s=s.breaker_reset_s)


@cache
def get_embedder() -> Embedder:
    s = get_settings()
    if s.embedding_provider == "fake":
        return FakeEmbedder(s.embedding_dim)
    from kb.providers.openai_compat import OpenAIEmbedder

    return OpenAIEmbedder(
        base_url=s.embedding_base_url,
        model=s.embedding_model,
        dim=s.embedding_dim,
        api_key=_secret(s.embedding_api_key),
        batch_size=s.embedding_batch_size,
        query_instruction=s.embedding_query_instruction,
        timeout=s.model_timeout_s,
        breaker=_breaker("embedder", s),
    )


@cache
def get_reranker() -> Reranker | None:
    """Cross-encoder reranker, or None when disabled. LLM reranking lives in kb.search."""
    s = get_settings()
    if s.rerank_provider in ("none", "llm"):
        return None
    if s.rerank_provider == "fake":
        return FakeReranker()
    from kb.providers.openai_compat import LlamaCppReranker

    return LlamaCppReranker(
        base_url=s.rerank_base_url,
        model=s.rerank_model,
        timeout=s.model_timeout_s,
        breaker=_breaker("reranker", s),
    )


@cache
def get_llm(kind: str = "local") -> LLM:
    """``local`` = on-device model (default ``KB_LLM_PROVIDER``); ``cloud`` = Claude."""
    s = get_settings()
    provider = "anthropic" if kind == "cloud" else s.llm_provider
    if provider == "fake":
        return FakeLLM()
    if provider == "anthropic":
        key = _secret(s.anthropic_api_key)
        if not key:
            raise ProviderError("KB_ANTHROPIC_API_KEY is not set")
        from kb.providers.anthropic_llm import AnthropicLLM

        return AnthropicLLM(api_key=key, model=s.anthropic_model, breaker=_breaker("claude", s))
    from kb.providers.openai_compat import OpenAIChat

    return OpenAIChat(
        base_url=s.llm_base_url,
        model=s.llm_model,
        api_key=_secret(s.llm_api_key),
        timeout=max(s.model_timeout_s, 120.0),
        breaker=_breaker("llm", s),
    )


def reset_providers() -> None:
    get_embedder.cache_clear()
    get_reranker.cache_clear()
    get_llm.cache_clear()
