"""Mutable candidate records that flow through fusion → rerank → post-processing."""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from kb.db import connection

RERANK_TEXT_CHARS = 2000


@dataclass
class Candidate:
    chunk_id: int
    score: float
    scores: dict[str, float] = field(default_factory=dict)
    document_id: UUID | None = None
    ord: int = 0
    heading_path: str = ""
    content: str = ""
    context: str = ""
    title: str = ""
    collection: str = ""
    source_uri: str | None = None
    tags: list[str] = field(default_factory=list)

    @property
    def hydrated(self) -> bool:
        return self.document_id is not None

    def rerank_text(self, limit: int = RERANK_TEXT_CHARS) -> str:
        """Breadcrumb + situating context + chunk, truncated on a word boundary."""
        parts = [p for p in (self.title, self.heading_path, self.context, self.content) if p]
        text = "\n".join(dict.fromkeys(parts))
        if len(text) <= limit:
            return text
        cut = text.rfind(" ", 0, limit)
        return text[: cut if cut > limit * 0.8 else limit]


async def hydrate(cands: list[Candidate]) -> list[Candidate]:
    """Load chunk text and document metadata; drops candidates whose rows vanished."""
    if not cands:
        return []
    ids = [c.chunk_id for c in cands]
    async with connection() as conn:
        cur = await conn.execute(
            """
            select c.id, c.document_id, c.ord, c.heading_path, c.content, c.context,
                   d.title, d.source_uri, d.tags, col.slug
            from chunks c
            join documents d on d.id = c.document_id
            join collections col on col.id = d.collection_id
            where c.id = any(%s::bigint[])
            """,
            (ids,),
        )
        rows = {r["id"]: r for r in await cur.fetchall()}
    out: list[Candidate] = []
    for cand in cands:
        r = rows.get(cand.chunk_id)
        if r is None:
            continue
        cand.document_id = r["document_id"]
        cand.ord = r["ord"]
        cand.heading_path = r["heading_path"]
        cand.content = r["content"]
        cand.context = r["context"]
        cand.title = r["title"]
        cand.source_uri = r["source_uri"]
        cand.tags = list(r["tags"] or [])
        cand.collection = r["slug"]
        out.append(cand)
    return out
