"""MCP server: tool catalogue, structured output, errors, resources and HTTP auth.

Service functions are monkeypatched, so these tests need neither models nor data.
"""

from __future__ import annotations

import importlib
import sys
import types
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from pydantic import SecretStr

from kb.config import get_settings
from kb.mcp.server import build_mcp_app, build_server, plain_snippet
from kb.schemas import (
    AskResponse,
    Citation,
    Collection,
    Document,
    DocumentSummary,
    IngestAccepted,
    Page,
    SearchHit,
    SearchResponse,
)

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
DOC_ID = UUID("11111111-1111-4111-8111-111111111111")
COL_ID = uuid4()


def _doc(body: str = "# Vacuum\n\nTune autovacuum.") -> Document:
    return Document(
        id=DOC_ID,
        collection_id=COL_ID,
        collection="default",
        title="Postgres vacuum",
        source_uri="note:vacuum",
        mime_type="text/markdown",
        tags=["postgres"],
        status="ready",
        error=None,
        chunk_count=3,
        created_at=NOW,
        updated_at=NOW,
        body=body,
        metadata={},
    )


@pytest.fixture
def patch_service(monkeypatch: pytest.MonkeyPatch) -> Callable[[str, str, Any], None]:
    """Patch ``module.attr``, creating a stub module if the real one does not exist yet."""

    def patch(module: str, attr: str, value: Any) -> None:
        try:
            mod = importlib.import_module(module)
        except ImportError:
            mod = types.ModuleType(module)
            monkeypatch.setitem(sys.modules, module, mod)
        monkeypatch.setattr(mod, attr, value, raising=False)

    return patch


def mcp_client() -> Client:
    """In-process client. Used as ``async with`` inside each test: anyio task groups must be
    entered and exited in the same task, which async-generator fixtures do not guarantee."""
    return Client(build_server())


async def test_tool_catalogue() -> None:
    async with mcp_client() as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
        assert list(tools) == [
            "kb_search",
            "kb_ask",
            "kb_read",
            "kb_list",
            "kb_add",
            "kb_update",
            "kb_delete",
            "kb_collections",
        ]
        for tool in tools.values():
            assert tool.description and len(tool.description) < 400
            assert tool.output_schema is not None
            assert tool.annotations is not None
        assert tools["kb_search"].annotations.read_only_hint is True
        assert tools["kb_delete"].annotations.destructive_hint is True
        assert tools["kb_add"].annotations.destructive_hint is False
        search_schema = tools["kb_search"].input_schema
        assert search_schema["required"] == ["query"]
        assert search_schema["properties"]["mode"]["enum"] == ["hybrid", "lexical", "semantic"]
        assert "hits" in tools["kb_search"].output_schema["properties"]
        templates = (await client.list_resource_templates()).resource_templates
        assert [t.uri_template for t in templates] == ["kb://documents/{document_id}"]


async def test_search(patch_service: Callable[..., None]) -> None:
    async with mcp_client() as client:
        seen: dict[str, Any] = {}

        async def fake_search(req: Any) -> SearchResponse:
            seen["req"] = req
            hit = SearchHit(
                chunk_id=7,
                document_id=DOC_ID,
                document_title="Postgres vacuum",
                collection="default",
                source_uri=None,
                heading_path="Tuning > Autovacuum",
                content="raw",
                snippet="Tune <mark>autovacuum</mark> &amp; friends",
                score=0.876543,
                ord=1,
            )
            return SearchResponse(
                query=req.query, mode=req.mode, hits=[hit], timings=[], total_ms=3
            )

        patch_service("kb.search.pipeline", "search", fake_search)
        res = await client.call_tool(
            "kb_search", {"query": "vacuum", "limit": 3, "tags": ["postgres"], "mode": "lexical"}
        )
        assert not res.is_error
        req = seen["req"]
        assert (req.limit, req.mode, req.filters.tags, req.filters.collections) == (
            3,
            "lexical",
            ["postgres"],
            None,
        )
        hit = res.structured_content["hits"][0]
        assert hit == {
            "chunk_id": 7,
            "document_id": str(DOC_ID),
            "title": "Postgres vacuum",
            "heading_path": "Tuning > Autovacuum",
            "snippet": "Tune autovacuum & friends",
            "score": 0.8765,
            "collection": "default",
            "source_uri": None,
        }
        text = res.content[0].text
        assert "[1] Postgres vacuum › Tuning > Autovacuum" in text and "<mark>" not in text


