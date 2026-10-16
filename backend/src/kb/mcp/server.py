"""MCP server: the knowledge base as a small set of workflow-level tools for agents.

Built on the official ``mcp`` SDK (``MCPServer``, spec 2026-07-28, still serving
2025-11-25 clients) over stateless Streamable HTTP. ``build_mcp_app()`` returns a
path-agnostic Starlette app that the API mounts at ``/mcp``; a static bearer token
(``KB_API_TOKEN``) guards every request when configured.

Tools are thin adapters over the same service layer as the REST API. Each returns a
typed ``structuredContent`` (published as ``outputSchema``) plus a compact text
rendering, so agents that only read text content still spend few tokens.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Annotated, Any, Literal
from uuid import UUID

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ResourceError, ResourceNotFoundError, ToolError
from mcp.server.streamable_http_manager import StreamableHTTPASGIApp, StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from mcp_types import CallToolResult, TextContent, ToolAnnotations
from pydantic import BaseModel, Field
from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.types import ASGIApp, Receive, Scope, Send

from kb import __version__
from kb.config import get_settings
from kb.mcp import shapes
from kb.mcp.shapes import plain_snippet

__all__ = ["build_mcp_app", "build_server", "plain_snippet"]

# Documents whose body fits under this many characters are returned whole by kb_read.
READ_MAX_CHARS = 40_000
# Default chunk window for long documents (~384 tokens per chunk -> ~8k tokens).
READ_DEFAULT_CHUNKS = 20

INSTRUCTIONS = """\
Personal knowledge base (notes, docs, decisions). Workflow: kb_search to find passages, \
kb_read to open a document, cite titles/ids in answers. kb_ask runs a full research \
agent and returns a cited answer (slower). Save durable findings with kb_add; pass \
source_uri to make re-saves update the same document instead of duplicating it."""


# --- output models (structuredContent / outputSchema) ---------------------------------


class Out(BaseModel):
    hint: str | None = Field(default=None, description="Guidance for the next call, if any.")


class HitOut(BaseModel):
    chunk_id: int
    document_id: str
    title: str
    heading_path: str
    snippet: str
    score: float
    collection: str
    source_uri: str | None = None


class SearchOut(Out):
    query: str
    hits: list[HitOut]
    degraded: list[str] = Field(default_factory=list)


class SourceOut(BaseModel):
    n: int
    document_id: str
    chunk_id: int
    title: str
    heading_path: str
    snippet: str


class AskOut(Out):
    answer: str
    sources: list[SourceOut]
    engine: str


class ReadOut(Out):
    document_id: str
    title: str
    collection: str
    tags: list[str]
    source_uri: str | None
    status: str
    content: str
    total_chunks: int
    start_chunk: int | None = None
    end_chunk: int | None = Field(default=None, description="Exclusive end of the chunk window.")
    truncated: bool = False
    next_start_chunk: int | None = None


class DocOut(BaseModel):
    id: str
    title: str
    collection: str
    tags: list[str]
    status: str
    source_uri: str | None
    chunk_count: int
    updated_at: str


class ListOut(Out):
    items: list[DocOut]
    total: int
    offset: int
    next_offset: int | None = None


class AddOut(Out):
    id: str
    status: str
    duplicate: bool


class UpdateOut(Out):
    id: str
    title: str
    status: str
    tags: list[str]


class DeleteOut(Out):
    id: str
    deleted: bool


class CollectionOut(BaseModel):
    slug: str
    name: str
    description: str
    document_count: int


class CollectionsOut(Out):
    collections: list[CollectionOut]


# --- helpers ---------------------------------------------------------------------------


def _result(model: Out, text: str) -> CallToolResult:
    """Typed ``structuredContent`` plus a compact text rendering (with the hint appended)."""
    return CallToolResult(
        content=[TextContent(type="text", text=shapes.with_hint(text, model.hint))],
        structured_content=model.model_dump(mode="json"),
    )


def _uuid(value: str, what: str = "document_id") -> UUID:
    try:
        return UUID(str(value))
    except ValueError:
        raise ToolError(
            f"{what} {value!r} is not a valid UUID. Get ids from kb_search or kb_list."
        ) from None


def _invalid(exc: ValueError) -> ToolError:
    msg = str(exc)
    if "collection" in msg:
        msg += ". Use kb_collections to see valid slugs."
    return ToolError(msg)


def _provider_guard(exc: Exception) -> ToolError:
    return ToolError(
        f"A model backend is unavailable ({exc}). Retry later, or use kb_search "
        "with mode='lexical', which needs no models."
    )


# --- tools -----------------------------------------------------------------------------

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)


async def kb_search(
    query: Annotated[str, Field(description="Natural-language question or keywords.")],
    limit: Annotated[int, Field(ge=1, le=50, description="Max hits.")] = 8,
    collections: Annotated[
        list[str] | None, Field(description="Restrict to these collection slugs.")
    ] = None,
    tags: Annotated[list[str] | None, Field(description="Match documents with any tag.")] = None,
    mode: Annotated[
        Literal["hybrid", "lexical", "semantic"],
        Field(description="'lexical' for exact identifiers/error strings; else 'hybrid'."),
    ] = "hybrid",
    rerank: Annotated[bool, Field(description="Cross-encoder rerank (better, slower).")] = True,
) -> Annotated[CallToolResult, SearchOut]:
    """Search the knowledge base and return the most relevant passages with their document ids.

    Use first for any question the user's notes or docs might answer. Follow up with
    kb_read(document_id) to read the full context of a promising hit."""
    from kb.providers.base import ProviderError
    from kb.schemas import SearchFilters, SearchRequest
    from kb.search.pipeline import search

    req = SearchRequest(
        query=query,
        limit=limit,
        mode=mode,
        rerank=rerank,
        filters=SearchFilters(collections=collections or None, tags=tags or None),
    )
    try:
        res = await search(req)
    except ProviderError as exc:
        raise _provider_guard(exc) from exc
    data = shapes.search_result(res.model_dump(mode="json"))
    out = SearchOut.model_validate(data)
    return _result(out, shapes.render_search({**data, "hint": None}))


async def kb_ask(
    question: Annotated[str, Field(description="The question to research and answer.")],
    engine: Annotated[
        Literal["local", "cloud"],
        Field(description="'local' on-device model, or 'cloud' (Claude) if configured."),
    ] = "local",
) -> Annotated[CallToolResult, AskOut]:
    """Answer a question from the knowledge base with an agentic search loop; returns an answer with [n] citations.

    Slower than kb_search (multiple searches + an LLM). Prefer kb_search when you can
    synthesise the answer yourself."""
    from kb.agent.loop import ask
    from kb.providers.base import ProviderError
    from kb.schemas import AskRequest

    try:
        res = await ask(AskRequest(question=question, engine=engine))
    except ProviderError as exc:
        raise _provider_guard(exc) from exc
    data = shapes.ask_result(res.model_dump(mode="json"))
    return _result(AskOut.model_validate(data), shapes.render_ask(data))


async def kb_read(
    document_id: Annotated[str, Field(description="Document id (UUID) from kb_search/kb_list.")],
    start_chunk: Annotated[
        int | None, Field(ge=0, description="First chunk to return (for long documents).")
    ] = None,
    max_chunks: Annotated[
        int | None, Field(ge=1, le=100, description="Number of chunks to return.")
    ] = None,
) -> Annotated[CallToolResult, ReadOut]:
    """Read a document as Markdown. Long documents are returned in chunk windows; continue with start_chunk=next_start_chunk."""
    doc_id = _uuid(document_id)
    from kb.ingest.service import get_chunks, get_document

    doc = await get_document(doc_id)
    if doc is None:
        raise ToolError(f"Document {document_id} not found. Use kb_search or kb_list to find ids.")
    base: dict[str, Any] = {
        "document_id": str(doc.id),
        "title": doc.title,
        "collection": doc.collection,
        "tags": doc.tags,
        "source_uri": doc.source_uri,
        "status": doc.status,
    }
    whole = start_chunk is None and max_chunks is None and len(doc.body) <= READ_MAX_CHARS
    chunks = [] if whole else await get_chunks(doc_id)
    if whole or not chunks:
        content = doc.body
        truncated = len(content) > READ_MAX_CHARS
        hint = None
        if truncated:
            content = content[:READ_MAX_CHARS]
            hint = (
                "Document is long and not yet chunked (status "
                f"{doc.status}); retry later for chunk pagination."
            )
        out = ReadOut(
            **base,
            content=content,
            total_chunks=doc.chunk_count,
            truncated=truncated,
            hint=hint,
        )
    else:
        chunks.sort(key=lambda c: c.get("ord", 0))
        start = min(start_chunk or 0, len(chunks))
        end = min(start + (max_chunks or READ_DEFAULT_CHUNKS), len(chunks))
        window = chunks[start:end]
        has_more = end < len(chunks)
        out = ReadOut(
            **base,
            content="\n\n".join(str(c.get("content", "")) for c in window),
            total_chunks=len(chunks),
            start_chunk=start,
            end_chunk=end,
            truncated=has_more or start > 0,
            next_start_chunk=end if has_more else None,
            hint=(
                f"Showing chunks {start}-{end - 1} of {len(chunks)}. "
                f"Continue with start_chunk={end}, or use kb_search to jump to a section."
                if has_more
                else None
            ),
        )
    return _result(out, shapes.doc_header(base) + "\n\n" + out.content)


async def kb_list(
    collection: Annotated[str | None, Field(description="Collection slug.")] = None,
    tag: Annotated[str | None, Field(description="Only documents with this tag.")] = None,
    query: Annotated[str | None, Field(description="Title substring filter.")] = None,
    limit: Annotated[int, Field(ge=1, le=100)] = 20,
    offset: Annotated[int, Field(ge=0)] = 0,
) -> Annotated[CallToolResult, ListOut]:
    """List documents (newest first) with optional collection/tag/title filters. For content search use kb_search."""
    from kb.ingest.service import list_documents

    page = await list_documents(
        collection=collection, status=None, tag=tag, q=query, limit=limit, offset=offset
    )
    data = shapes.list_result(page.model_dump(mode="json"))
    return _result(ListOut.model_validate(data), shapes.render_list({**data, "hint": None}))


async def kb_add(
    content: Annotated[
        str, Field(min_length=1, description="Markdown body. Start with a one-line summary.")
    ],
    title: Annotated[
        str | None, Field(description="Title; derived from content if omitted.")
    ] = None,
    collection: Annotated[str, Field(description="Collection slug.")] = "default",
    tags: Annotated[list[str] | None, Field(description="Lowercase topic tags.")] = None,
    source_uri: Annotated[
        str | None,
        Field(
            description="Stable origin (URL, file path, 'note:<slug>'). Re-adding with the same source_uri updates that document."
        ),
    ] = None,
) -> Annotated[CallToolResult, AddOut]:
    """Save a note or document to the knowledge base. Indexing is asynchronous (status 'pending' until searchable)."""
    from kb.ingest.service import create_document
    from kb.schemas import DocumentIn

    try:
        doc_in = DocumentIn(
            content=content,
            title=title,
            collection=collection,
            tags=tags or [],
            source_uri=source_uri,
        )
        res = await create_document(doc_in)
    except ValueError as exc:  # unknown collection, conflict, validation
        raise _invalid(exc) from exc
    out = AddOut(
        id=str(res.id),
        status=res.status,
        duplicate=res.duplicate,
        hint="Identical content already exists; returned its id." if res.duplicate else None,
    )
    verb = "duplicate of" if res.duplicate else "saved"
    return _result(out, f"{verb} doc={out.id} status={out.status}")


async def kb_update(
    document_id: Annotated[str, Field(description="Document id (UUID).")],
    content: Annotated[
        str | None, Field(description="New full Markdown body (re-indexes).")
    ] = None,
    title: Annotated[str | None, Field(description="New title.")] = None,
    tags: Annotated[list[str] | None, Field(description="Replacement tag list.")] = None,
) -> Annotated[CallToolResult, UpdateOut]:
    """Update a document's content, title or tags. Content replaces the whole body; read it first to edit."""
    doc_id = _uuid(document_id)
    if content is None and title is None and tags is None:
        raise ToolError("Nothing to update: pass content, title and/or tags.")
    from kb.ingest.service import update_document
    from kb.schemas import DocumentPatch

    fields = {"content": content, "title": title, "tags": tags}
    patch = DocumentPatch(**{k: v for k, v in fields.items() if v is not None})
    try:
        doc = await update_document(doc_id, patch)
    except ValueError as exc:
        raise _invalid(exc) from exc
    if doc is None:
        raise ToolError(f"Document {document_id} not found.")
    out = UpdateOut(id=str(doc.id), title=doc.title, status=doc.status, tags=doc.tags)
    return _result(out, f"updated doc={out.id} title={out.title!r} status={out.status}")


