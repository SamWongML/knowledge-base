"""Regression tests for bugs found by the end-to-end and stress runs (backend hardening)."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import anyio
import psycopg
import pytest
from httpx import ASGITransport, AsyncClient
from psycopg_pool import PoolTimeout

from kb.api.sse import until_shutdown
from kb.config import get_settings
from kb.db import connection
from kb.ingest import pipeline, queue, service
from kb.ingest.worker import Worker, drain_jobs
from kb.schemas import AskRequest, DocumentIn, DocumentPatch, SearchRequest
from kb.search.cache import SearchCache


async def _ready_doc(content: str, collection: str = "default") -> uuid.UUID:
    accepted = await service.create_document(DocumentIn(content=content, collection=collection))
    await drain_jobs()
    return accepted.id


async def _listen(conn: psycopg.AsyncConnection[Any]) -> AsyncIterator[dict[str, Any]]:
    async for note in conn.notifies(timeout=5):
        yield json.loads(note.payload)


async def _events_during(action: Any) -> list[dict[str, Any]]:
    """Run ``action`` and return every kb_events payload it produced."""
    async with await psycopg.AsyncConnection.connect(
        get_settings().database_url, autocommit=True
    ) as conn:
        await conn.execute("listen kb_events")
        await action()
        await conn.execute("select pg_notify('kb_events', '{\"type\": \"test.end\"}')")
        events: list[dict[str, Any]] = []
        async for event in _listen(conn):
            if event["type"] == "test.end":
                break
            events.append(event)
        return events


# --- document events (migration 0002) -------------------------------------------------


async def test_delete_and_metadata_edits_emit_events(app: Any) -> None:
    doc_id = await _ready_doc(f"# Events {uuid.uuid4().hex}\n\nbody text here")

    async def rename() -> None:
        await service.update_document(doc_id, DocumentPatch(title="Renamed", tags=["x"]))

    events = await _events_during(rename)
    assert [e["type"] for e in events] == ["document.updated"]
    assert events[0]["title"] == "Renamed"

    async def delete() -> None:
        assert await service.delete_document(doc_id)

    events = await _events_during(delete)
    assert events == [
        {"type": "document.deleted", "id": str(doc_id), "collection_id": events[0]["collection_id"]}
    ]


async def test_long_error_does_not_break_the_status_update(app: Any) -> None:
    """pg_notify rejects payloads over 8000 bytes, which used to fail the UPDATE itself."""
    doc_id = await _ready_doc(f"# Long error {uuid.uuid4().hex}\n\nbody")

    async def fail() -> None:
        # 2000 four-byte characters: the stored error (and a 500-char title) blow the limit.
        await service.update_document(doc_id, DocumentPatch(title="ü" * 500))
        await pipeline.mark_failed(doc_id, "💥" * 5000)

    events = await _events_during(fail)
    assert events[-1]["status"] == "failed"
    assert len(events[-1]["error"]) == 1000 and len(events[-1]["title"]) == 200
    doc = await service.get_document(doc_id)
    assert doc is not None and doc.status == "failed" and len(doc.error or "") == 2000


# --- caches ---------------------------------------------------------------------------


def test_search_cache_discards_results_computed_across_an_invalidation() -> None:
    from kb.schemas import SearchResponse

    cache = SearchCache(ttl_s=30)
    resp = SearchResponse(query="q", mode="hybrid", hits=[], timings=[], total_ms=1.0)
    version = cache.version
    cache.bump()  # a document changed while the search was running
    cache.put("k", resp, version)
    assert cache.get("k") is None
    cache.put("k", resp, cache.version)
    assert cache.get("k") is not None


async def test_deleted_document_is_not_served_from_the_search_cache(
    app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from kb.events import broker
    from kb.search import pipeline as search_pipeline

    monkeypatch.setattr(search_pipeline.cache, "ttl_s", 30.0)
    word = f"zq{uuid.uuid4().hex[:10]}"
    doc_id = await _ready_doc(f"# Cached\n\n{word} appears here")
    req = SearchRequest(query=word, mode="lexical")
    assert [h.document_id for h in (await search_pipeline.search(req)).hits] == [doc_id]
    assert (await search_pipeline.search(req)).timings[0].stage == "cache"

    await service.delete_document(doc_id)
    # The event travels Postgres NOTIFY -> broker; until then the TTL alone would serve it.
    broker.publish_local({"type": "document.deleted", "id": str(doc_id)})
    assert (await search_pipeline.search(req)).hits == []


async def test_answer_cache_is_invalidated_by_document_events(app: Any) -> None:
    from kb.agent import loop
    from kb.events import broker

    loop.clear_cache()
    req = AskRequest(question=f"what about {uuid.uuid4().hex}?")
    first = await loop.ask(req)
    assert (await loop.ask(req)).steps == first.steps
    assert loop._cache  # cached
    broker.publish_local({"type": "document.deleted", "id": str(uuid.uuid4())})
    assert not loop._cache


# --- ingestion ------------------------------------------------------------------------


async def test_document_moved_during_ingest_gets_chunks_in_new_collection(
    app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from kb.schemas import CollectionIn

    slug = f"mv-{uuid.uuid4().hex[:8]}"
    await service.create_collection(CollectionIn(slug=slug, name="Move target"))
    accepted = await service.create_document(
        DocumentIn(content=f"# Mover {uuid.uuid4().hex}\n\nwords to index")
    )
    real = pipeline.build_contexts

    async def move_meanwhile(title: str, body: str, chunks: Any) -> list[str]:
        await service.update_document(accepted.id, DocumentPatch(collection=slug))
        return await real(title, body, chunks)

    monkeypatch.setattr(pipeline, "build_contexts", move_meanwhile)
    assert await pipeline.ingest_document(accepted.id) == "ready"
    async with connection() as conn:
        cur = await conn.execute(
            """
            select count(*) filter (where c.collection_id <> d.collection_id) as wrong
            from chunks c join documents d on d.id = c.document_id where d.id = %s
            """,
            (accepted.id,),
        )
        row = await cur.fetchone()
    assert row is not None and row["wrong"] == 0


async def test_worker_consumer_survives_bookkeeping_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failure while recording a job's outcome used to kill the consumer task (and with
    it, after the grace period, the whole worker)."""
    from kb.ingest import worker as worker_mod

    jobs = [queue.Job(1, "noop", {}, 1, 5, "w"), queue.Job(2, "noop", {}, 1, 5, "w")]
    processed: list[int] = []

    async def fake_claim(_conn: Any, _worker_id: str) -> queue.Job | None:
        return jobs.pop(0) if jobs else None

    async def flaky_process(job: queue.Job, *, heartbeat: bool = True) -> str:
        processed.append(job.id)
        if job.id == 1:
            raise psycopg.OperationalError("connection lost")
        stop.set()
        return "ready"

    stop = asyncio.Event()
    monkeypatch.setattr(worker_mod.queue, "claim", fake_claim)
    monkeypatch.setattr(worker_mod, "process_job", flaky_process)
    monkeypatch.setattr(worker_mod, "BUSY_RETRY_S", 0.0)
    await asyncio.wait_for(Worker(1)._consume(stop), 5)
    assert processed == [1, 2]


