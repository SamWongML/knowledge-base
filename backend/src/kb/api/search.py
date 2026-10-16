from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from kb.schemas import SearchFilters, SearchMode, SearchRequest, SearchResponse
from kb.search.pipeline import search

router = APIRouter(tags=["search"])


@router.post("/search", response_model=SearchResponse)
async def search_post(req: SearchRequest) -> SearchResponse:
    """Hybrid (BM25 + dense) search with reranking. Supports inline `"phrase"`, `tag:x`, `in:slug`."""
    return await search(req)


@router.get("/search", response_model=SearchResponse)
async def search_get(
    q: Annotated[str, Query(min_length=1, max_length=2000)],
    limit: Annotated[int, Query(ge=1, le=100)] = 10,
    mode: SearchMode = "hybrid",
    collection: Annotated[list[str] | None, Query()] = None,
    tag: Annotated[list[str] | None, Query()] = None,
    document_id: Annotated[list[UUID] | None, Query()] = None,
    rerank: bool = True,
    llm_rerank: bool | None = None,
    expand_context: bool = False,
) -> SearchResponse:
    """Convenience GET form: `/api/search?q=...&limit=&mode=&collection=&tag=`."""
    req = SearchRequest(
        query=q,
        mode=mode,
        limit=limit,
        filters=SearchFilters(collections=collection, tags=tag, document_ids=document_id),
        rerank=rerank,
        llm_rerank=llm_rerank,
        expand_context=expand_context,
    )
    return await search(req)
