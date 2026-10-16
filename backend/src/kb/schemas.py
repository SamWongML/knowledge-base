"""Public API contract. Shared by the HTTP API, the MCP server and the CLI.

Keep these stable: the frontend generates TypeScript types from the OpenAPI schema
they produce, and agents rely on the field names.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

DocumentStatus = Literal["pending", "processing", "ready", "failed"]
StepType = Literal["plan", "search", "read", "reflect", "answer_delta", "answer", "error"]
SearchMode = Literal["hybrid", "lexical", "semantic"]


class Model(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


# --- collections -------------------------------------------------------------------


class CollectionIn(Model):
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    name: str = Field(min_length=1, max_length=200)
    description: str = ""


class Collection(Model):
    id: UUID
    slug: str
    name: str
    description: str
    created_at: datetime
    document_count: int = 0


# --- documents ---------------------------------------------------------------------


class DocumentIn(Model):
    """Create a document from text (Markdown preferred). Files go through multipart upload."""

    title: str | None = Field(default=None, max_length=500)
    content: str = Field(min_length=1)
    collection: str = "default"  # collection slug
    source_uri: str | None = None
    mime_type: str = "text/markdown"
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class DocumentPatch(Model):
    title: str | None = None
    content: str | None = None  # replacing content triggers re-ingestion
    tags: list[str] | None = None
    metadata: dict[str, Any] | None = None
    collection: str | None = None


class DocumentSummary(Model):
    id: UUID
    collection_id: UUID
    collection: str  # slug
    title: str
    source_uri: str | None
    mime_type: str
    tags: list[str]
    status: DocumentStatus
    error: str | None
    chunk_count: int
    created_at: datetime
    updated_at: datetime


class Document(DocumentSummary):
    body: str
    metadata: dict[str, Any]


class IngestAccepted(Model):
    """Returned by create/upload. Ingestion continues asynchronously."""

    id: UUID
    status: DocumentStatus
    duplicate: bool = False  # true when identical content already existed


class Page[T](Model):
    items: list[T]
    total: int
    limit: int
    offset: int


# --- search ------------------------------------------------------------------------


class SearchFilters(Model):
    collections: list[str] | None = None  # slugs
    tags: list[str] | None = None  # match-any
    document_ids: list[UUID] | None = None
    updated_after: datetime | None = None


class SearchRequest(Model):
    query: str = Field(min_length=1, max_length=2000)
    mode: SearchMode = "hybrid"
    limit: int = Field(default=10, ge=1, le=100)
    filters: SearchFilters = Field(default_factory=SearchFilters)
    rerank: bool = True
    llm_rerank: bool | None = None  # None = server default
    # Expand each hit with neighbouring chunks (small-to-big retrieval).
    expand_context: bool = False


class StageTiming(Model):
    stage: str
    ms: float
    detail: str | None = None


class SearchHit(Model):
    chunk_id: int
    document_id: UUID
    document_title: str
    collection: str
    source_uri: str | None
    heading_path: str
    content: str
    snippet: str  # highlighted with <mark>…</mark>
    score: float  # final score, higher is better
    scores: dict[str, float] = Field(
        default_factory=dict
    )  # per-stage: bm25, dense, rrf, rerank, llm
    ord: int
    tags: list[str] = Field(default_factory=list)


class SearchResponse(Model):
    query: str
    mode: SearchMode
    hits: list[SearchHit]
    timings: list[StageTiming]
    degraded: list[str] = Field(default_factory=list)  # stages skipped because a model was down
    total_ms: float


# --- agentic answer ----------------------------------------------------------------


class AskRequest(Model):
    question: str = Field(min_length=1, max_length=4000)
    filters: SearchFilters = Field(default_factory=SearchFilters)
    max_steps: int | None = Field(default=None, ge=1, le=12)
    # "local" = small on-device model, "cloud" = Claude (needs KB_ANTHROPIC_API_KEY).
    engine: Literal["local", "cloud"] = "local"


class Citation(Model):
    n: int  # [n] marker used in the answer text
    chunk_id: int
    document_id: UUID
    document_title: str
    heading_path: str
    snippet: str


class AgentStep(Model):
    """One step of the agent loop, streamed to clients as it happens."""

    type: StepType
    message: str = ""
    query: str | None = None
    hits: list[SearchHit] | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class AskResponse(Model):
    question: str
    answer: str
    citations: list[Citation]
    steps: list[AgentStep]
    engine: str
    total_ms: float


# --- system ------------------------------------------------------------------------


class ComponentHealth(Model):
    name: str
    ok: bool
    latency_ms: float | None = None
    detail: str | None = None


class Health(Model):
    status: Literal["ok", "degraded", "down"]
    components: list[ComponentHealth]
    version: str


class Stats(Model):
    documents: int
    chunks: int
    collections: int
    queued_jobs: int
    failed_jobs: int
    queries_24h: int
