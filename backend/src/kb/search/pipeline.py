"""Hybrid retrieval pipeline.

``parse → (BM25 ‖ embed+HNSW) → fusion (RRF | convex) → hydrate → cross-encoder → LLM judge
→ per-document diversity → context expansion → snippets``.

Every model-backed stage is optional and fails soft: an embedder outage yields lexical-only
results with ``degraded=["dense"]``; a reranker/LLM outage keeps the previous order.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import time
from dataclasses import dataclass, field
from typing import Any

import structlog

from kb.config import get_settings
from kb.db import connection
from kb.schemas import SearchHit, SearchRequest, SearchResponse, StageTiming
from kb.search.cache import SearchCache, attach_to_broker
from kb.search.candidates import Candidate, hydrate
from kb.search.dense import embed_and_search
from kb.search.filters import single_document
from kb.search.fusion import FusionMethod, fuse
from kb.search.lexical import lexical_search
from kb.search.postprocess import diversify, expand_context, make_snippet
from kb.search.query import ParsedQuery, parse_query
from kb.search.rerank import cross_encode, llm_rerank

log = structlog.get_logger(__name__)

MAX_PER_DOCUMENT = 3
HYDRATE_MAX = 200

cache = SearchCache(ttl_s=0.0 if get_settings().env == "test" else 30.0)
attach_to_broker(cache)

_background: set[asyncio.Task[Any]] = set()


@dataclass
class SearchOptions:
    """Knobs beyond :class:`SearchRequest`, used by the eval harness and power users."""

    fusion: FusionMethod = "rrf"
    weights: dict[str, float] | None = None  # per retriever: {"bm25": .., "dense": ..}
    per_document: int | None = MAX_PER_DOCUMENT
    use_cache: bool = True
    log_query: bool = True


@dataclass
class _Run:
    timings: list[StageTiming] = field(default_factory=list)
    degraded: list[str] = field(default_factory=list)

    def time(self, stage: str, t0: float, detail: str | None = None) -> float:
        now = time.perf_counter()
        self.timings.append(StageTiming(stage=stage, ms=round((now - t0) * 1000, 2), detail=detail))
        return now


def _cache_key(req: SearchRequest, opts: SearchOptions) -> str:
    payload = req.model_dump(mode="json") | {
        "_opts": [opts.fusion, opts.weights, opts.per_document]
    }
    return json.dumps(payload, sort_keys=True, default=str)


async def _retrieve(
    req: SearchRequest, pq: ParsedQuery, run: _Run
) -> dict[str, list[tuple[int, float]]]:
    s = get_settings()
    n = max(s.search_candidates, req.limit)
    want_lexical = req.mode in ("hybrid", "lexical")
    want_dense = req.mode in ("hybrid", "semantic") and bool(pq.semantic)

    async def timed(name: str, coro: Any) -> Any:
        t0 = time.perf_counter()
        try:
            return await coro
        finally:
            run.time(name, t0)

    tasks: dict[str, asyncio.Task[Any]] = {}
    async with asyncio.TaskGroup() as tg:
        if want_lexical:
            tasks["bm25"] = tg.create_task(
                _soft(timed("lexical", lexical_search(pq, pq.filters, n)))
            )
        if want_dense:
            tasks["dense"] = tg.create_task(
                _soft(timed("dense", embed_and_search(pq.semantic, pq.filters, n)))
            )

    rankings: dict[str, list[tuple[int, float]]] = {}
    errors: dict[str, BaseException] = {}
    for name, task in tasks.items():
        ok, value = task.result()
        if not ok:
            errors[name] = value
            run.degraded.append("lexical" if name == "bm25" else "dense")
            log.warning("search.retriever_failed", retriever=name, error=repr(value))
            continue
        if name == "dense":
            hits, embed_ms = value
            run.timings.append(StageTiming(stage="embed", ms=round(embed_ms, 2)))
            rankings[name] = hits
        else:
            rankings[name] = value
    if "dense" in errors and req.mode == "semantic":
        # Semantic-only request with the embedder down: BM25 is better than nothing.
        t0 = time.perf_counter()
        rankings["bm25"] = await lexical_search(pq, pq.filters, n)
        run.time("lexical", t0, "fallback")
        return rankings
    if errors and len(errors) == len(tasks):
        # Nothing worked. A DB failure is a real error; surface it.
        raise next(iter(errors.values()))
    return rankings


async def _soft(coro: Any) -> tuple[bool, Any]:
    try:
        return True, await coro
    except Exception as exc:  # degrade instead of failing the request
        return False, exc


def _wants_llm(req: SearchRequest) -> bool:
    s = get_settings()
    if req.llm_rerank is not None:
        return req.llm_rerank
    return req.rerank and (s.llm_rerank or s.rerank_provider == "llm")


def _monotonic(cands: list[Candidate]) -> None:
    """Guarantee ``score`` is non-increasing in result order (higher = better)."""
    for prev, cur in itertools.pairwise(cands):
        if cur.score > prev.score:
            cur.score = prev.score - 1e-6


async def search(req: SearchRequest, options: SearchOptions | None = None) -> SearchResponse:
    opts = options or SearchOptions()
    s = get_settings()
    started = time.perf_counter()
    run = _Run()

    key = _cache_key(req, opts)
    cache_version = cache.version
    if opts.use_cache and (cached := cache.get(key)) is not None:
        cached.timings = [StageTiming(stage="cache", ms=0.0, detail="hit")]
        cached.total_ms = round((time.perf_counter() - started) * 1000, 2)
        return cached

    t = time.perf_counter()
    pq = parse_query(req.query, req.filters)
    t = run.time("parse", t)

    cands: list[Candidate] = []
    if not pq.empty:
        rankings = await _retrieve(req, pq, run)
        t = time.perf_counter()
        fused = fuse(rankings, method=opts.fusion, k=s.rrf_k, weights=opts.weights)
        cands = [Candidate(chunk_id=cid, score=sc, scores=detail) for cid, sc, detail in fused]
        t = run.time("fusion", t, f"{opts.fusion}: {len(cands)} candidates")

        window = max(s.rerank_candidates, req.limit)
        cands = await hydrate(cands[: max(window, min(HYDRATE_MAX, req.limit * 4))])
        t = run.time("hydrate", t)

        if req.rerank and s.rerank_provider not in ("none", "llm") and cands:
            try:
                cands = await cross_encode(pq.semantic, cands, window)
            except Exception as exc:
                run.degraded.append("rerank")
                log.warning("search.rerank_failed", error=repr(exc))
            t = run.time("rerank", t, f"top {min(window, len(cands))}")

        if _wants_llm(req) and cands:
            try:
                cands = await llm_rerank(pq.semantic, cands, s.llm_rerank_top_n)
            except Exception as exc:
                run.degraded.append("llm_rerank")
                log.warning("search.llm_rerank_failed", error=repr(exc))
            t = run.time("llm_rerank", t, f"top {min(s.llm_rerank_top_n, len(cands))}")

    per_doc = None if single_document(pq.filters) else opts.per_document
    cands = diversify(cands, req.limit, per_doc)
    _monotonic(cands)
    snippets = {c.chunk_id: make_snippet(c.content, pq.terms) for c in cands}
    if req.expand_context:
        await expand_context(cands)
    hits = [
        SearchHit(
            chunk_id=c.chunk_id,
            document_id=c.document_id,
            document_title=c.title,
            collection=c.collection,
            source_uri=c.source_uri,
            heading_path=c.heading_path,
            content=c.content,
            snippet=snippets[c.chunk_id],
            score=c.score,
            scores=c.scores,
            ord=c.ord,
            tags=c.tags,
        )
        for c in cands
        if c.document_id is not None  # hydrate() drops chunks deleted mid-query
    ]
    run.time("postprocess", t)

    total_ms = round((time.perf_counter() - started) * 1000, 2)
    resp = SearchResponse(
        query=req.query,
        mode=req.mode,
        hits=hits,
        timings=run.timings,
        degraded=run.degraded,
        total_ms=total_ms,
    )
    if opts.use_cache and not run.degraded:
        cache.put(key, resp, cache_version)
    if opts.log_query:
        _spawn(_log_query(req, total_ms, [h.chunk_id for h in hits]))
    return resp


def _spawn(coro: Any) -> None:
    task = asyncio.create_task(coro)
    _background.add(task)
    task.add_done_callback(_background.discard)


async def _log_query(req: SearchRequest, latency_ms: float, result_ids: list[int]) -> None:
    try:
        async with connection() as conn:
            await conn.execute(
                "insert into query_log (query, mode, filters, latency_ms, result_ids)"
                " values (%s, %s, %s::jsonb, %s, %s)",
                (
                    req.query.replace("\x00", ""),
                    req.mode,
                    req.filters.model_dump_json(exclude_none=True),
                    int(latency_ms),
                    result_ids,
                ),
            )
    except Exception as exc:  # analytics must never fail a search
        log.warning("search.query_log_failed", error=repr(exc))


async def flush_background() -> None:
    """Wait for pending query-log writes (tests, graceful shutdown)."""
    if _background:
        await asyncio.gather(*list(_background), return_exceptions=True)
