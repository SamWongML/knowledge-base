"""BM25 retriever over the ParadeDB ``chunks_bm25`` index.

Scoring = BM25(content) + 0.6·BM25(heading_path) + 0.5·BM25(context), using the
``|||`` (match-any) operator, which tokenizes the user text with the field's own
tokenizer (English stemmer) – no query-parser syntax is ever interpreted. Quoted phrases
become required ``###`` (phrase) matches over content or heading.
"""

from __future__ import annotations

from typing import Any

from kb.db import connection
from kb.schemas import SearchFilters
from kb.search.filters import filter_sql
from kb.search.query import ParsedQuery

HEADING_BOOST = 0.6
CONTEXT_BOOST = 0.5


def build_lexical_sql(pq: ParsedQuery, filters: SearchFilters, limit: int) -> tuple[str, dict]:
    params: dict[str, Any] = {"limit": limit}
    conds: list[str] = []
    if pq.text:
        params["q"] = pq.text
        conds.append(
            "(c.content ||| %(q)s"
            f" or c.heading_path ||| %(q)s::pdb.boost({HEADING_BOOST})"
            f" or c.context ||| %(q)s::pdb.boost({CONTEXT_BOOST}))"
        )
    for i, phrase in enumerate(pq.phrases):
        key = f"p{i}"
        params[key] = phrase
        conds.append(f"(c.content ### %({key})s or c.heading_path ### %({key})s)")
    fsql, fparams = filter_sql(filters)
    params.update(fparams)
    sql = (
        "select c.id, paradedb.score(c.id) as score from chunks c where "
        + " and ".join(conds)
        + fsql
        + " order by score desc limit %(limit)s"
    )
    return sql, params


async def lexical_search(
    pq: ParsedQuery, filters: SearchFilters, limit: int
) -> list[tuple[int, float]]:
    """Return ``[(chunk_id, bm25_score)]`` best first."""
    if pq.empty:
        return []
    sql, params = build_lexical_sql(pq, filters, limit)
    async with connection() as conn, conn.transaction():
        await conn.execute("set local statement_timeout = '10s'")
        cur = await conn.execute(sql, params)  # type: ignore[arg-type]
        rows = await cur.fetchall()
    return [(int(r["id"]), float(r["score"])) for r in rows]
