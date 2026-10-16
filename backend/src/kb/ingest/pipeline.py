"""Document ingestion: chunk → contextualise → embed → swap chunks atomically.

Model calls happen with no transaction open. The final write is one short transaction
that replaces all chunks of the document and flips it to ``ready``. A per-document
session advisory lock serialises concurrent runs for the same document, and a content
hash check discards the result of a run whose input changed meanwhile (the newer run
that was queued by the change will write instead), so retries are idempotent.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal
from uuid import UUID

import structlog

from kb.config import get_settings
from kb.db import LONG_STATEMENT_TIMEOUT, connection
from kb.ingest import queue
from kb.ingest.chunking import chunk_markdown
from kb.ingest.contextual import build_contexts
from kb.providers import ProviderError, get_embedder

log = structlog.get_logger(__name__)

# Two-int advisory lock namespace ("kb" in ASCII) + hashtext(document id).
_LOCK_NS = 0x6B62
REEMBED_DELAY_S = 60.0
REEMBED_MAX_ATTEMPTS = 12

Outcome = Literal["ready", "failed", "missing", "stale", "embedded", "noop"]


class DocumentBusy(Exception):
    """Another worker is ingesting this document right now."""


def to_vector(values: list[float]) -> str:
    """pgvector text literal (works for vector and halfvec casts and COPY)."""
    return "[" + ",".join(f"{v:.7g}" for v in values) + "]"


def embed_text(context: str, content: str) -> str:
    return f"{context}\n\n{content}" if context else content


@asynccontextmanager
async def document_lock(document_id: UUID) -> AsyncIterator[None]:
    """Hold a session advisory lock for the whole run without an open transaction."""
    async with connection() as conn:
        cur = await conn.execute(
            "select pg_try_advisory_lock(%s, hashtext(%s)) as ok", (_LOCK_NS, str(document_id))
        )
        row = await cur.fetchone()
        if not row or not row["ok"]:
            raise DocumentBusy(str(document_id))
        try:
            yield
        finally:
            try:
                await conn.execute(
                    "select pg_advisory_unlock(%s, hashtext(%s))", (_LOCK_NS, str(document_id))
                )
            except Exception:
                # Never hand a connection that may still hold the lock back to the pool.
                await conn.close()
                raise


async def embed_all(texts: list[str]) -> list[list[float]]:
    embedder = get_embedder()
    size = max(1, get_settings().embedding_batch_size)
    out: list[list[float]] = []
    for i in range(0, len(texts), size):
        out.extend(await embedder.embed_documents(texts[i : i + size]))
    if len(out) != len(texts):
        raise ProviderError(f"embedder returned {len(out)} vectors for {len(texts)} texts")
    return out


async def mark_failed(document_id: UUID, error: str) -> None:
    async with connection() as conn:
        await conn.execute(
            "update documents set status = 'failed', error = %s where id = %s",
            (error[:2000], document_id),
        )


async def ingest_document(document_id: UUID) -> Outcome:
    """Run the full pipeline for one document. Raises only on transient (retryable) errors."""
    async with document_lock(document_id):
        async with connection() as conn:
            cur = await conn.execute(
                """
                update documents set status = 'processing', error = null
                where id = %s
                returning collection_id, title, body, content_hash
                """,
                (document_id,),
            )
            doc = await cur.fetchone()
        if doc is None:
            return "missing"

        body: str = doc["body"]
        s = get_settings()
        if len(body) > 200_000:
            chunks = await asyncio.to_thread(
                chunk_markdown,
                body,
                target_tokens=s.chunk_target_tokens,
                overlap_tokens=s.chunk_overlap_tokens,
            )
        else:
            chunks = chunk_markdown(
                body, target_tokens=s.chunk_target_tokens, overlap_tokens=s.chunk_overlap_tokens
            )
        if not chunks:
            await mark_failed(document_id, "document has no text content")
            return "failed"

        contexts = await build_contexts(doc["title"], body, chunks)
        vectors: list[str | None]
        degraded: str | None = None
        try:
            embedded = await embed_all(
                [embed_text(ctx, c.content) for ctx, c in zip(contexts, chunks, strict=True)]
            )
            vectors = [to_vector(v) for v in embedded]
        except ProviderError as exc:
            degraded = str(exc)
            vectors = [None] * len(chunks)

        async with connection() as conn, conn.transaction():
            await conn.execute(f"set local statement_timeout = '{LONG_STATEMENT_TIMEOUT}'")
            cur = await conn.execute(
                "select content_hash, collection_id from documents where id = %s for update",
                (document_id,),
            )
            current = await cur.fetchone()
            if current is None:
                return "missing"
            if current["content_hash"] != doc["content_hash"]:
                log.info("ingest.stale", document_id=str(document_id))
                return "stale"
            await conn.execute("delete from chunks where document_id = %s", (document_id,))
            async with conn.cursor().copy(
                """
                copy chunks (document_id, collection_id, ord, heading_path, content, context,
                             token_count, char_start, char_end, embedding)
                from stdin
                """
            ) as copy:
                for chunk, ctx, vec in zip(chunks, contexts, vectors, strict=True):
                    await copy.write_row(
                        (
                            document_id,
                            current["collection_id"],  # may have moved since the run began
                            chunk.ord,
                            chunk.heading_path,
                            chunk.content,
                            ctx,
                            chunk.token_count,
                            chunk.char_start,
                            chunk.char_end,
                            vec,
                        )
                    )
            await conn.execute(
                "update documents set status = 'ready', error = null, chunk_count = %s where id = %s",
                (len(chunks), document_id),
            )
            if degraded is not None:
                await queue.enqueue(
                    conn,
                    queue.REEMBED_DOCUMENT,
                    {"document_id": str(document_id)},
                    dedup_key=f"reembed:{document_id}",
                    delay_s=REEMBED_DELAY_S,
                    max_attempts=REEMBED_MAX_ATTEMPTS,
                )
        if degraded is not None:
            log.warning(
                "ingest.embedder_down",
                document_id=str(document_id),
                error=degraded,
                chunks=len(chunks),
            )
        else:
            log.info("ingest.ready", document_id=str(document_id), chunks=len(chunks))
        return "ready"


async def reembed_document(document_id: UUID) -> Outcome:
    """Fill in embeddings for chunks stored without one. Raises ProviderError to retry."""
    async with connection() as conn:
        cur = await conn.execute(
            """
            select id, context, content from chunks
            where document_id = %s and embedding is null order by ord
            """,
            (document_id,),
        )
        rows = await cur.fetchall()
    if not rows:
        return "noop"
    vectors = await embed_all([embed_text(r["context"], r["content"]) for r in rows])
    async with connection() as conn, conn.transaction(), conn.cursor() as cur:
        await cur.execute(f"set local statement_timeout = '{LONG_STATEMENT_TIMEOUT}'")
        # Chunks replaced by a concurrent re-ingest simply no longer match.
        await cur.executemany(
            "update chunks set embedding = %s::halfvec where id = %s and embedding is null",
            [(to_vector(v), r["id"]) for v, r in zip(vectors, rows, strict=True)],
        )
    log.info("ingest.reembedded", document_id=str(document_id), chunks=len(rows))
    return "embedded"


async def enqueue_missing_embeddings(limit: int = 100) -> int:
    """Safety net: queue re-embedding for ready documents that still have NULL vectors."""
    async with connection() as conn:
        cur = await conn.execute(
            """
            select distinct c.document_id from chunks c
            join documents d on d.id = c.document_id and d.status = 'ready'
            where c.embedding is null
            limit %s
            """,
            (limit,),
        )
        ids = [r["document_id"] for r in await cur.fetchall()]
        queued = 0
        for doc_id in ids:
            if await queue.enqueue(
                conn,
                queue.REEMBED_DOCUMENT,
                {"document_id": str(doc_id)},
                dedup_key=f"reembed:{doc_id}",
                max_attempts=REEMBED_MAX_ATTEMPTS,
            ):
                queued += 1
    return queued
