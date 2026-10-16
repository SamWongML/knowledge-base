"""Runtime configuration, read from environment variables prefixed with ``KB_``.

Every model-backed stage is pluggable. Provider strings use the form ``<kind>`` and
point at an endpoint configured separately, so the same code runs against
llama.cpp / Ollama / LM Studio / MLX servers on the Mac mini, Anthropic in the
cloud, or deterministic fakes in tests and CI.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

EmbeddingProvider = Literal["openai", "fake"]
RerankProvider = Literal["llamacpp", "llm", "none", "fake"]
LLMProvider = Literal["openai", "anthropic", "fake"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="KB_", env_file=".env", extra="ignore")

    # --- service -------------------------------------------------------------------
    env: Literal["dev", "test", "prod"] = "dev"
    host: str = "127.0.0.1"
    port: int = 8787
    workers: int = 2
    # Bearer token required on write endpoints and MCP when set. Empty = open (localhost only!).
    api_token: SecretStr | None = None
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])
    # Serve the built SPA from this directory when it exists.
    static_dir: str = "../frontend/dist"
    log_level: str = "INFO"
    log_json: bool = False

    # --- database ------------------------------------------------------------------
    database_url: str = "postgresql://kb:kb@localhost:5432/kb"
    db_pool_min: int = 2
    db_pool_max: int = 16
    db_statement_timeout_ms: int = 15_000

    # --- ingestion -----------------------------------------------------------------
    worker_concurrency: int = 4
    job_max_attempts: int = 5
    job_lease_seconds: int = 300
    chunk_target_tokens: int = 384
    chunk_overlap_tokens: int = 48
    max_upload_mb: int = 100
    # Generate an LLM "situating context" per chunk (Anthropic Contextual Retrieval).
    contextual_chunks: bool = False

    # --- embeddings ----------------------------------------------------------------
    embedding_provider: EmbeddingProvider = "openai"
    # OpenAI-compatible /v1/embeddings endpoint (llama-server, Ollama, LM Studio, MLX).
    embedding_base_url: str = "http://127.0.0.1:8081/v1"
    embedding_model: str = "Qwen3-Embedding-0.6B"
    embedding_api_key: SecretStr | None = None
    embedding_dim: int = 1024
    embedding_batch_size: int = 32
    # Instruction prefix for queries (Qwen3-Embedding style instruction-aware models).
    embedding_query_instruction: str = (
        "Instruct: Given a search query, retrieve relevant passages that answer the query\nQuery: "
    )

    # --- reranking -----------------------------------------------------------------
    rerank_provider: RerankProvider = "llamacpp"
    # llama-server started with --reranking exposes /v1/rerank.
    rerank_base_url: str = "http://127.0.0.1:8082/v1"
    rerank_model: str = "Qwen3-Reranker-0.6B"
    # Optional second, LLM-judged stage over the cross-encoder's top results.
    llm_rerank: bool = False
    llm_rerank_top_n: int = 20

    # --- chat / agent LLMs ---------------------------------------------------------
    llm_provider: LLMProvider = "openai"
    # OpenAI-compatible /v1/chat/completions (Ollama, LM Studio, mlx-lm, llama-server).
    llm_base_url: str = "http://127.0.0.1:11434/v1"
    llm_model: str = "qwen3.5:9b"
    # Smaller model for routing / grading / query rewriting (JSON-schema constrained).
    llm_router_model: str = "qwen3.5:4b"
    llm_api_key: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None
    anthropic_model: str = "claude-haiku-5-5"
    agent_max_steps: int = 4
    agent_timeout_s: float = 90.0
    # Ask local servers for non-thinking mode (Qwen3.x): faster, and steps are schema-constrained.
    llm_disable_thinking: bool = True

    # --- search defaults -----------------------------------------------------------
    search_candidates: int = 100  # per retriever
    rrf_k: int = 60
    rerank_candidates: int = 50
    default_limit: int = 10

    # --- resilience ----------------------------------------------------------------
    model_timeout_s: float = 30.0
    breaker_failures: int = 3
    breaker_reset_s: float = 30.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
