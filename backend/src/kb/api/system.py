"""Health, statistics, live events (SSE) and recent queries."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Query
from sse_starlette import EventSourceResponse, ServerSentEvent

from kb import __version__
from kb.api.sse import sse_response
from kb.db import connection
from kb.events import broker
from kb.providers import CircuitOpenError, get_embedder, get_llm, get_reranker
from kb.schemas import ComponentHealth, Health, Model, Stats

router = APIRouter(tags=["system"])
public_router = APIRouter(tags=["system"])

HEALTH_TIMEOUT_S = 3.0
HEALTH_CACHE_S = 10.0
PING_S = 15


class RecentQuery(Model):
    query: str
    mode: str
    created_at: datetime


async def _db() -> str | None:
    async with connection() as conn:
        await conn.execute("select 1")
    return None


async def _embedder() -> str | None:
    embedder = get_embedder()
    await embedder.health()
    return embedder.name


async def _reranker() -> str | None:
    reranker = get_reranker()
    if reranker is None:
        return "disabled"
    await reranker.health()
    return reranker.name


async def _llm() -> str | None:
    llm = get_llm("local")
    await llm.health()
    return llm.name


async def _check(name: str, probe: Callable[[], Awaitable[str | None]]) -> ComponentHealth:
    t0 = time.perf_counter()
    try:
        detail = await asyncio.wait_for(probe(), HEALTH_TIMEOUT_S)
        ok = True
    except TimeoutError:
        ok, detail = False, f"timed out after {HEALTH_TIMEOUT_S:.0f}s"
    except CircuitOpenError as exc:
        ok, detail = False, str(exc)
    except Exception as exc:
        ok, detail = False, f"{type(exc).__name__}: {exc}"[:300]
    return ComponentHealth(
        name=name, ok=ok, latency_ms=round((time.perf_counter() - t0) * 1000, 1), detail=detail
    )


_health_cache: tuple[float, Health] | None = None
_health_lock = asyncio.Lock()


@public_router.get("/health", response_model=Health)
async def health(fresh: bool = False) -> Health:
    """Probe every component concurrently. ``down`` only when the database is unreachable."""
    global _health_cache
    async with _health_lock:  # concurrent callers share one probe round
        now = time.monotonic()
        if not fresh and _health_cache and now - _health_cache[0] < HEALTH_CACHE_S:
            return _health_cache[1]
        components = list(
            await asyncio.gather(
                _check("db", _db),
                _check("embedder", _embedder),
                _check("reranker", _reranker),
                _check("llm", _llm),
            )
        )
        if not components[0].ok:
            overall = "down"
        elif all(c.ok for c in components):
            overall = "ok"
        else:
            overall = "degraded"
        result = Health(status=overall, components=components, version=__version__)
        _health_cache = (time.monotonic(), result)
        return result


@router.get("/stats", response_model=Stats)
async def stats() -> Stats:
    async with connection() as conn:
        cur = await conn.execute(
            """
            select
              (select count(*) from documents)::int as documents,
              (select count(*) from chunks)::int as chunks,
              (select count(*) from collections)::int as collections,
              (select count(*) from jobs where status in ('queued', 'running'))::int as queued_jobs,
              (select count(*) from jobs where status = 'failed')::int as failed_jobs,
              (select count(*) from query_log
                where created_at > now() - interval '24 hours')::int as queries_24h
            """
        )
        return Stats.model_validate(await cur.fetchone())


@router.get("/events")
async def events() -> EventSourceResponse:
    """Server-sent events: ``document.status`` updates (plus a ``ping`` every 15 s)."""

    async def stream() -> AsyncIterator[dict[str, Any]]:
        async with broker.subscribe() as queue:
            while True:
                event = await queue.get()
                yield {
                    "event": event.get("type", "message"),
                    "data": json.dumps(event, default=str),
                }

    return sse_response(
        stream(),
        ping=PING_S,
        ping_message_factory=lambda: ServerSentEvent(event="ping", data="{}"),
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )


@router.get("/queries/recent", response_model=list[RecentQuery])
async def recent_queries(limit: Annotated[int, Query(ge=1, le=200)] = 20) -> list[dict[str, Any]]:
    """Most recent distinct queries, newest first."""
    async with connection() as conn:
        cur = await conn.execute(
            """
            select query, mode, created_at from (
                select distinct on (lower(query)) id, query, mode, created_at
                from query_log
                where created_at > now() - interval '90 days'
                order by lower(query), created_at desc, id desc
            ) q
            order by created_at desc, id desc
            limit %s
            """,
            (limit,),
        )
        return list(await cur.fetchall())