# --- HTTP layer -----------------------------------------------------------------------


async def test_body_size_limit_rejects_streamed_and_declared_bodies(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "max_upload_mb", 1)
    monkeypatch.setattr(get_settings(), "max_request_mb", 1)
    big = b"x" * (1 << 20) + b"y"

    resp = await client.post(
        "/api/documents",
        content=json.dumps({"content": big.decode()}),
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 413

    async def chunked() -> AsyncIterator[bytes]:  # no Content-Length: counted as it streams
        for _ in range(4):
            yield b"z" * (1 << 19)

    resp = await client.post(
        "/api/documents", content=chunked(), headers={"content-type": "application/json"}
    )
    assert resp.status_code == 413
    resp = await client.post(
        "/api/documents/upload",
        files=[("files", ("a.md", b"# a\n\nsmall", "text/markdown"))],
    )
    assert resp.status_code == 202


async def test_database_overload_is_a_503(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def busy() -> None:
        raise PoolTimeout("couldn't get a connection after 30.00 sec")

    monkeypatch.setattr(service, "list_collections", busy)
    resp = await client.get("/api/collections")
    assert resp.status_code == 503
    assert resp.headers["retry-after"] == "2" and "busy" in resp.json()["detail"]


async def test_spa_fallback_never_answers_api_paths_with_html(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<!doctype html><title>kb</title>")
    monkeypatch.setattr(get_settings(), "static_dir", str(tmp_path))
    from kb.api.app import create_app

    spa_app = create_app(run_migrations=False)
    async with AsyncClient(transport=ASGITransport(app=spa_app), base_url="http://t") as c:
        assert (await c.get("/library/anything")).text.startswith("<!doctype html>")
        assert (await c.get("/api/no-such-endpoint")).status_code == 404
        assert (await c.get("/api")).status_code == 404


# --- SSE ------------------------------------------------------------------------------


async def test_until_shutdown_ends_stream_and_cancels_the_pending_read() -> None:
    closed = asyncio.Event()

    async def endless() -> AsyncIterator[int]:
        try:
            yield 1
            await asyncio.Event().wait()  # blocks like queue.get() on an idle stream
            yield 2
        finally:
            closed.set()

    shutdown = anyio.Event()
    got: list[int] = []

    async def consume() -> None:
        async for item in until_shutdown(endless(), shutdown):
            got.append(item)

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.05)
    shutdown.set()
    await asyncio.wait_for(task, 2)  # returns normally: the response can be completed
    assert got == [1] and closed.is_set()


async def test_until_shutdown_client_disconnect_does_not_leak_the_reader() -> None:
    closed = asyncio.Event()

    async def endless() -> AsyncIterator[int]:
        try:
            await asyncio.Event().wait()
            yield 0
        finally:
            closed.set()

    async def consume() -> None:
        async for _ in until_shutdown(endless(), anyio.Event()):
            pass

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.05)
    task.cancel()  # what sse-starlette does when the client goes away
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed.is_set()


async def test_token_protects_reads_but_not_health(client, monkeypatch):
    from pydantic import SecretStr

    from kb.config import get_settings

    monkeypatch.setattr(get_settings(), "api_token", SecretStr("s3cret"))
    assert (await client.get("/api/health")).status_code == 200
    assert (await client.get("/api/stats")).status_code == 401
    assert (await client.post("/api/search", json={"query": "x"})).status_code == 401
    ok = await client.get("/api/stats", headers={"Authorization": "Bearer s3cret"})
    assert ok.status_code == 200
