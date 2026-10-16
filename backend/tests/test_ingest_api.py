import asyncio
import json
import uuid

import pytest

from kb.config import get_settings
from kb.db import connection
from kb.ingest import pipeline, queue, service
from kb.ingest.worker import drain_jobs
from kb.providers import Embedder, ProviderError


def _md(title: str, extra: str = "") -> str:
    return (
        f"# {title}\n\nThis note explains {title.lower()} in detail. {extra}\n\n"
        "## Setup\n\nInstall the package and configure the service.\n\n"
        "## Usage\n\n```bash\nkb search 'hello'\n```\n\nThat is all.\n"
    )


async def _create(client, **body) -> dict:
    resp = await client.post("/api/documents", json=body)
    assert resp.status_code == 202, resp.text
    return resp.json()


async def _doc(client, doc_id: str) -> dict:
    resp = await client.get(f"/api/documents/{doc_id}")
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _jobs_for(doc_id: str, kind: str | None = None) -> list[dict]:
    async with connection() as conn:
        cur = await conn.execute(
            """
            select * from jobs where payload->>'document_id' = %s
              and (%s::text is null or kind = %s) order by id
            """,
            (doc_id, kind, kind),
        )
        return await cur.fetchall()


async def test_create_ingest_ready_with_embeddings(client):
    tag = uuid.uuid4().hex[:6]
    accepted = await _create(client, content=_md(f"Alpha {tag}"), tags=["guide", tag])
    assert accepted["status"] == "pending" and accepted["duplicate"] is False
    await drain_jobs()
    doc = await _doc(client, accepted["id"])
    assert doc["status"] == "ready" and doc["error"] is None
    assert doc["title"] == f"Alpha {tag}" and doc["collection"] == "default"
    assert doc["chunk_count"] >= 2 and doc["tags"] == ["guide", tag]
    chunks = (await client.get(f"/api/documents/{accepted['id']}/chunks")).json()
    assert len(chunks) == doc["chunk_count"]
    assert all(c["embedded"] for c in chunks)
    assert [c["ord"] for c in chunks] == list(range(len(chunks)))
    assert chunks[1]["heading_path"] == f"Alpha {tag} > Setup"
    assert chunks[1]["context"] == f"Alpha {tag} — Alpha {tag} > Setup"
    body = doc["body"]
    for c in chunks:
        assert body[c["char_start"] : c["char_end"]] == c["content"]


async def test_duplicate_content_is_deduplicated(client):
    content = _md(f"Dup {uuid.uuid4().hex}")
    first = await _create(client, content=content)
    second = await _create(client, content=content + "\n\n\n")  # normalises identically
    assert second["id"] == first["id"] and second["duplicate"] is True
    # Same content in another collection is a different document.
    slug = f"c-{uuid.uuid4().hex[:8]}"
    assert (
        await client.post("/api/collections", json={"slug": slug, "name": "X"})
    ).status_code == 201
    third = await _create(client, content=content, collection=slug)
    assert third["id"] != first["id"] and third["duplicate"] is False


async def test_source_uri_upsert_replaces_content(client):
    uri = f"notes/{uuid.uuid4().hex}.md"
    first = await _create(client, content=_md("Original", "banana"), source_uri=uri)
    await drain_jobs()
    second = await _create(client, content=_md("Rewritten", "cherry"), source_uri=uri, tags=["v2"])
    assert second["id"] == first["id"] and second["duplicate"] is False
    await drain_jobs()
    doc = await _doc(client, first["id"])
    assert doc["status"] == "ready" and "cherry" in doc["body"] and "banana" not in doc["body"]
    assert doc["title"] == "Rewritten" and doc["tags"] == ["v2"]
    chunks = (await client.get(f"/api/documents/{first['id']}/chunks")).json()
    assert any("cherry" in c["content"] for c in chunks)
    assert not any("banana" in c["content"] for c in chunks)
    # Same content again via the same URI: a no-op duplicate.
    third = await _create(client, content=_md("Rewritten", "cherry"), source_uri=uri)
    assert third == {"id": first["id"], "status": "ready", "duplicate": True}


