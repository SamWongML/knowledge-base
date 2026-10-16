"""Pure unit tests for search building blocks (no database)."""

from __future__ import annotations

import itertools
import uuid

import pytest

from kb.events import EventBroker
from kb.schemas import SearchFilters, SearchResponse
from kb.search.cache import SearchCache, attach_to_broker
from kb.search.candidates import Candidate
from kb.search.filters import filter_sql, single_document
from kb.search.fusion import convex, fuse, minmax, rrf
from kb.search.postprocess import _strip_overlap, diversify, make_snippet
from kb.search.query import normalize, parse_query
from kb.search.rerank import blend, parse_ranking

# --- query analysis ---------------------------------------------------------------------


def test_parse_operators_and_phrases():
    pq = parse_query('tag:ops in:Docs "dutch oven" bake tag:"two words" a:b')
    assert pq.phrases == ["dutch oven"]
    assert pq.text == "bake a:b"
    assert pq.filters.tags == ["ops", "two words"]
    assert pq.filters.collections == ["docs"]
    assert pq.semantic == "bake a:b dutch oven"
    assert "dutch" in pq.terms and "bake" in pq.terms


def test_parse_merges_with_request_filters():
    pq = parse_query("x tag:b in:c2", SearchFilters(tags=["a", "b"], collections=["c1"]))
    assert pq.filters.tags == ["a", "b"]
    assert pq.filters.collections == ["c1", "c2"]


@pytest.mark.parametrize(
    "raw, text",
    [
        ('"unbalanced', "unbalanced"),
        ('"one"', "one"),
        ('""', ""),
        ("in:Bad_Slug!", "in:Bad_Slug!"),
        ("tag:", "tag:"),
        ("x\x00y\x07z", "x y z"),
        ("  lots \n\t of   space ", "lots of space"),
        ("ｆｕｌｌ ｗｉｄｔｈ", "full width"),
    ],
)
def test_parse_edge_cases(raw, text):
    assert parse_query(raw).text == text


def test_parse_caps_length():
    pq = parse_query("word " * 1000)
    assert len(pq.text) <= 1000
    assert "\x00" not in normalize("a\x00b")


def test_empty_query():
    assert parse_query("tag:x").empty
    assert not parse_query('"a b"').empty


# --- filters ----------------------------------------------------------------------------


def test_filter_sql_shapes():
    doc = uuid.uuid4()
    sql, params = filter_sql(
        SearchFilters(collections=["a"], tags=["t"], document_ids=[doc], updated_after=None)
    )
    assert "c.collection_id = any(" in sql and "c.document_id = any(%(f_docs)s" in sql
    assert "tags && %(f_tags)s" in sql
    assert params == {"f_cols": ["a"], "f_docs": [doc], "f_tags": ["t"]}
    assert filter_sql(SearchFilters()) == ("", {})
    assert single_document(SearchFilters(document_ids=[doc, doc]))
    assert not single_document(SearchFilters())


# --- fusion -----------------------------------------------------------------------------


def test_rrf_math():
    fused = rrf({"bm25": [(1, 9.0), (2, 5.0)], "dense": [(2, 0.9), (3, 0.8)]}, k=60)
    assert fused[1] == pytest.approx(1 / 61)
    assert fused[2] == pytest.approx(1 / 62 + 1 / 61)
    assert fused[3] == pytest.approx(1 / 62)
    weighted = rrf({"bm25": [(1, 1.0)], "dense": [(1, 1.0)]}, k=0, weights={"dense": 2.0})
    assert weighted[1] == pytest.approx(3.0)


def test_fuse_orders_and_keeps_detail():
    out = fuse({"bm25": [(1, 9.0), (2, 5.0)], "dense": [(2, 0.9), (3, 0.8)]}, k=60)
    assert [cid for cid, _, _ in out] == [2, 1, 3]
    detail = out[0][2]
    assert detail["bm25"] == 5.0 and detail["bm25_rank"] == 2.0
    assert detail["dense"] == 0.9 and detail["dense_rank"] == 1.0
    assert detail["rrf"] == pytest.approx(1 / 62 + 1 / 61)
    # Tie between 1 and 3 broken by best rank, then id.
    assert out[1][0] == 1


