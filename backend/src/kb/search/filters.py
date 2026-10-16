"""Translate :class:`SearchFilters` into SQL predicates on ``chunks c``.

Collection and document filters are expressed as ``= any(<array>)`` on columns that live in
the BM25 index, so pg_search pushes them into the Tantivy query (verified with EXPLAIN on
0.26.2: ``terms_with_operator('document_id', '=', $1)``). Tag / date filters resolve to a
document-id array through an uncorrelated sub-select (an InitPlan), which keeps the
pushdown instead of forcing a join (a join also triggers a "JoinScan not used" WARNING).
"""

from __future__ import annotations

from typing import Any, LiteralString

from kb.schemas import SearchFilters


def filter_sql(filters: SearchFilters) -> tuple[LiteralString, dict[str, Any]]:
    """Return ``(" and ...", params)``; the fragment is empty when nothing is filtered."""
    parts: list[LiteralString] = []
    params: dict[str, Any] = {}
    if filters.collections is not None:
        parts.append(
            "c.collection_id = any(array(select id from collections where slug = any(%(f_cols)s)))"
        )
        params["f_cols"] = list(filters.collections)
    if filters.document_ids is not None:
        parts.append("c.document_id = any(%(f_docs)s::uuid[])")
        params["f_docs"] = list(filters.document_ids)
    doc_conds: list[LiteralString] = []
    if filters.tags:
        doc_conds.append("tags && %(f_tags)s::text[]")
        params["f_tags"] = list(filters.tags)
    if filters.updated_after is not None:
        doc_conds.append("updated_at > %(f_after)s")
        params["f_after"] = filters.updated_after
    if doc_conds:
        parts.append(
            "c.document_id = any(array(select id from documents where "
            + " and ".join(doc_conds)
            + "))"
        )
    return "".join(f" and {p}" for p in parts), params


def single_document(filters: SearchFilters) -> bool:
    return filters.document_ids is not None and len(set(filters.document_ids)) == 1