async def test_upload_markdown_file(client):
    name = f"upload-{uuid.uuid4().hex[:6]}"
    resp = await client.post(
        "/api/documents/upload",
        files=[
            ("files", (f"{name}.md", b"Just text, no heading.\n\nSecond para.", "text/markdown")),
            ("files", (f"{name}.html", b"<h1>Html Doc</h1><p>Body here.</p>", "text/html")),
        ],
        data={"collection": "default", "tags": "inbox, upload ,"},
    )
    assert resp.status_code == 202, resp.text
    items = resp.json()
    assert len(items) == 2 and all(i["status"] == "pending" for i in items)
    await drain_jobs()
    md_doc = await _doc(client, items[0]["id"])
    html_doc = await _doc(client, items[1]["id"])
    assert md_doc["status"] == "ready" and md_doc["title"] == name
    assert md_doc["tags"] == ["inbox", "upload"]
    assert md_doc["metadata"]["filename"] == f"{name}.md"
    assert html_doc["title"] == "Html Doc" and html_doc["body"].startswith("# Html Doc")


async def test_upload_unsupported_and_oversized(client, monkeypatch):
    try:
        import markitdown  # noqa: F401
    except ImportError:
        resp = await client.post(
            "/api/documents/upload",
            files=[("files", (f"{uuid.uuid4().hex}.pdf", b"%PDF-1.7 fake", "application/pdf"))],
        )
        assert resp.status_code == 202
        doc = await _doc(client, resp.json()[0]["id"])
        assert doc["status"] == "failed" and "parsing" in doc["error"]
    monkeypatch.setattr(get_settings(), "max_upload_mb", 0)
    resp = await client.post(
        "/api/documents/upload", files=[("files", ("big.md", b"x" * 10, "text/markdown"))]
    )
    assert resp.status_code == 413


async def test_unknown_collection_and_validation(client):
    resp = await client.post("/api/documents", json={"content": "x", "collection": "nope-nope"})
    assert resp.status_code == 422
    resp = await client.post("/api/documents", json={"content": ""})
    assert resp.status_code == 422
    resp = await client.post("/api/documents", json={"content": "  \n  "})
    assert resp.status_code == 422
    assert (
        await client.get("/api/documents", params={"collection": "nope-nope"})
    ).status_code == 404
    assert (await client.get(f"/api/documents/{uuid.uuid4()}")).status_code == 404


async def test_patch_metadata_does_not_reembed_but_content_does(client):
    accepted = await _create(client, content=_md(f"Patch {uuid.uuid4().hex}"))
    doc_id = accepted["id"]
    await drain_jobs()
    before = (await client.get(f"/api/documents/{doc_id}/chunks")).json()
    jobs_before = len(await _jobs_for(doc_id))

    resp = await client.patch(
        f"/api/documents/{doc_id}", json={"title": "Renamed", "tags": ["x"], "metadata": {"k": 1}}
    )
    assert resp.status_code == 200
    doc = resp.json()
    assert doc["title"] == "Renamed" and doc["tags"] == ["x"] and doc["metadata"] == {"k": 1}
    assert doc["status"] == "ready"
    assert len(await _jobs_for(doc_id)) == jobs_before
    assert (await client.get(f"/api/documents/{doc_id}/chunks")).json() == before

    resp = await client.patch(f"/api/documents/{doc_id}", json={"content": "# New\n\nfresh words"})
    assert resp.json()["status"] == "pending"
    await drain_jobs()
    doc = await _doc(client, doc_id)
    assert doc["status"] == "ready" and doc["chunk_count"] == 1
    chunks = (await client.get(f"/api/documents/{doc_id}/chunks")).json()
    assert chunks[0]["content"] == "# New\n\nfresh words"


async def test_move_between_collections_updates_chunks(client):
    slug = f"m-{uuid.uuid4().hex[:8]}"
    await client.post("/api/collections", json={"slug": slug, "name": "Moved"})
    accepted = await _create(client, content=_md(f"Move {uuid.uuid4().hex}"))
    await drain_jobs()
    resp = await client.patch(f"/api/documents/{accepted['id']}", json={"collection": slug})
    assert resp.status_code == 200 and resp.json()["collection"] == slug
    async with connection() as conn:
        cur = await conn.execute(
            """
            select count(*) as n from chunks ch join collections c on c.id = ch.collection_id
            where ch.document_id = %s and c.slug = %s
            """,
            (accepted["id"], slug),
        )
        assert (await cur.fetchone())["n"] == resp.json()["chunk_count"]


