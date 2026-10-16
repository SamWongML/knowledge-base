"""End-to-end tests of the hybrid search pipeline against a real ParadeDB database."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from kb.providers.base import LLM, Embedder, ProviderError, Reranker
from kb.providers.fake import FakeEmbedder
from kb.schemas import SearchFilters, SearchRequest

SUFFIX = uuid.uuid4().hex[:6]
COL_A = f"srch-a-{SUFFIX}"
COL_B = f"srch-b-{SUFFIX}"

CORPUS: dict[str, dict[str, Any]] = {
    "pg": {
        "col": COL_A,
        "title": "PostgreSQL operations guide",
        "tags": ["db", "ops"],
        "chunks": [
            (
                "Guide > Install",
                "Install PostgreSQL on macOS using Homebrew: brew install postgresql and start the service.",
            ),
            (
                "Guide > Tuning",
                "Configure shared_buffers and work_mem for performance tuning of the database server.",
            ),
            (
                "Guide > Backup",
                "Backups with pgBackRest provide point in time recovery from WAL archives.",
            ),
        ],
    },
    "bread": {
        "col": COL_A,
        "title": "Sourdough bread",
        "tags": ["cooking"],
        "chunks": [
            (
                "Starter",
                "Feed the sourdough starter with flour and water every day until it doubles.",
            ),
            (
                "Baking",
                "Bake the loaf in a preheated dutch oven at 250 degrees for twenty minutes.",
            ),
            ("Shaping", "Shape the dough gently and let it proof overnight in the fridge."),
        ],
    },
    "k8s": {
        "col": COL_B,
        "title": "Kubernetes networking",
        "tags": ["ops"],
        "chunks": [
            (
                "Services",
                "Services expose pods through kube-proxy and iptables rules on every node.",
            ),
            (
                "Ingress",
                "Ingress controllers route external HTTP traffic to services by host and path.",
            ),
        ],
    },
    "rust": {
        "col": COL_B,
        "title": "Rust ownership",
        "tags": ["lang"],
        "chunks": [
            (
                f"Ownership > Part {i}",
                f"The borrow checker enforces ownership rules, lesson {i} about borrow checker lifetimes.",
            )
            for i in range(6)
        ],
    },
    "xss": {
        "col": COL_B,
        "title": "Security notes <b>bold</b>",
        "tags": ["security"],
        "chunks": [
            (
                "XSS",
                "Never render <script>alert('pwned')</script> from user input & always escape output.",
            ),
        ],
    },
}


@pytest.fixture(scope="session")
async def corpus(app: object) -> dict[str, Any]:
    from kb.db import connection

    emb = FakeEmbedder(256)
    ids: dict[str, Any] = {"chunks": {}}
    async with connection() as conn, conn.transaction():
        cols = {}
        for slug in (COL_A, COL_B):
            cur = await conn.execute(
                "insert into collections (slug, name) values (%s, %s) returning id", (slug, slug)
            )
            cols[slug] = (await cur.fetchone())["id"]
        for key, doc in CORPUS.items():
            cur = await conn.execute(
                "insert into documents (collection_id, title, content_hash, tags, status, body,"
                " source_uri) values (%s, %s, %s, %s, 'ready', '', %s) returning id",
                (
                    cols[doc["col"]],
                    doc["title"],
                    f"{key}-{SUFFIX}",
                    doc["tags"],
                    f"file:///{key}.md",
                ),
            )
            doc_id = (await cur.fetchone())["id"]
            ids[key] = doc_id
            texts = [f"{h}\n{c}" for h, c in doc["chunks"]]
            vecs = await emb.embed_documents(texts)
            for ord_, ((heading, content), vec) in enumerate(zip(doc["chunks"], vecs, strict=True)):
                cur = await conn.execute(
                    "insert into chunks (document_id, collection_id, ord, heading_path, content,"
                    " embedding) values (%s, %s, %s, %s, %s, %s::halfvec) returning id",
                    (doc_id, cols[doc["col"]], ord_, heading, content, str(vec)),
                )
                ids["chunks"][(key, ord_)] = (await cur.fetchone())["id"]
    return ids


def req(query: str, **kw: Any) -> SearchRequest:
    filters = kw.pop("filters", None) or SearchFilters(collections=[COL_A, COL_B])
    return SearchRequest(query=query, filters=filters, **kw)


async def run(query: str, **kw: Any):
    from kb.search.pipeline import search

    return await search(req(query, **kw))


def doc_ids(resp) -> list:
    return [h.document_id for h in resp.hits]


@pytest.mark.parametrize("mode", ["lexical", "semantic", "hybrid"])
async def test_modes_find_expected_document(corpus, mode):
    resp = await run("feed the sourdough starter flour", mode=mode, rerank=False)
    assert resp.hits, resp
    assert resp.hits[0].document_id == corpus["bread"]
    assert resp.degraded == []
    stages = {t.stage for t in resp.timings}
    if mode != "semantic":
        assert "lexical" in stages and "bm25" in resp.hits[0].scores
    if mode != "lexical":
        assert {"dense", "embed"} <= stages and "dense" in resp.hits[0].scores


async def test_hybrid_scores_and_metadata(corpus):
    resp = await run("pgBackRest point in time recovery")
    top = resp.hits[0]
    assert top.document_id == corpus["pg"]
    assert top.document_title == "PostgreSQL operations guide"
    assert top.collection == COL_A
    assert top.source_uri == "file:///pg.md"
    assert top.tags == ["db", "ops"]
    assert top.heading_path == "Guide > Backup"
    assert {"rrf", "rerank"} <= set(top.scores)
    scores = [h.score for h in resp.hits]
    assert scores == sorted(scores, reverse=True)
    assert "<mark>" in top.snippet


async def test_collection_filter(corpus):
    resp = await run("install service", filters=SearchFilters(collections=[COL_B]))
    assert corpus["pg"] not in doc_ids(resp)
    assert all(h.collection == COL_B for h in resp.hits)
    none = await run("install", filters=SearchFilters(collections=["no-such-collection"]))
    assert none.hits == []


async def test_tag_and_document_filters(corpus):
    resp = await run(
        "oven install pods", filters=SearchFilters(collections=[COL_A, COL_B], tags=["cooking"])
    )
    assert resp.hits and set(doc_ids(resp)) == {corpus["bread"]}
    resp = await run(
        "ops", filters=SearchFilters(tags=["ops"], collections=[COL_A, COL_B]), mode="semantic"
    )
    assert set(doc_ids(resp)) <= {corpus["pg"], corpus["k8s"]}
    resp = await run("services", filters=SearchFilters(document_ids=[corpus["k8s"]]))
    assert resp.hits and set(doc_ids(resp)) == {corpus["k8s"]}


async def test_updated_after_filter(corpus):
    future = datetime.now(UTC) + timedelta(days=1)
    resp = await run("sourdough", filters=SearchFilters(collections=[COL_A], updated_after=future))
    assert resp.hits == []
    past = datetime.now(UTC) - timedelta(days=1)
    resp = await run("sourdough", filters=SearchFilters(collections=[COL_A], updated_after=past))
    assert resp.hits


async def test_inline_operators(corpus):
    resp = await run(f"tag:cooking in:{COL_A} bake", filters=SearchFilters())
    assert resp.hits and set(doc_ids(resp)) == {corpus["bread"]}


async def test_phrase_query(corpus):
    resp = await run('"dutch oven"', mode="lexical", rerank=False)
    assert [h.chunk_id for h in resp.hits] == [corpus["chunks"][("bread", 1)]]
    resp = await run('"oven dutch"', mode="lexical", rerank=False)
    assert resp.hits == []


async def test_per_document_diversity(corpus):
    resp = await run("borrow checker ownership lifetimes", limit=10)
    rust = [h for h in resp.hits if h.document_id == corpus["rust"]]
    assert len(rust) == 3
    resp = await run(
        "borrow checker ownership lifetimes",
        limit=10,
        filters=SearchFilters(document_ids=[corpus["rust"]]),
    )
    assert len(resp.hits) == 6


async def test_rerank_orders_by_cross_encoder(corpus):
    resp = await run("kube-proxy iptables pods services", limit=5)
    rr = [h.scores["rerank"] for h in resp.hits]
    assert rr == sorted(rr, reverse=True)
    assert resp.hits[0].document_id == corpus["k8s"]
    assert any(t.stage == "rerank" for t in resp.timings)


class _BrokenEmbedder(Embedder):
    dim = 256

    async def embed_documents(self, texts):
        raise ProviderError("down")

    async def embed_query(self, text):
        raise ProviderError("down")


class _BrokenReranker(Reranker):
    async def rerank(self, query, documents):
        raise ProviderError("down")


async def test_degraded_when_embedder_down(corpus, monkeypatch):
    import kb.search.dense as dense

    monkeypatch.setattr(dense, "get_embedder", lambda: _BrokenEmbedder())
    resp = await run("sourdough starter")
    assert resp.degraded == ["dense"]
    assert resp.hits and resp.hits[0].document_id == corpus["bread"]
    sem = await run("sourdough starter", mode="semantic")
    # Semantic-only falls back to BM25 rather than failing.
    assert sem.degraded == ["dense"]
    assert sem.hits and sem.hits[0].document_id == corpus["bread"]


async def test_degraded_when_reranker_down(corpus, monkeypatch):
    import kb.search.rerank as rerank

    monkeypatch.setattr(rerank, "get_reranker", lambda: _BrokenReranker())
    resp = await run("sourdough starter")
    assert "rerank" in resp.degraded
    assert resp.hits[0].document_id == corpus["bread"]


class _ScriptedLLM(LLM):
    name = "llm:scripted"
    model = "scripted"

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.prompts: list[str] = []

    async def complete(self, messages, **kw):
        self.prompts.append(messages[-1]["content"])
        return self.reply

    async def stream(self, messages, **kw):  # pragma: no cover
        yield self.reply


async def test_llm_rerank_blends_and_demotes(corpus, monkeypatch):
    import kb.search.rerank as rerank

    only_bread = SearchFilters(document_ids=[corpus["bread"]])
    base = await run("bake bread loaf oven dough", limit=5, filters=only_bread)
    assert len(base.hits) == 3
    first = base.hits[0].chunk_id
    # Judge says passage 1 (the cross-encoder's best) is irrelevant and passage 3 is perfect.
    llm = _ScriptedLLM(
        'Sure! ```json\n{"ranking":[{"id":3,"relevance":3},{"id":"1","relevance":0}]}\n```'
    )
    monkeypatch.setattr(rerank, "choose_llm", lambda: (llm, None))
    resp = await run("bake bread loaf oven dough", limit=5, llm_rerank=True, filters=only_bread)
    assert resp.degraded == []
    assert resp.hits[0].chunk_id == base.hits[2].chunk_id
    assert resp.hits[0].scores["llm"] == 3
    assert resp.hits[-1].chunk_id == first  # demoted to the very end
    assert "[1]" in llm.prompts[0] and "Query: bake bread" in llm.prompts[0]


async def test_llm_rerank_garbage_falls_back(corpus, monkeypatch):
    import kb.search.rerank as rerank

    base = await run("bake bread loaf oven dough", limit=5)
    monkeypatch.setattr(
        rerank, "choose_llm", lambda: (_ScriptedLLM("I cannot help {not json"), None)
    )
    resp = await run("bake bread loaf oven dough", limit=5, llm_rerank=True)
    assert resp.degraded == ["llm_rerank"]
    assert [h.chunk_id for h in resp.hits] == [h.chunk_id for h in base.hits]


async def test_rerank_provider_llm_only(corpus, monkeypatch):
    import kb.search.pipeline as pipeline
    import kb.search.rerank as rerank

    settings = pipeline.get_settings()
    monkeypatch.setattr(settings, "rerank_provider", "llm")
    llm = _ScriptedLLM('{"ranking":[{"id":1,"relevance":2}]}')
    monkeypatch.setattr(rerank, "choose_llm", lambda: (llm, None))
    resp = await run("sourdough starter")
    assert llm.prompts, "LLM stage should run when rerank_provider=llm"
    assert all("rerank" not in h.scores for h in resp.hits)
    assert resp.hits[0].scores.get("llm") == 2


async def test_snippet_is_escaped(corpus):
    resp = await run("alert script escape output", mode="lexical")
    hit = next(h for h in resp.hits if h.document_id == corpus["xss"])
    assert "<script>" not in hit.snippet
    assert "&lt;" in hit.snippet and "<mark>" in hit.snippet
    stripped = hit.snippet.replace("<mark>", "").replace("</mark>", "")
    assert "<" not in stripped and ">" not in stripped
    assert hit.document_title == "Security notes <b>bold</b>"  # raw data, clients escape


async def test_expand_context(corpus):
    resp = await run('"shared_buffers"', mode="lexical", expand_context=True, limit=1)
    hit = resp.hits[0]
    assert hit.ord == 1
    assert "Homebrew" in hit.content and "pgBackRest" in hit.content
    assert "shared_buffers" in hit.content


NASTY = [
    '"unbalanced',
    "a:b",
    "(",
    ")",
    "*",
    "?",
    "'; drop table chunks; --",
    'O\'Reilly "quoted" \\ backslash',
    "🦊🔥 emoji ünïcödé 日本語",
    "tag:",
    "in:Bad_Slug!",
    'tag:"" in:""',
    "AND OR NOT",
    "content:foo heading_path:bar",
    "~2 ^3 +must -not [a TO b] {x}",
    "x\x00y\x01z",
    "   ",
    "word " * 400,
    "a" * 2000,
]


@pytest.mark.parametrize("query", NASTY)
async def test_query_fuzz_never_500(corpus, client, query):
    resp = await client.post(
        "/api/search", json={"query": query, "filters": {"collections": [COL_A, COL_B]}}
    )
    assert resp.status_code in (200, 422), resp.text
    if resp.status_code == 200:
        body = resp.json()
        assert isinstance(body["hits"], list)


async def test_get_endpoint(corpus, client):
    resp = await client.get(
        "/api/search", params={"q": "sourdough", "limit": 2, "mode": "lexical", "collection": COL_A}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["mode"] == "lexical" and len(body["hits"]) <= 2
    assert body["hits"][0]["document_id"] == str(corpus["bread"])


async def test_query_log_written(corpus):
    from kb.db import connection
    from kb.search.pipeline import flush_background

    marker = f"querylog {uuid.uuid4().hex}"
    await run(marker)
    await flush_background()
    async with connection() as conn:
        cur = await conn.execute(
            "select mode, latency_ms from query_log where query = %s", (marker,)
        )
        row = await cur.fetchone()
    assert row is not None and row["mode"] == "hybrid"


async def test_cache_hit_and_invalidation(corpus, monkeypatch):
    from kb.search import pipeline

    monkeypatch.setattr(pipeline.cache, "ttl_s", 30.0)
    pipeline.cache.clear()
    first = await run("kubernetes ingress")
    second = await run("kubernetes ingress")
    assert [t.stage for t in second.timings] == ["cache"]
    assert [h.chunk_id for h in first.hits] == [h.chunk_id for h in second.hits]
    from kb.events import broker

    broker.publish_local({"type": "document.status", "status": "ready"})
    third = await run("kubernetes ingress")
    assert [t.stage for t in third.timings] != ["cache"]
    pipeline.cache.clear()


async def test_fifty_concurrent_searches(corpus):
    queries = ["sourdough", "postgres backup", "borrow checker", "ingress", "dutch oven"] * 10
    results = await asyncio.gather(*(run(q) for q in queries))
    assert len(results) == 50
    assert all(r.hits for r in results)