async def test_search_empty_gives_hint(patch_service: Callable[..., None]) -> None:
    async with mcp_client() as client:

        async def fake_search(req: Any) -> SearchResponse:
            return SearchResponse(query=req.query, mode=req.mode, hits=[], timings=[], total_ms=1)

        patch_service("kb.search.pipeline", "search", fake_search)
        res = await client.call_tool("kb_search", {"query": "nothing"})
        assert res.structured_content["hits"] == []
        assert "broader" in res.structured_content["hint"]


async def test_ask(patch_service: Callable[..., None]) -> None:
    async with mcp_client() as client:

        async def fake_ask(req: Any) -> AskResponse:
            assert req.engine == "cloud"
            cit = Citation(
                n=1,
                chunk_id=7,
                document_id=DOC_ID,
                document_title="Postgres vacuum",
                heading_path="Tuning",
                snippet="<mark>Tune</mark> it",
            )
            return AskResponse(
                question=req.question,
                answer="Tune autovacuum [1].",
                citations=[cit],
                steps=[],
                engine="cloud",
                total_ms=10,
            )

        patch_service("kb.agent.loop", "ask", fake_ask)
        res = await client.call_tool("kb_ask", {"question": "how?", "engine": "cloud"})
        sc = res.structured_content
        assert sc["answer"] == "Tune autovacuum [1]."
        assert sc["sources"][0]["snippet"] == "Tune it"
        assert "Sources:\n[1] Postgres vacuum › Tuning" in res.content[0].text


async def test_read_whole_and_windowed(patch_service: Callable[..., None]) -> None:
    async with mcp_client() as client:

        async def get_document(doc_id: UUID) -> Document | None:
            return _doc() if doc_id == DOC_ID else None

        async def get_chunks(doc_id: UUID) -> list[dict]:
            return [
                {"id": i, "ord": i, "heading_path": "", "content": f"chunk {i}", "token_count": 2}
                for i in range(5)
            ]

        patch_service("kb.ingest.service", "get_document", get_document)
        patch_service("kb.ingest.service", "get_chunks", get_chunks)

        res = await client.call_tool("kb_read", {"document_id": str(DOC_ID)})
        assert res.structured_content["content"].startswith("# Vacuum")
        assert res.structured_content["truncated"] is False

        res = await client.call_tool(
            "kb_read", {"document_id": str(DOC_ID), "start_chunk": 1, "max_chunks": 2}
        )
        sc = res.structured_content
        assert sc["content"] == "chunk 1\n\nchunk 2"
        assert (sc["start_chunk"], sc["end_chunk"], sc["next_start_chunk"]) == (1, 3, 3)
        assert "start_chunk=3" in sc["hint"]

        res = await client.call_tool("kb_read", {"document_id": str(uuid4())})
        assert res.is_error and "not found" in res.content[0].text

        res = await client.call_tool("kb_read", {"document_id": "abc"})
        assert res.is_error and "not a valid UUID" in res.content[0].text


async def test_resource(patch_service: Callable[..., None]) -> None:
    async with mcp_client() as client:

        async def get_document(doc_id: UUID) -> Document | None:
            return _doc() if doc_id == DOC_ID else None

        patch_service("kb.ingest.service", "get_document", get_document)
        res = await client.read_resource(f"kb://documents/{DOC_ID}")
        assert res.contents[0].text.startswith("# Vacuum")


