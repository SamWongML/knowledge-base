"""Document and collection operations shared by the HTTP API, the MCP server and the CLI.

Writes only touch the database and enqueue an ingestion job in the same transaction;
the expensive work (chunking, LLM context, embeddings) happens in the worker.
"""

from __future__ import annotations

import asyncio
import hashlib
from typing import Any, LiteralString
from uuid import UUID

from psycopg import errors
from psycopg.types.json import Jsonb

from kb.db import LONG_STATEMENT_TIMEOUT, Conn, connection
from kb.ingest import queue
from kb.ingest.parsers import (
    ParsedDocument,
    ParseError,
    content_hash,
    normalize_text,
    parse_bytes,
    parse_text,
)
from kb.schemas import (
    Collection,
    CollectionIn,
    Document,
    DocumentIn,
    DocumentPatch,
    DocumentStatus,
    DocumentSummary,
    IngestAccepted,
    Page,
)


class UnknownCollectionError(ValueError):
    def __init__(self, slug: str) -> None:
        super().__init__(f"unknown collection: {slug!r}")
        self.slug = slug


class ConflictError(ValueError):
    """The change would violate a uniqueness rule (duplicate slug, duplicate content)."""


_SUMMARY_COLS = """
    d.id, d.collection_id, c.slug as collection, d.title, d.source_uri, d.mime_type, d.tags,
    d.status, d.error, d.chunk_count, d.created_at, d.updated_at
"""


async def _collection_id(conn: Conn, slug: str) -> UUID:
    cur = await conn.execute("select id from collections where slug = %s", (slug,))
    row = await cur.fetchone()
    if row is None:
        raise UnknownCollectionError(slug)
    return row["id"]


async def _enqueue_ingest(conn: Conn, doc_id: UUID) -> None:
    await queue.enqueue(
        conn,
        queue.INGEST_DOCUMENT,
        {"document_id": str(doc_id)},
        dedup_key=f"ingest:{doc_id}",
    )


def _clean_tags(tags: list[str]) -> list[str]:
    seen: dict[str, None] = {}
    for t in tags:
        t = t.strip()
        if t:
            seen.setdefault(t[:100], None)
    return list(seen)


# --- documents -----------------------------------------------------------------------


async def _store(
    parsed: ParsedDocument,
    *,
    collection: str,
    title: str | None,
    source_uri: str | None,
    tags: list[str],
    metadata: dict[str, Any],
    status: DocumentStatus = "pending",
    error: str | None = None,
    digest: str | None = None,
) -> IngestAccepted:
    """Insert (or upsert by ``source_uri``) a parsed document and queue its ingestion.

    Concurrency: ``on conflict do nothing`` absorbs races on either unique rule (content
    hash or source URI); the loser re-reads and takes the dedup or upsert path.
    """
    digest = digest or content_hash(parsed.markdown)
    title = (title or parsed.title or "Untitled").strip()[:500]
    tags = _clean_tags(tags)
    for _ in range(5):
        async with connection() as conn, conn.transaction():
            coll_id = await _collection_id(conn, collection)
            if source_uri:
                cur = await conn.execute(
                    """
                    select id, content_hash, status from documents
                    where collection_id = %s and source_uri = %s for update
                    """,
                    (coll_id, source_uri),
                )
                existing = await cur.fetchone()
                if existing is not None:
                    return await _replace(conn, existing, parsed, digest, title, tags, metadata)
            cur = await conn.execute(
                """
                insert into documents (collection_id, title, source_uri, mime_type, content_hash,
                                       body, metadata, tags, status, error)
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                on conflict do nothing
                returning id
                """,
                (
                    coll_id,
                    title,
                    source_uri,
                    parsed.mime_type,
                    digest,
                    parsed.markdown,
                    Jsonb(metadata),
                    tags,
                    status,
                    error,
                ),
            )
            row = await cur.fetchone()
            if row is not None:
                if status == "pending":
                    await _enqueue_ingest(conn, row["id"])
                return IngestAccepted(id=row["id"], status=status)
            cur = await conn.execute(
                "select id, status from documents where collection_id = %s and content_hash = %s",
                (coll_id, digest),
            )
            dup = await cur.fetchone()
            if dup is not None:
                return IngestAccepted(id=dup["id"], status=dup["status"], duplicate=True)
        # Lost a race on source_uri: loop and take the upsert path.
    raise ConflictError("document is being modified concurrently; retry")