async def kb_delete(
    document_id: Annotated[str, Field(description="Document id (UUID).")],
) -> Annotated[CallToolResult, DeleteOut]:
    """Permanently delete a document and its index entries. Only when the user explicitly asks."""
    doc_id = _uuid(document_id)
    from kb.ingest.service import delete_document

    if not await delete_document(doc_id):
        raise ToolError(f"Document {document_id} not found.")
    return _result(DeleteOut(id=str(doc_id), deleted=True), f"deleted doc={doc_id}")


async def kb_collections() -> Annotated[CallToolResult, CollectionsOut]:
    """List collections (slug, name, document count). Use slugs as `collection`/`collections` arguments."""
    from kb.ingest.service import list_collections

    rows = [shapes.collection_row(c.model_dump(mode="json")) for c in await list_collections()]
    return _result(
        CollectionsOut.model_validate({"collections": rows}), shapes.render_collections(rows)
    )


async def read_document_resource(document_id: str) -> str:
    """Full Markdown body of a document."""
    from kb.ingest.service import get_document

    try:
        doc_id = UUID(document_id)
    except ValueError:
        raise ResourceError(f"invalid document id {document_id!r}") from None
    doc = await get_document(doc_id)
    if doc is None:
        raise ResourceNotFoundError(f"document {document_id} not found")
    return doc.body