async def test_delete_cascades(client):
    accepted = await _create(client, content=_md(f"Delete {uuid.uuid4().hex}"))
    await drain_jobs()
    assert (await client.delete(f"/api/documents/{accepted['id']}")).status_code == 204
    assert (await client.get(f"/api/documents/{accepted['id']}")).status_code == 404
    assert (await client.get(f"/api/documents/{accepted['id']}/chunks")).status_code == 404
    assert (await client.delete(f"/api/documents/{accepted['id']}")).status_code == 404
    async with connection() as conn:
        cur = await conn.execute(
            "select count(*) as n from chunks where document_id = %s", (accepted["id"],)
        )
        assert (await cur.fetchone())["n"] == 0


async def test_delete_before_ingest_is_harmless(client):
    accepted = await _create(client, content=_md(f"Gone {uuid.uuid4().hex}"))
    assert await pipeline.ingest_document(uuid.uuid4()) == "missing"
    assert (await client.delete(f"/api/documents/{accepted['id']}")).status_code == 204
    assert await _jobs_for(accepted["id"], queue.INGEST_DOCUMENT) == []
    await drain_jobs()


async def test_collections_crud(client):
    slug = f"col-{uuid.uuid4().hex[:8]}"
    resp = await client.post("/api/collections", json={"slug": slug, "name": "Research"})
    assert resp.status_code == 201 and resp.json()["document_count"] == 0
    assert (
        await client.post("/api/collections", json={"slug": slug, "name": "R"})
    ).status_code == 409
    assert (
        await client.post("/api/collections", json={"slug": "Bad Slug", "name": "R"})
    ).status_code == 422
    doc = await _create(client, content=_md("In collection"), collection=slug)
    await drain_jobs()
    cols = {c["slug"]: c for c in (await client.get("/api/collections")).json()}
    assert cols[slug]["document_count"] == 1 and "default" in cols
    listed = (await client.get("/api/documents", params={"collection": slug})).json()
    assert listed["total"] == 1 and listed["items"][0]["id"] == doc["id"]
    assert (await client.delete("/api/collections/default")).status_code == 409
    assert (await client.delete(f"/api/collections/{slug}")).status_code == 204
    assert (await client.get(f"/api/documents/{doc['id']}")).status_code == 404
    assert (await client.delete(f"/api/collections/{slug}")).status_code == 404


async def test_list_filters(client):
    tag = f"t{uuid.uuid4().hex[:8]}"
    for i in range(3):
        await _create(client, content=_md(f"Filterable {tag} {i}"), tags=[tag])
    page = (await client.get("/api/documents", params={"tag": tag, "limit": 2})).json()
    assert page["total"] == 3 and len(page["items"]) == 2 and page["limit"] == 2
    page = (await client.get("/api/documents", params={"q": f"{tag} 1"})).json()
    assert page["total"] == 1
    page = (await client.get("/api/documents", params={"q": "100%_"})).json()
    assert page["total"] == 0
    page = (await client.get("/api/documents", params={"tag": tag, "status": "pending"})).json()
    assert page["total"] == 3
    await drain_jobs()
    page = (await client.get("/api/documents", params={"tag": tag, "status": "ready"})).json()
    assert page["total"] == 3


async def test_reindex(client):
    accepted = await _create(client, content=_md(f"Reindex {uuid.uuid4().hex}"))
    await drain_jobs()
    old_ids = [
        c["id"] for c in (await client.get(f"/api/documents/{accepted['id']}/chunks")).json()
    ]
    resp = await client.post(f"/api/documents/{accepted['id']}/reindex")
    assert resp.status_code == 202 and resp.json()["status"] == "pending"
    await drain_jobs()
    new = (await client.get(f"/api/documents/{accepted['id']}/chunks")).json()
    assert len(new) == len(old_ids) and set(c["id"] for c in new).isdisjoint(old_ids)
    assert (await client.post(f"/api/documents/{uuid.uuid4()}/reindex")).status_code == 404


class _DownEmbedder(Embedder):
    name = "embedder:down"
    dim = 256

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        raise ProviderError("embedder:down: connection refused")

    async def embed_query(self, text: str) -> list[float]:
        raise ProviderError("embedder:down: connection refused")