async def _replace(
    conn: Conn,
    existing: dict[str, Any],
    parsed: ParsedDocument,
    digest: str,
    title: str,
    tags: list[str],
    metadata: dict[str, Any],
) -> IngestAccepted:
    """Upsert by source URI: new content replaces the old and is re-ingested."""
    doc_id = existing["id"]
    if existing["content_hash"] == digest:
        await conn.execute(
            "update documents set title = %s, tags = %s, metadata = metadata || %s where id = %s",
            (title, tags, Jsonb(metadata), doc_id),
        )
        return IngestAccepted(id=doc_id, status=existing["status"], duplicate=True)
    try:
        async with conn.transaction():  # savepoint: the new content may collide on hash
            await conn.execute(
                """
                update documents
                set title = %s, tags = %s, metadata = metadata || %s, mime_type = %s,
                    body = %s, content_hash = %s, status = 'pending', error = null
                where id = %s
                """,
                (title, tags, Jsonb(metadata), parsed.mime_type, parsed.markdown, digest, doc_id),
            )
    except errors.UniqueViolation:
        cur = await conn.execute(
            """
            select d.id, d.status from documents d
            join documents e on e.id = %s and d.collection_id = e.collection_id
            where d.content_hash = %s
            """,
            (doc_id, digest),
        )
        dup = await cur.fetchone()
        if dup is None:
            raise
        return IngestAccepted(id=dup["id"], status=dup["status"], duplicate=True)
    await _enqueue_ingest(conn, doc_id)
    return IngestAccepted(id=doc_id, status="pending")


async def create_document(doc: DocumentIn) -> IngestAccepted:
    """Create from text. Raises UnknownCollectionError, or ParseError for unusable input."""
    filename = doc.source_uri.rsplit("/", 1)[-1] if doc.source_uri else None
    parsed = parse_text(doc.content, mime_type=doc.mime_type, filename=filename)
    return await _store(
        parsed,
        collection=doc.collection,
        title=doc.title,
        source_uri=doc.source_uri,
        tags=doc.tags,
        metadata=doc.metadata,
    )


async def create_from_upload(
    filename: str, data: bytes, collection: str, tags: list[str], mime_type: str | None
) -> IngestAccepted:
    """Create from an uploaded file. Unparseable files are stored as ``failed`` with a reason."""
    if mime_type in (None, "", "application/octet-stream"):
        mime_type = None
    metadata: dict[str, Any] = {"filename": filename, "size_bytes": len(data)}
    try:
        if len(data) > 1_000_000 or filename.lower().endswith((".pdf", ".docx", ".pptx", ".xlsx")):
            parsed = await asyncio.to_thread(
                parse_bytes, data, filename=filename, mime_type=mime_type
            )
        else:
            parsed = parse_bytes(data, filename=filename, mime_type=mime_type)
    except ParseError as exc:
        # Keep a record so the user sees why; hash the raw bytes for dedup.
        failed = ParsedDocument("", filename, mime_type or "application/octet-stream")
        return await _store(
            failed,
            collection=collection,
            title=filename,
            source_uri=None,
            tags=tags,
            metadata=metadata,
            status="failed",
            error=str(exc),
            digest="raw:" + hashlib.sha256(data).hexdigest(),
        )
    return await _store(
        parsed, collection=collection, title=None, source_uri=None, tags=tags, metadata=metadata
    )


async def get_document(id: UUID) -> Document | None:
    async with connection() as conn:
        cur = await conn.execute(
            f"""
            select {_SUMMARY_COLS}, d.body, d.metadata
            from documents d join collections c on c.id = d.collection_id
            where d.id = %s
            """,
            (id,),
        )
        row = await cur.fetchone()
    return Document.model_validate(row) if row else None


def _like(q: str) -> str:
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