TOOLS: list[tuple[Any, ToolAnnotations]] = [
    (kb_search, READ_ONLY),
    (kb_ask, READ_ONLY),
    (kb_read, READ_ONLY),
    (kb_list, READ_ONLY),
    (
        kb_add,
        ToolAnnotations(
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        ),
    ),
    (
        kb_update,
        ToolAnnotations(
            read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=False
        ),
    ),
    (
        kb_delete,
        ToolAnnotations(
            read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=False
        ),
    ),
    (kb_collections, READ_ONLY),
]


def build_server() -> MCPServer:
    """The MCP server with all tools and the ``kb://documents/{id}`` resource template."""
    server: MCPServer = MCPServer(
        "kb",
        title="Knowledge Base",
        instructions=INSTRUCTIONS,
        version=__version__,
        log_level="WARNING",
    )
    for fn, hints in TOOLS:
        server.add_tool(fn, annotations=hints)
    server.resource(
        "kb://documents/{document_id}",
        name="document",
        title="Knowledge base document",
        mime_type="text/markdown",
    )(read_document_resource)
    return server


# --- ASGI ------------------------------------------------------------------------------


class BearerAuth:
    """Reject requests without the configured bearer token (no-op when no token is set)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            from kb.api.deps import check_token

            auth = None
            for key, value in scope.get("headers", []):
                if key == b"authorization":
                    auth = value.decode("latin-1")
                    break
            if not check_token(auth):
                from starlette.responses import JSONResponse

                response = JSONResponse(
                    {"error": "invalid or missing bearer token"},
                    status_code=401,
                    headers={"WWW-Authenticate": 'Bearer realm="kb"'},
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


def _transport_security() -> TransportSecuritySettings:
    """DNS-rebinding protection for an open (token-less) server bound to loopback.

    With a token configured the bearer check already defeats rebinding, and Host checks
    would break access through a reverse proxy (Caddy) or a tailnet name.
    """
    settings = get_settings()
    token = settings.api_token.get_secret_value() if settings.api_token else ""
    if not token and settings.host in ("127.0.0.1", "localhost", "::1"):
        return TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=["127.0.0.1", "127.0.0.1:*", "localhost", "localhost:*", "[::1]:*"],
            allowed_origins=["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"],
        )
    return TransportSecuritySettings(enable_dns_rebinding_protection=False)


def _lifespan(
    manager: StreamableHTTPSessionManager,
) -> Callable[[Starlette], AbstractAsyncContextManager[None]]:
    """Run the session manager in a dedicated task.

    ``manager.run()`` holds an anyio task group, which must be exited by the task that
    entered it. Host lifespans (and test fixtures) may enter and exit in different
    tasks, so the task group gets a task of its own.
    """

    @asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncIterator[None]:
        started, stop = asyncio.Event(), asyncio.Event()

        async def run() -> None:
            async with manager.run():
                started.set()
                await stop.wait()

        task = asyncio.create_task(run(), name="mcp-session-manager")
        ready = asyncio.create_task(started.wait())
        await asyncio.wait({task, ready}, return_when=asyncio.FIRST_COMPLETED)
        if task.done():  # failed to start: surface the error
            ready.cancel()
            await task
        try:
            yield
        finally:
            stop.set()
            await task

    return lifespan


def build_mcp_app(server: MCPServer | None = None) -> Starlette:
    """Stateless Streamable-HTTP ASGI app, answering on any path below its mount point.

    Add it as a plain route so the endpoint is exactly ``/mcp``::

        app.add_route("/mcp", build_mcp_app(), include_in_schema=False)

    (``app.mount("/mcp", ...)`` also works but serves ``/mcp/`` and 307-redirects ``/mcp``.)
    The returned app's lifespan runs the session manager and must be entered by the host.
    """
    server = server or build_server()
    server.streamable_http_app(
        stateless_http=True,
        json_response=True,
        transport_security=_transport_security(),
    )
    manager = server.session_manager
    endpoint = BearerAuth(StreamableHTTPASGIApp(manager))
    app = Starlette(routes=[Mount("", app=endpoint)], lifespan=_lifespan(manager))
    app.state.mcp_server = server
    return app