async def test_list_add_update_delete_collections(patch_service: Callable[..., None]) -> None:
    async with mcp_client() as client:
        summary = DocumentSummary(**_doc().model_dump(exclude={"body", "metadata"}))

        async def list_documents(**kw: Any) -> Page[DocumentSummary]:
            assert kw["tag"] == "postgres" and kw["limit"] == 1
            return Page[DocumentSummary](items=[summary], total=2, limit=1, offset=0)

        created: dict[str, Any] = {}

        async def create_document(doc: Any) -> IngestAccepted:
            created["doc"] = doc
            return IngestAccepted(id=DOC_ID, status="pending")

        async def update_document(doc_id: UUID, patch: Any) -> Document | None:
            return _doc().model_copy(update={"title": patch.title}) if doc_id == DOC_ID else None

        async def delete_document(doc_id: UUID) -> bool:
            return doc_id == DOC_ID

        async def list_collections() -> list[Collection]:
            return [
                Collection(
                    id=COL_ID,
                    slug="default",
                    name="Default",
                    description="",
                    created_at=NOW,
                    document_count=2,
                )
            ]

        for name, fn in {
            "list_documents": list_documents,
            "create_document": create_document,
            "update_document": update_document,
            "delete_document": delete_document,
            "list_collections": list_collections,
        }.items():
            patch_service("kb.ingest.service", name, fn)

        res = await client.call_tool("kb_list", {"tag": "postgres", "limit": 1})
        assert res.structured_content["next_offset"] == 1
        assert res.structured_content["items"][0]["id"] == str(DOC_ID)

        res = await client.call_tool(
            "kb_add", {"content": "hello", "tags": ["x"], "source_uri": "note:hello"}
        )
        assert res.structured_content == {
            "hint": None,
            "id": str(DOC_ID),
            "status": "pending",
            "duplicate": False,
        }
        assert created["doc"].source_uri == "note:hello" and created["doc"].collection == "default"

        res = await client.call_tool("kb_update", {"document_id": str(DOC_ID), "title": "New"})
        assert res.structured_content["title"] == "New"
        res = await client.call_tool("kb_update", {"document_id": str(DOC_ID)})
        assert res.is_error and "Nothing to update" in res.content[0].text

        res = await client.call_tool("kb_delete", {"document_id": str(DOC_ID)})
        assert res.structured_content["deleted"] is True
        res = await client.call_tool("kb_delete", {"document_id": str(uuid4())})
        assert res.is_error

        res = await client.call_tool("kb_collections", {})
        assert res.structured_content["collections"][0]["slug"] == "default"
        assert res.content[0].text == "- default: Default (2 docs)"


def test_plain_snippet() -> None:
    assert plain_snippet("a <mark>b</mark>\n &lt;c&gt;") == "a b <c>"
    assert plain_snippet("x" * 400, 10) == "x" * 9 + "…"


# --- HTTP transport --------------------------------------------------------------------


async def _http_tools(app: Any, headers: dict[str, str] | None = None) -> list[str]:
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://127.0.0.1", headers=headers
    ) as http:
        transport = streamable_http_client("http://127.0.0.1/mcp", http_client=http)
        async with Client(transport) as client:
            return [t.name for t in (await client.list_tools()).tools]


async def test_http_open_when_no_token() -> None:
    http_app = build_mcp_app()
    async with http_app.router.lifespan_context(http_app):
        assert "kb_search" in await _http_tools(http_app)


async def test_http_requires_bearer_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "api_token", SecretStr("s3cret"))
    http_app = build_mcp_app()
    async with http_app.router.lifespan_context(http_app):
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=http_app), base_url="http://127.0.0.1"
        ) as http:
            body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
            r = await http.post("/mcp", json=body)
            assert r.status_code == 401
            assert r.headers["www-authenticate"].startswith("Bearer")
            r = await http.post("/mcp", json=body, headers={"Authorization": "Bearer wrong"})
            assert r.status_code == 401
        tools = await _http_tools(http_app, {"Authorization": "Bearer s3cret"})
        assert len(tools) == 8


async def test_mounted_in_api_app() -> None:
    """Exact ``/mcp`` must reach the MCP endpoint when hosted inside a parent app."""
    from fastapi import FastAPI

    sub = build_mcp_app()
    parent = FastAPI()
    parent.add_route("/mcp", sub, include_in_schema=False)
    async with sub.router.lifespan_context(sub):
        assert "kb_ask" in await _http_tools(parent)


# --- end to end through the API app and the real services ------------------------------


async def test_end_to_end_through_api_app(app: Any) -> None:
    """kb_add -> ingest -> kb_search -> kb_read -> kb_delete over HTTP at /mcp."""
    from kb.ingest.worker import drain_jobs

    async with (
        httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as http,
        Client(streamable_http_client("http://127.0.0.1/mcp", http_client=http)) as client,
    ):
        body = "# Zebrafish husbandry\n\nZebrafish tanks need 28 degree water and daily feeding."
        res = await client.call_tool(
            "kb_add",
            {"content": body, "tags": ["biology"], "source_uri": "note:mcp-e2e-zebrafish"},
        )
        assert not res.is_error, res.content
        doc_id = res.structured_content["id"]
        await drain_jobs()

        res = await client.call_tool("kb_search", {"query": "zebrafish tanks", "mode": "lexical"})
        assert not res.is_error, res.content
        assert doc_id in {h["document_id"] for h in res.structured_content["hits"]}

        res = await client.call_tool("kb_read", {"document_id": doc_id})
        assert "28 degree water" in res.structured_content["content"]

        res = await client.call_tool("kb_delete", {"document_id": doc_id})
        assert res.structured_content["deleted"] is True