async def list_documents(
    *,
    collection: str | None = None,
    status: str | None = None,
    tag: str | None = None,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> Page[DocumentSummary]:
    where: list[LiteralString] = ["true"]
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    async with connection() as conn:
        if collection:
            params["coll"] = await _collection_id(conn, collection)
            where.append("d.collection_id = %(coll)s")
        if status:
            params["status"] = status
            where.append("d.status = %(status)s")
        if tag:
            params["tag"] = tag
            where.append("d.tags @> array[%(tag)s]::text[]")
        if q:
            params["q"] = _like(q)
            where.append("d.title ilike %(q)s")
        clause = " and ".join(where)
        cur = await conn.execute(
            f"select count(*) as n from documents d where {clause}",
            params,
        )
        count_row = await cur.fetchone()
        total = count_row["n"] if count_row else 0
        cur = await conn.execute(
            f"""
            select {_SUMMARY_COLS}
            from documents d join collections c on c.id = d.collection_id
            where {clause}
            order by d.updated_at desc, d.id desc
            limit %(limit)s offset %(offset)s
            """,
            params,
        )
        rows = await cur.fetchall()
    return Page[DocumentSummary](
        items=[DocumentSummary.model_validate(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


async def update_document(id: UUID, patch: DocumentPatch) -> Document | None:
    """Patch fields. New content re-hashes and re-ingests; other fields never re-embed."""
    fields = patch.model_dump(exclude_unset=True)
    parsed: ParsedDocument | None = None
    if fields.get("content") is not None:
        cur_doc = await get_document(id)
        if cur_doc is None:
            return None
        parsed = parse_text(
            fields["content"], mime_type=cur_doc.mime_type, filename=cur_doc.source_uri
        )
    async with connection() as conn:
        try:
            async with conn.transaction():
                cur = await conn.execute(
                    "select id, collection_id from documents where id = %s for update", (id,)
                )
                row = await cur.fetchone()
                if row is None:
                    return None
                sets: list[LiteralString] = []
                params: list[Any] = []
                if fields.get("title") is not None:
                    sets.append("title = %s")
                    params.append(fields["title"].strip()[:500] or "Untitled")
                if fields.get("tags") is not None:
                    sets.append("tags = %s")
                    params.append(_clean_tags(fields["tags"]))
                if fields.get("metadata") is not None:
                    sets.append("metadata = %s")
                    params.append(Jsonb(fields["metadata"]))
                new_coll = None
                if fields.get("collection") is not None:
                    new_coll = await _collection_id(conn, fields["collection"])
                    if new_coll != row["collection_id"]:
                        sets.append("collection_id = %s")
                        params.append(new_coll)
                    else:
                        new_coll = None
                if parsed is not None:
                    sets.extend(
                        ("body = %s", "content_hash = %s", "status = 'pending'", "error = null")
                    )
                    params += [parsed.markdown, content_hash(parsed.markdown)]
                if sets:
                    await conn.execute(
                        f"update documents set {', '.join(sets)} where id = %s",
                        [*params, id],
                    )
                if new_coll is not None:
                    await conn.execute(f"set local statement_timeout = '{LONG_STATEMENT_TIMEOUT}'")
                    await conn.execute(
                        "update chunks set collection_id = %s where document_id = %s",
                        (new_coll, id),
                    )
                if parsed is not None:
                    await _enqueue_ingest(conn, id)
        except errors.UniqueViolation as exc:
            raise ConflictError(
                "a document with the same content or source_uri already exists in that collection"
            ) from exc
    return await get_document(id)


async def delete_document(id: UUID) -> bool:
    async with connection() as conn, conn.transaction():
        cur = await conn.execute("delete from documents where id = %s returning id", (id,))
        deleted = await cur.fetchone() is not None
        if deleted:
            await conn.execute(
                "delete from jobs where status = 'queued' and payload->>'document_id' = %s",
                (str(id),),
            )
    return deleted


async def reindex_document(id: UUID) -> IngestAccepted | None:
    async with connection() as conn, conn.transaction():
        cur = await conn.execute(
            """
            update documents set status = 'pending', error = null
            where id = %s and body <> '' returning id, status
            """,
            (id,),
        )
        row = await cur.fetchone()
        if row is None:
            cur = await conn.execute("select id, status from documents where id = %s", (id,))
            existing = await cur.fetchone()
            # Failed uploads keep no text to re-ingest.
            return IngestAccepted(id=id, status=existing["status"]) if existing else None
        await _enqueue_ingest(conn, id)
    return IngestAccepted(id=id, status="pending")


async def get_chunks(document_id: UUID) -> list[dict[str, Any]]:
    async with connection() as conn:
        cur = await conn.execute(
            """
            select id, ord, heading_path, content, context, token_count, char_start, char_end,
                   embedding is not null as embedded
            from chunks where document_id = %s order by ord
            """,
            (document_id,),
        )
        return list(await cur.fetchall())


# --- collections -----------------------------------------------------------------------


_COLLECTION_SQL = """
    select c.id, c.slug, c.name, c.description, c.created_at,
           (select count(*) from documents d where d.collection_id = c.id)::int as document_count
    from collections c
"""


async def list_collections() -> list[Collection]:
    async with connection() as conn:
        cur = await conn.execute(_COLLECTION_SQL + " order by c.slug = 'default' desc, c.name")
        return [Collection.model_validate(r) for r in await cur.fetchall()]


async def get_collection(slug: str) -> Collection | None:
    async with connection() as conn:
        cur = await conn.execute(_COLLECTION_SQL + " where c.slug = %s", (slug,))
        row = await cur.fetchone()
    return Collection.model_validate(row) if row else None


async def create_collection(c: CollectionIn) -> Collection:
    async with connection() as conn:
        try:
            cur = await conn.execute(
                """
                insert into collections (slug, name, description) values (%s, %s, %s)
                returning id, slug, name, description, created_at, 0 as document_count
                """,
                (c.slug, c.name, c.description),
            )
        except errors.UniqueViolation as exc:
            raise ConflictError(f"collection {c.slug!r} already exists") from exc
        return Collection.model_validate(await cur.fetchone())


async def delete_collection(slug: str) -> bool:
    """Delete a collection and (by cascade) all of its documents and chunks."""
    if slug == "default":
        raise ConflictError("the default collection cannot be deleted")
    async with connection() as conn, conn.transaction():
        cur = await conn.execute("delete from collections where slug = %s returning id", (slug,))
        row = await cur.fetchone()
    return row is not None


__all__ = [
    "ConflictError",
    "UnknownCollectionError",
    "create_collection",
    "create_document",
    "create_from_upload",
    "delete_collection",
    "delete_document",
    "get_chunks",
    "get_collection",
    "get_document",
    "list_collections",
    "list_documents",
    "normalize_text",
    "reindex_document",
    "update_document",
]