async def test_embedder_down_degrades_and_reembeds_later(client, monkeypatch):
    monkeypatch.setattr(pipeline, "get_embedder", lambda: _DownEmbedder())
    accepted = await _create(client, content=_md(f"Degraded {uuid.uuid4().hex}"))
    await drain_jobs()
    doc = await _doc(client, accepted["id"])
    assert doc["status"] == "ready" and doc["chunk_count"] >= 2
    chunks = (await client.get(f"/api/documents/{accepted['id']}/chunks")).json()
    assert chunks and not any(c["embedded"] for c in chunks)
    jobs = await _jobs_for(accepted["id"], queue.REEMBED_DOCUMENT)
    assert len(jobs) == 1 and jobs[0]["status"] == "queued"
    # Lexical search still works on the unembedded chunks.
    async with connection() as conn:
        cur = await conn.execute(
            "select count(*) as n from chunks where document_id = %s and content ||| 'configure'",
            (accepted["id"],),
        )
        assert (await cur.fetchone())["n"] >= 1

    # Re-embedding retries while the embedder is still down ...
    async with connection() as conn:
        await conn.execute("update jobs set run_after = now() where id = %s", (jobs[0]["id"],))
    await drain_jobs()
    job = (await _jobs_for(accepted["id"], queue.REEMBED_DOCUMENT))[0]
    assert (
        job["status"] == "queued"
        and job["attempts"] == 1
        and "connection refused" in job["last_error"]
    )

    # ... and succeeds once it is back.
    monkeypatch.undo()
    async with connection() as conn:
        await conn.execute("update jobs set run_after = now() where id = %s", (jobs[0]["id"],))
    await drain_jobs()
    chunks = (await client.get(f"/api/documents/{accepted['id']}/chunks")).json()
    assert all(c["embedded"] for c in chunks)
    assert (await _jobs_for(accepted["id"], queue.REEMBED_DOCUMENT))[0]["status"] == "done"


async def test_missing_embedding_sweeper(client):
    accepted = await _create(client, content=_md(f"Sweep {uuid.uuid4().hex}"))
    await drain_jobs()
    async with connection() as conn:
        await conn.execute(
            "update chunks set embedding = null where document_id = %s", (accepted["id"],)
        )
    assert await pipeline.enqueue_missing_embeddings() >= 1
    await drain_jobs()
    chunks = (await client.get(f"/api/documents/{accepted['id']}/chunks")).json()
    assert all(c["embedded"] for c in chunks)


async def test_contextual_chunks_with_llm_and_fallback(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "contextual_chunks", True)
    accepted = await _create(client, content=_md(f"Contextual {uuid.uuid4().hex}"))
    await drain_jobs()
    chunks = (await client.get(f"/api/documents/{accepted['id']}/chunks")).json()
    assert chunks and all(c["context"].startswith("Based on the knowledge base") for c in chunks)

    class _DownLLM:
        async def complete(self, *args, **kwargs):
            raise ProviderError("llm down")

    from kb.ingest import contextual

    monkeypatch.setattr(contextual, "get_llm", lambda kind="local": _DownLLM())
    title = f"Fallback {uuid.uuid4().hex}"
    accepted = await _create(client, content=_md(title))
    await drain_jobs()
    chunks = (await client.get(f"/api/documents/{accepted['id']}/chunks")).json()
    assert chunks[0]["context"] == title
    assert chunks[1]["context"] == f"{title} — {title} > Setup"
    assert (await _doc(client, accepted["id"]))["status"] == "ready"


async def test_same_document_is_never_ingested_concurrently(client):
    accepted = await _create(client, content=_md(f"Lock {uuid.uuid4().hex}"))
    doc_id = uuid.UUID(accepted["id"])
    async with pipeline.document_lock(doc_id):
        with pytest.raises(pipeline.DocumentBusy):
            await pipeline.ingest_document(doc_id)
    results = await asyncio.gather(
        pipeline.ingest_document(doc_id), pipeline.ingest_document(doc_id), return_exceptions=True
    )
    assert "ready" in results
    await drain_jobs()
    assert (await _doc(client, accepted["id"]))["status"] == "ready"


