"""BM25 retriever over the ParadeDB ``chunks_bm25`` index.

Scoring = BM25(content) + 0.6·BM25(heading_path) + 0.5·BM25(context), using the
``|||`` (match-any) operator, which tokenizes the user text with the field's own
tokenizer (English stemmer) – no query-parser syntax is ever interpreted. Quoted phrases
become required ``###`` (phrase) matches over content or heading.
"""

from __future__ import annotations

from typing import Any, Final

from psycopg import sql

from kb.db import connection
from kb.schemas import SearchFilters
from kb.search.filters import filter_sql
from kb.search.query import ParsedQuery

# Kept as SQL literals so the statement text is a compile-time constant.
HEADING_BOOST: Final = "0.6"
CONTEXT_BOOST: Final = "0.5"


def build_lexical_sql(
    pq: ParsedQuery, filters: SearchFilters, limit: int
) -> tuple[sql.Composed, dict[str, Any]]:
    """Build the BM25 query. All user text is bound as parameters, never spliced."""
    params: dict[str, Any] = {"limit": limit}
    conds: list[sql.Composable] = []
    if pq.text:
        params["q"] = pq.text
        conds.append(
            sql.SQL(
                "(c.content ||| %(q)s"
                f" or c.heading_path ||| %(q)s::pdb.boost({HEADING_BOOST})"
                f" or c.context ||| %(q)s::pdb.boost({CONTEXT_BOOST}))"
            )
        )
    for i, phrase in enumerate(pq.phrases):
        key = f"p{i}"
        params[key] = phrase
        conds.append(
            sql.SQL("(c.content ### {k} or c.heading_path ### {k})").format(k=sql.Placeholder(key))
        )
    fsql, fparams = filter_sql(filters)
    params.update(fparams)
    query = sql.SQL(
        "select c.id, paradedb.score(c.id) as score from chunks c where {conds}"
        + fsql
        + " order by score desc limit %(limit)s"
    ).format(conds=sql.SQL(" and ").join(conds))
    return query, params


async def lexical_search(
    pq: ParsedQuery, filters: SearchFilters, limit: int
) -> list[tuple[int, float]]:
    """Return ``[(chunk_id, bm25_score)]`` best first."""
    if pq.empty:
        return []
    query, params = build_lexical_sql(pq, filters, limit)
    async with connection() as conn, conn.transaction():
        await conn.execute("set local statement_timeout = '10s'")
        cur = await conn.execute(query, params)
        rows = await cur.fetchall()
    return [(int(r["id"]), float(r["score"])) for r in rows]
