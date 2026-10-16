# 0001. One PostgreSQL 18 database for metadata, vectors, BM25, queue and events

- Status: accepted
- Date: 2026-10-16
- Related: [research §3](../research/README.md#3-storage-job-queue-and-api-stack), [raw/03](../research/raw/03-storage-backend.md), [raw/01 §1–2](../research/raw/01-retrieval-reranking.md), `backend/src/kb/migrations/0001_init.sql`, [ADR 0008](0008-postgres-skip-locked-job-queue.md)

## Context

The kb runs on one Mac mini. It must handle many concurrent ingestions alongside interactive hybrid queries, and it must survive restarts without losing work. The corpus is personal or small-team scale, at most a few million chunks, so a 1024-d `halfvec` HNSW graph fits in RAM. Every extra stateful service is another thing to supervise, back up and keep consistent with the others through dual writes.

Native `tsvector` ranking is not BM25: it scored nDCG@10 of 0.33 against about 0.68 for BM25 on SciFact ([Particula](https://particula.tech/blog/pg-search-vs-pg-textsearch-bm25-postgres)), so a BM25 extension is required.

## Decision

Use one **PostgreSQL 18** database ([`paradedb/paradedb:0.26.2-pg18`](https://hub.docker.com/r/paradedb/paradedb), or Postgres.app 18 with the ParadeDB pkg) for all durable state:

- **Metadata:** `collections`, `documents` and `chunks`, keyed by `uuidv7()` (PG 18 native). Chunk ids are `bigint identity`.
- **Vectors:** pgvector ≥ 0.8.2 (because of CVE-2026-3172). `chunks.embedding halfvec(1024)` with HNSW `halfvec_cosine_ops`, `m=16, ef_construction=128`. Filtered kNN uses iterative scans.
- **Lexical:** ParadeDB **pg_search** 0.26 BM25 index over `id, content, context, heading_path, collection_id, document_id`, with an English stemmer. The filter columns are in the index for pushdown.
- **Queue:** a `jobs` table drained with `SKIP LOCKED` ([ADR 0008](0008-postgres-skip-locked-job-queue.md)).
- **Events:** a trigger calls `pg_notify('kb_events')` on document status changes, which API processes fan out over SSE.
- **Analytics:** `query_log`.

**pg_search rather than pg_textsearch.**
- It is bundled with pgvector in the ParadeDB image, so dev, CI and compose run the same image.
- It installs natively on macOS through the ParadeDB `.pkg` on Postgres.app 18.
- It supports phrase, fuzzy and prefix queries plus `paradedb.snippet` highlighting, which the UI and CLI use.
- Its licence is AGPL-3.0. We run it unmodified in a self-hosted personal deployment, so AGPL's network source-offer obligation (which applies to modified versions) is not triggered.
- The two extensions cannot coexist in one database, because both register `bm25`.

## Alternatives considered

| Option | Why not |
|---|---|
| pg_textsearch (Tiger Data, PostgreSQL licence) | Permissive, and its quality is on par. But it has no phrase or fuzzy search yet, is not in the ParadeDB image, and its macOS packaging is less straightforward. It is the first choice if AGPL ever becomes a problem; switching changes only the index DDL and the query operator. |
| Postgres + Qdrant | Strong vector engine, but dual writes, two backups and weak BM25 or metadata joins. Overkill for one box. |
| LanceDB / Milvus Lite | Concurrent writers fail or retry (LanceDB), or the system is single-process (Milvus Lite). |
| SQLite + sqlite-vec + FTS5 | Single writer and an immature ANN index. |
| Elasticsearch / OpenSearch | Best-in-class BM25, but a JVM and a second datastore. |
| pgvectorscale (DiskANN), VectorChord | Help only when the index exceeds RAM, or when HNSW insert cost dominates. Both are drop-in later via index DDL. |

## Consequences

- One transaction makes a chunk, its vector and its BM25 entry visible together, and `ON DELETE CASCADE` cleans up everything.
- A single `pg_dump` (or a single pgBackRest stanza later) backs up the whole system.
- MVCC lets readers and ingesting writers proceed concurrently.
- HNSW inserts are the most expensive writes. Batch chunk inserts per document, and use `CREATE INDEX CONCURRENTLY` for bulk re-embeds.
- The embedding dimension is fixed at migration time (`{{EMBEDDING_DIM}}`). Changing models requires a migration and a re-embed.
- We take on AGPL compliance (keep pg_search unmodified) and a dependency on ParadeDB's release cadence. Pin the image tag.
- The BM25 index is rebuilt on logical restore. Measure restore time.
- Revisit if: the corpus exceeds about 5M chunks, HNSW insert latency starves ingestion, or the deployment becomes multi-tenant (BM25 statistics leak across RLS boundaries).