async def test_stale_run_does_not_overwrite_newer_content(client, monkeypatch):
    accepted = await _create(client, content=_md(f"Stale {uuid.uuid4().hex}", "old words"))
    doc_id = uuid.UUID(accepted["id"])
    real = pipeline.build_contexts

    async def slow_contexts(title, body, chunks):
        # The document changes while this (old) run is busy with model calls.
        from kb.schemas import DocumentPatch

        await service.update_document(doc_id, DocumentPatch(content="# Newer\n\nnew words"))
        return await real(title, body, chunks)

    monkeypatch.setattr(pipeline, "build_contexts", slow_contexts)
    assert await pipeline.ingest_document(doc_id) == "stale"
    monkeypatch.undo()
    await drain_jobs()
    chunks = (await client.get(f"/api/documents/{doc_id}/chunks")).json()
    assert [c["content"] for c in chunks] == ["# Newer\n\nnew words"]


async def test_fifty_concurrent_creations(client):
    run = uuid.uuid4().hex[:8]
    tag = f"bulk{run}"
    results = await asyncio.gather(
        *(
            client.post(
                "/api/documents",
                json={"content": _md(f"Bulk {run} {i}", f"unique {i}"), "tags": [tag]},
            )
            for i in range(50)
        )
    )
    assert all(r.status_code == 202 for r in results), [
        r.text for r in results if r.status_code != 202
    ]
    ids = {r.json()["id"] for r in results}
    assert len(ids) == 50
    # Concurrent identical creations converge on one document.
    same = await asyncio.gather(
        *(client.post("/api/documents", json={"content": _md(f"Same {run}")}) for _ in range(10))
    )
    assert len({r.json()["id"] for r in same}) == 1
    assert sum(not r.json()["duplicate"] for r in same) == 1
    await drain_jobs()
    page = (await client.get("/api/documents", params={"tag": tag, "limit": 100})).json()
    assert page["total"] == 50 and all(d["status"] == "ready" for d in page["items"])


async def test_health_stats_and_recent_queries(client):
    health = (await client.get("/api/health", params={"fresh": True})).json()
    names = {c["name"]: c for c in health["components"]}
    assert set(names) == {"db", "embedder", "reranker", "llm"}
    assert health["status"] == "ok" and all(c["ok"] for c in names.values())
    stats = (await client.get("/api/stats")).json()
    assert stats["documents"] >= 1 and stats["collections"] >= 1
    async with connection() as conn:
        await conn.execute(
            "insert into query_log (query, mode, latency_ms) values (%s, 'hybrid', 5), (%s, 'lexical', 7)",
            ("first query", "second query"),
        )
    recent = (await client.get("/api/queries/recent", params={"limit": 2})).json()
    assert [r["query"] for r in recent] == ["second query", "first query"]


async def test_health_reports_degraded_embedder(client, monkeypatch):
    from kb.api import system

    monkeypatch.setattr(system, "get_embedder", lambda: _DownEmbedder())
    health = (await client.get("/api/health", params={"fresh": True})).json()
    assert health["status"] == "degraded"
    emb = next(c for c in health["components"] if c["name"] == "embedder")
    assert emb["ok"] is False and "connection refused" in emb["detail"]


async def test_events_stream_yields_document_status(app):
    from kb.api.system import events
    from kb.events import broker

    response = await events()
    iterator = response.body_iterator.__aiter__()
    first = asyncio.ensure_future(iterator.__anext__())
    await asyncio.sleep(0.05)
    broker.publish_local({"type": "document.status", "id": "abc", "status": "ready"})
    item = await asyncio.wait_for(first, 2)
    assert item["event"] == "document.status"
    assert json.loads(item["data"])["status"] == "ready"
    await iterator.aclose()


async def test_write_endpoints_require_token_when_configured(client, monkeypatch):
    from pydantic import SecretStr

    monkeypatch.setattr(get_settings(), "api_token", SecretStr("s3cret"))
    assert (await client.post("/api/documents", json={"content": "x"})).status_code == 401
    assert (
        await client.post("/api/collections", json={"slug": "zz", "name": "Z"})
    ).status_code == 401
    assert (await client.get("/api/documents")).status_code == 200
    ok = await client.post(
        "/api/documents",
        json={"content": f"token {uuid.uuid4().hex}"},
        headers={"Authorization": "Bearer s3cret"},
    )
    assert ok.status_code == 202