def test_minmax_and_convex():
    assert minmax([]) == {}
    assert minmax([(1, 2.0), (2, 2.0)]) == {1: 1.0, 2: 1.0}
    assert minmax([(1, 10.0), (2, 5.0), (3, 0.0)]) == {1: 1.0, 2: 0.5, 3: 0.0}
    fused = convex(
        {"bm25": [(1, 10.0), (2, 0.0)], "dense": [(2, 0.9), (3, 0.1)]},
        weights={"bm25": 0.3, "dense": 0.7},
    )
    assert fused == pytest.approx({1: 0.3, 2: 0.7, 3: 0.0})
    out = fuse({"bm25": [(1, 1.0)], "dense": [(1, 1.0)]}, method="convex")
    assert out[0][1] == pytest.approx(1.0) and "convex" in out[0][2]


# --- LLM rerank parsing -----------------------------------------------------------------


@pytest.mark.parametrize(
    "reply, expected",
    [
        ('{"ranking":[{"id":2,"relevance":3},{"id":1,"relevance":0}]}', {1: 3, 0: 0}),
        ('```json\n{"ranking":[{"id":"[1]","relevance":2.6}]}\n```', {0: 3}),
        ('Here you go: [{"id": 3, "relevance": 9}, {"id": 99, "relevance": 1}]', {2: 3}),
        ('{"ranking":[{"id":1,"relevance":1},{"id":1,"relevance":3}]}', {0: 1}),
        ('{"results":[{"id":1,"score":-4}]}', {0: 0}),
        ("garbage", None),
        ("", None),
        ('{"ranking": "nope"}', None),
        ('{"ranking": [1, 2, "x", null, {"id": null}]}', None),
        ('{"ranking": []}', None),
        ("{broken json [", None),
    ],
)
def test_parse_ranking(reply, expected):
    assert parse_ranking(reply, 3) == expected


def _cands(scores: list[float]) -> list[Candidate]:
    return [Candidate(chunk_id=i, score=s) for i, s in enumerate(scores)]


def test_blend_promotes_and_demotes():
    cands = _cands([0.9, 0.8, 0.7, 0.6])  # window 3, one tail item
    out = blend(cands, 3, {0: 0, 2: 3})
    assert [c.chunk_id for c in out] == [2, 1, 3, 0]
    assert out[0].scores["llm"] == 3 and out[-1].scores["llm"] == 0
    assert all(a.score >= b.score for a, b in itertools.pairwise(out))


# --- post-processing --------------------------------------------------------------------


def test_snippet_highlights_and_escapes():
    text = "Intro <b>bold</b> & stuff. " + "filler " * 80 + "We were installing PostgreSQL servers."
    snip = make_snippet(text, ["install", "postgresql"])
    assert "<mark>installing</mark>" in snip and "<mark>PostgreSQL</mark>" in snip
    assert snip.startswith("…")
    assert "<b>" not in snip
    short = make_snippet("<img src=x onerror=alert(1)>", ["alert"])
    assert short == "&lt;img src=x onerror=<mark>alert</mark>(1)&gt;"
    assert make_snippet("plain text", []) == "plain text"


def test_diversify_caps_per_document():
    d1, d2 = uuid.uuid4(), uuid.uuid4()
    cands = [Candidate(chunk_id=i, score=1, document_id=d1) for i in range(5)]
    cands.append(Candidate(chunk_id=9, score=0.5, document_id=d2))
    assert [c.chunk_id for c in diversify(cands, 10, 3)] == [0, 1, 2, 9]
    assert len(diversify(cands, 4, None)) == 4


def test_strip_overlap():
    left = "alpha beta gamma delta epsilon zeta eta theta"
    right = "epsilon zeta eta theta iota kappa"
    assert _strip_overlap(left, right) == "iota kappa"
    assert _strip_overlap("abc", "xyz") == "xyz"


# --- cache ------------------------------------------------------------------------------


def test_cache_ttl_version_and_lru(monkeypatch):
    resp = SearchResponse(query="q", mode="hybrid", hits=[], timings=[], total_ms=1)
    off = SearchCache(ttl_s=0)
    off.put("k", resp)
    assert off.get("k") is None
    cache = SearchCache(ttl_s=60, max_entries=2)
    cache.put("a", resp)
    got = cache.get("a")
    assert got == resp and got is not resp
    broker = EventBroker()
    monkeypatch.setattr("kb.events.broker", broker)
    attach_to_broker(cache)
    broker.publish_local({"type": "document.status"})
    assert cache.get("a") is None
    for key in "abc":
        cache.put(key, resp)
    assert cache.get("a") is None and cache.get("c") is not None
