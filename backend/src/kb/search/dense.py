"""Dense retriever: query embedding + HNSW (halfvec cosine) nearest neighbours.

``hnsw.iterative_scan = relaxed_order`` makes pgvector keep scanning the graph until
enough rows survive the filters, so a collection/tag-filtered query still returns
``limit`` rows. Relaxed order can be slightly out of order, so results are re-sorted.
"""

from __future__ import annotations

import time

from psycopg import sql

from kb.db import connection
from kb.providers import get_embedder
from kb.schemas import SearchFilters
from kb.search.filters import filter_sql

EF_SEARCH = 200


def to_halfvec(vec: list[float]) -> str:
    return "[" + ",".join(f"{v:.6g}" for v in vec) + "]"


async def embed_query(text: str) -> list[float]:
    return await get_embedder().embed_query(text)


async def dense_search(
    vector: list[float], filters: SearchFilters, limit: int
) -> list[tuple[int, float]]:
    """Return ``[(chunk_id, cosine_similarity)]`` best first."""
    fsql, params = filter_sql(filters)
    params |= {"v": to_halfvec(vector), "limit": limit}
    query = (
        "select c.id, c.embedding <=> %(v)s::halfvec as dist from chunks c "
        "where c.embedding is not null"
        + fsql
        + " order by c.embedding <=> %(v)s::halfvec limit %(limit)s"
    )
    async with connection() as conn, conn.transaction():
        await conn.execute("set local statement_timeout = '10s'")
        await conn.execute(
            sql.SQL("set local hnsw.ef_search = {}").format(sql.Literal(max(EF_SEARCH, limit)))
        )
        await conn.execute("set local hnsw.iterative_scan = relaxed_order")
        cur = await conn.execute(query, params)
        rows = await cur.fetchall()
    hits = [(int(r["id"]), 1.0 - float(r["dist"])) for r in rows]
    hits.sort(key=lambda h: h[1], reverse=True)
    return hits


async def embed_and_search(
    text: str, filters: SearchFilters, limit: int
) -> tuple[list[tuple[int, float]], float]:
    """Embed then search; returns hits and the embedding latency in ms."""
    t0 = time.perf_counter()
    vector = await embed_query(text)
    embed_ms = (time.perf_counter() - t0) * 1000
    return await dense_search(vector, filters, limit), embed_ms
