# 03: Storage backend, job queue, API stack and single-node HA (research as of 2026-10-16)

Scope: a personal or small-team knowledge base on one Apple Silicon Mac mini, with concurrent ingestion (parse, chunk, embed), concurrent hybrid queries and a Python backend. Versions below come from PyPI and Docker Hub as of 2026-10-16. Where I could not reach a primary source (GitHub was blocked from this session), I say so.

## 1. Store

### Recommendation: PostgreSQL 18 + pgvector + ParadeDB pg_search in one database

**Why one database is the right call here.** Metadata, chunks, vectors and the BM25 index all commit in the same ACID transaction. A chunk is never searchable by BM25 while missing its vector, and deleting a document removes everything through `ON DELETE CASCADE`. The ingestion job queue can live in the same database too (section 2), so a single `pg_dump` or pgBackRest stanza backs up the whole system. On one box, a second datastore adds a second failure domain and a consistency problem (dual writes) without adding capacity. ParadeDB makes the same argument: its indexes run "in your existing database with ACID guarantees and transactional consistency" ([ParadeDB hybrid manual](https://paradedb.com/blog/hybrid-search-in-postgresql-the-missing-manual)).

**Concurrency.** Postgres MVCC means readers never block writers, and many ingestion workers can insert at once. HNSW inserts are the expensive part. Each insert walks the graph and updates neighbour lists, at a cost driven by `m` (default 16) and `ef_construction` (default 64): "a higher value of ef_construction provides better recall at the cost of index build time / insert speed" ([pgvector 0.8.5 HNSW docs](https://docs.pgedge.com/pgvector/v0-8-5/hnsw/)). Mitigations:
- Batch inserts per document, using `COPY` or `executemany` in a single transaction.
- Keep the defaults for `m` and `ef_construction`.
- Use `halfvec` (up to 4,000 dims) to halve index size and WAL.
- For bulk re-embeds, drop the index, load, then `CREATE INDEX CONCURRENTLY` with a high `maintenance_work_mem` and `max_parallel_maintenance_workers`.
- Use pgvector ≥ 0.8.2 because of CVE-2026-3172, a buffer overflow in parallel HNSW builds in 0.6.0 through 0.8.1 ([thebuild.com](https://thebuild.com/blog/on-pgvectorscale-and-hybrid-search-without-an-elasticsearch-sidecar/)).
- pgvector 0.8's iterative index scans (`SET hnsw.iterative_scan = relaxed_order`) fix the "filtered kNN returns too few rows" problem when filtering by collection ([pgvector 0.8.0 release](https://www.postgresql.org/about/news/pgvector-080-released-2952/)).

At personal scale (≤ a few million chunks of 768 to 1024 dims), the HNSW graph fits in a 32 to 64 GB Mac mini's RAM, so HNSW is the right index.

**PostgreSQL 18** (released 2025-09-25) adds the following ([Neon summary](https://neon.com/postgresql/postgresql-18-new-features)):
- Asynchronous I/O. `io_method=worker` is the portable mode; on macOS you get `worker`, not `io_uring`.
- Native `uuidv7()`, which gives time-ordered primary keys and B-tree locality.
- Skip scan.
- `pg_upgrade` keeps planner statistics.

PG18 is also the default in the ParadeDB image. Choose 18 over 17.

**BM25: ParadeDB pg_search 0.26.x.**
- Index syntax changed in 0.25: it is now `CREATE INDEX ... USING paradedb (...)`. `USING bm25` remains an alias, and `key_field` is "deprecated and ignored".
- Tokenizers are set with casts such as `content::pdb.simple('stemmer=english')`.
- Only one ParadeDB index is allowed per table ([create-index docs](https://www.paradedb.com/docs/documentation/indexing/create-index.md)).
- Queries use `|||` (match-any) with `pdb.score(id)` ([score docs](https://www.paradedb.com/docs/documentation/sorting/score.md)).

**macOS arm64 native.** Yes. ParadeDB now ships prebuilt `.pkg` installers for Apple Silicon only:
- macOS 15 Sequoia and 26 Tahoe.
- Postgres.app, PG 18 only.
- Example: `pg_search@18--0.26.2.arm64_tahoe.pkg`.

It requires `shared_preload_libraries='pg_search'` plus a restart. The docs also state that pgvector must already be installed, and you enable it with `CREATE EXTENSION pg_search CASCADE` ([deploy/extension docs](https://www.paradedb.com/docs/operate/deploy/self-hosted/extension.md)). Postgres.app bundles pgvector, so **Postgres.app 18 + the pg_search pkg** is the most native route. The other route is Docker or OrbStack with `paradedb/paradedb:0.26.2-pg18`, which matches CI exactly. Homebrew is not listed as a target.

### Options considered

| Option | Verdict |
|---|---|
| **pgvectorscale (DiskANN)** | Only helps when the index exceeds RAM: ~9× lower p95 and 14× smaller than HNSW at 1M×1536 in 3.8 GB RAM, while "HNSW is still faster when the whole index fits in RAM". Needs a Rust/pgrx build, and the published benchmarks are read-only ([thebuild.com](https://thebuild.com/blog/on-pgvectorscale-and-hybrid-search-without-an-elasticsearch-sidecar/)). Skip it for now; it is an easy later add-on. |
| **VectorChord 1.1 (vchord)** | Claims 16× higher insert throughput and faster builds than pgvector HNSW, and is pgvector-type compatible. However, it is AGPLv3/ELv2 and its macOS story is unclear (Docker image `tensorchord/vchord-postgres`) ([pgxn](https://pgxn.org/dist/vchord/1.1.0/)). Revisit it if HNSW insert cost becomes the bottleneck. A migration only swaps the index DDL. |
| **Qdrant** | Excellent vector engine with good concurrent upserts. It is a second datastore, though: dual-write consistency problems, separate backups and weaker BM25/metadata joins. Overkill for one box. |
| **LanceDB** | Embedded with strong reads, but "too many concurrent writers can lead to failing writes" (optimistic commit retries), each insert creates a fragment that needs compaction, and fork-based multiprocessing is unsafe ([LanceDB FAQ](https://docs.lancedb.com/faq/faq-oss)). A poor fit for many concurrent ingesters. |
| **Milvus Lite** | Intended for prototyping in a single process, not for concurrent multi-process services. |
| **SQLite + sqlite-vec + FTS5** | Simplest operations, but has a single writer (WAL mode serialises writes) and sqlite-vec's ANN is less mature. Good for a laptop demo, weak for "many concurrent insertions". |
| **Elasticsearch/OpenSearch** | Best-in-class BM25, but heavy on a JVM and a second datastore. pg_search gives Tantivy-based BM25 inside Postgres. |
| **Turbopuffer-like** | Object-storage-first designs only pay off on S3 economics; there is no local equivalent worth running. |

**Backup and restore.** WAL archiving plus pgBackRest gives full and differential backups with point-in-time recovery to a local USB/NAS repository and optionally S3. pgBackRest runs on macOS through Homebrew. Inside Docker, the ParadeDB image already ships Barman Cloud ([ParadeDB Dockerfile](https://api.pgxn.org/src/pg_search/pg_search-0.25.1/docker/Dockerfile.paradedb-18)). Add a nightly `pg_dump -Fc` as a logical safety net, and test restores monthly with a scripted restore into a scratch cluster. BM25 and HNSW indexes are WAL-logged, so physical backups include them.

## 2. Ingestion job queue

**Recommendation: Procrastinate 3.10 (psycopg3, async) in the same Postgres.** Rationale:
- No Redis to supervise.
- Enqueueing happens in the same transaction that inserts the `documents` row, which is the transactional outbox for free.
- Workers wake up via LISTEN/NOTIFY with a polling fallback.
- Built-in retry strategies with exponential backoff.
- `lock` gives per-key serialisation, so only one job per document runs at a time.
- `queueing_lock` gives dedupe of pending jobs.
- Supports Postgres ≥ 13 and Python ≥ 3.10 ([docs](https://procrastinate.readthedocs.io/en/stable/discussions.html), [PyPI](https://pypi.org/pypi/procrastinate)).

**Alternatives:**
- **pgqueuer 1.6.0** (2026-10-11): asyncpg or psycopg drivers, per-entrypoint concurrency limits, cron and `execute_after` scheduling ([PyPI](https://pypi.org/project/pgqueuer)). It is lighter, and a fine substitute if you prefer asyncpg.
- **DBOS 3.3**: a library-only durable-workflow engine checkpointing to Postgres, with queue concurrency and rate limits ([architecture](https://docs.dbos.dev/architecture)). Attractive if ingestion grows into multi-step workflows such as OCR, summarise, embed and re-embed. It costs about one database write per step.
- **Hatchet / Temporal**: separate servers, too much for one box.
- **arq / Dramatiq / Celery / Taskiq**: need Redis or RabbitMQ, which means a second stateful service with its own persistence and HA concerns, and no transactional enqueue.

**Idempotency, dedup and backpressure:**
- `documents.content_sha256` carries a `UNIQUE (collection_id, content_sha256)` constraint, and ingestion uses `INSERT ... ON CONFLICT DO NOTHING RETURNING id`, so duplicate uploads become no-ops.
- Jobs are keyed by `document_id` and `pipeline_version` through Procrastinate `queueing_lock=f"doc:{id}"`.
- Each stage is idempotent. Chunks are replaced with `DELETE ... WHERE document_id=$1` followed by inserts in one transaction, and embeddings are cached by `(model, chunk_sha256)` so retries do not re-embed.
- For backpressure:
  - Cap worker concurrency to the embedder's capacity.
  - Use a separate `embed` queue with concurrency N.
  - Have the API return `429`/`503 Retry-After` when the count of pending jobs exceeds a threshold.
  - Bound the in-memory batch size.

## 3. Python API stack

| Component | Recommendation | Notes |
|---|---|---|
| Framework | **FastAPI 0.143** | Largest ecosystem; Pydantic v2 native. Litestar 2.24 is technically excellent but sees slower releases (last release June 2026) and has a smaller community. |
| Driver | **psycopg 3.3.6 + psycopg-pool 3.3.3 (async)** | The `pgvector` Python package (0.5.1) registers vector types for psycopg. Procrastinate uses psycopg too, so there is one driver and one pool style. asyncpg 0.32 is faster in micro-benchmarks, but that rarely matters next to embedding latency. |
| Data access | **Raw SQL for search; SQLAlchemy 2.1 Core (optional) for CRUD** | Hybrid search is a hand-tuned CTE (BM25 + kNN + RRF), and ORMs add little there. Alembic or plain numbered SQL files handle migrations. |
| Server | **Granian 2.8 (`--workers N --respawn-failed-workers`)** or **Uvicorn 0.54 (`--workers N`)** | Both supervise worker processes. Granian's Rust I/O and built-in respawn suit the Mac mini; uvicorn is the conservative fallback. |

Request hygiene:
- A per-request `statement_timeout` is set with `SET LOCAL` in the query transaction, for example 2 s for search.
- Set `idle_in_transaction_session_timeout=30s` at the role level.
- Use an asyncio `timeout()` around outbound model calls.
- Rate limiting can use an in-process token bucket per API token (or `slowapi`), since there is one host. Set a request body size limit for uploads.

## 4. "HA" on a single node

**Supervision.**
- Native installs use launchd `LaunchDaemons`, one plist per role (postgres, api, worker, embedder), with `KeepAlive=true` and `ThrottleInterval`. Postgres.app can auto-start itself.
- On Docker or OrbStack, use `restart: unless-stopped` plus `healthcheck` (with `pg_isready`) and `depends_on: condition: service_healthy`.
- Disable sleep with `pmset -a sleep 0 disablesleep 1` and enable "Start up automatically after a power failure". A small UPS is the cheapest HA upgrade.

**Process model.**
- Run 2 to 4 API workers and separate ingestion worker processes, so ingestion CPU and memory spikes never take down queries.
- Run the embedder or model server as its own process, for example llama.cpp, Ollama or an MLX server.
- Use separate connection pools and Postgres roles for `api` and `ingest`. Give the ingest role a lower `work_mem` and connection cap so ingestion cannot starve queries.

**Graceful shutdown and health.**
- On SIGTERM: stop accepting requests, drain in-flight requests, and close pools.
- Procrastinate workers finish their current job on shutdown. Jobs from killed workers are retried through stalled-job recovery.
- `/livez` checks only that the process is alive.
- `/readyz` checks the database ping, migrations and queue-depth thresholds, and reports embedder status as *degraded* rather than failing.

**Degraded modes.**
- Wrap embedder calls in a circuit breaker (a small hand-rolled state machine or `aiobreaker`).
- When the breaker is open, query requests fall back to **BM25-only** with `degraded: ["semantic"]` in the response. Ingestion jobs keep retrying with backoff, and chunks stay BM25-searchable before their vectors land (`embedding` is nullable, and `embedded_at` is tracked).
- Avoid long transactions:
  - Never hold a transaction open across parsing or embedding calls.
  - Parse and embed outside any transaction, then write.
  - Long transactions block vacuum and bloat the HNSW and BM25 indexes.

**Optional streaming replica.** A second Postgres cluster on the same Mac (another port and disk) or on a second cheap box can serve as a hot standby. It is useful for zero-downtime major upgrades and as a read target, but it does not protect against the host failing. Prefer pgBackRest PITR to an external disk first.

## 5. Real-time updates to the UI

- Ingestion stages write to `ingest_events` (or update `documents.status`) and call `pg_notify('ingest', json)` in the same transaction.
- The API holds **one** dedicated `LISTEN` connection per worker process (not from the pool) and fans out to clients over **SSE**, using `sse-starlette` 3.5 or FastAPI's `StreamingResponse`.
- SSE is enough because updates are server-to-client only. It reconnects automatically, and `Last-Event-ID` can replay from `ingest_events.id`.
- NOTIFY payloads are capped at 8 KB, so send only IDs and status.
- WebSockets are only needed for bidirectional features such as collaborative editing.

## 6. Multi-tenancy-lite

- Use `collections` (spaces) as the top-level scope, and put `collection_id` on every row.
- Tags live in a `text[]` column with a GIN index, and are also indexed inside the BM25 index for filter pushdown.
- `api_tokens` store a SHA-256 hash, a name, scopes (`read`/`write`/`admin`), optional `collection_ids` and `last_used_at`; send them as `Authorization: Bearer`.
- No ACLs beyond token scopes. Row-level security can be added later by keying on `collection_id`.

---

## RECOMMENDED ARCHITECTURE

**Components (pin these):**
- **Database:**
  - CI and Docker: `paradedb/paradedb:0.26.2-pg18`, which is PostgreSQL 18 + pg_search 0.26.2 + pgvector (0.8.4 in the 0.25.x Dockerfile; assert ≥ 0.8.2 at startup) + pg_cron.
  - Mac-native: Postgres.app (PG 18) + `pg_search@18--0.26.2.arm64_<sequoia|tahoe>.pkg`.
- **Python 3.12/3.13:** FastAPI 0.143, Pydantic v2, psycopg 3.3 + psycopg-pool 3.3, pgvector-python 0.5.1, Procrastinate 3.10, Granian 2.8 (or Uvicorn 0.54), sse-starlette 3.5.
- **Processes under launchd or compose:**
  - `postgres`
  - `api` (Granian, 3 workers)
  - `ingest-worker` (Procrastinate, queues `parse`, `embed`)
  - `embedder` (local model server)
  - `backup` (pgBackRest cron)

**Schema sketch:**
```sql
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_search;

CREATE TABLE collections (
  id uuid PRIMARY KEY DEFAULT uuidv7(),
  slug text UNIQUE NOT NULL, name text NOT NULL,
  embed_model text NOT NULL, embed_dim int NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now());

CREATE TABLE api_tokens (
  id uuid PRIMARY KEY DEFAULT uuidv7(),
  token_sha256 bytea UNIQUE NOT NULL, name text NOT NULL,
  scopes text[] NOT NULL DEFAULT '{read}', collection_ids uuid[],
  last_used_at timestamptz, revoked_at timestamptz);

CREATE TABLE documents (
  id uuid PRIMARY KEY DEFAULT uuidv7(),
  collection_id uuid NOT NULL REFERENCES collections ON DELETE CASCADE,
  source_uri text, title text, mime text,
  content_sha256 bytea NOT NULL,
  tags text[] NOT NULL DEFAULT '{}',
  status text NOT NULL DEFAULT 'pending'  -- pending|parsing|embedding|ready|failed
    CHECK (status IN ('pending','parsing','embedding','ready','failed')),
  error text, pipeline_version int NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (collection_id, content_sha256));
CREATE INDEX documents_tags_gin ON documents USING gin (tags);

CREATE TABLE chunks (
  id uuid PRIMARY KEY DEFAULT uuidv7(),
  document_id uuid NOT NULL REFERENCES documents ON DELETE CASCADE,
  collection_id uuid NOT NULL,             -- denormalised for filter pushdown
  ord int NOT NULL, content text NOT NULL, heading_path text,
  tags text[] NOT NULL DEFAULT '{}',
  chunk_sha256 bytea NOT NULL,
  embedding halfvec(1024),                 -- nullable: BM25-searchable before embedding
  embedded_at timestamptz,
  UNIQUE (document_id, ord));

-- Vector index (cosine); per-query: SET LOCAL hnsw.ef_search = 100; SET LOCAL hnsw.iterative_scan = relaxed_order;
CREATE INDEX CONCURRENTLY chunks_embedding_hnsw
  ON chunks USING hnsw (embedding halfvec_cosine_ops) WITH (m = 16, ef_construction = 64);

-- BM25 (one ParadeDB index per table; include filter columns for pushdown)
CREATE INDEX chunks_bm25 ON chunks USING paradedb (
  id,
  (content::pdb.simple('stemmer=english')),
  (heading_path::pdb.simple('stemmer=english')),
  collection_id, tags);

CREATE TABLE embedding_cache (
  model text NOT NULL, chunk_sha256 bytea NOT NULL, embedding halfvec NOT NULL,
  PRIMARY KEY (model, chunk_sha256));

CREATE TABLE ingest_events (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  document_id uuid NOT NULL REFERENCES documents ON DELETE CASCADE,
  stage text NOT NULL, detail jsonb, at timestamptz NOT NULL DEFAULT now());
-- Procrastinate creates its own procrastinate_* tables via `procrastinate schema --apply`.
```

**Hybrid query (RRF, k=60):**
```sql
WITH bm AS (
  SELECT id, ROW_NUMBER() OVER (ORDER BY pdb.score(id) DESC) r FROM chunks
  WHERE content ||| $1 AND collection_id = $2 ORDER BY pdb.score(id) DESC LIMIT 50),
vec AS (
  SELECT id, ROW_NUMBER() OVER (ORDER BY embedding <=> $3) r FROM chunks
  WHERE collection_id = $2 AND embedding IS NOT NULL ORDER BY embedding <=> $3 LIMIT 50)
SELECT c.*, s.score FROM (
  SELECT id, SUM(1.0/(60+r)) score FROM (SELECT * FROM bm UNION ALL SELECT * FROM vec) u GROUP BY id
) s JOIN chunks c USING (id) ORDER BY s.score DESC LIMIT 10;
```
Degraded mode runs only the `bm` CTE.

**CI testability (GitHub Actions, Linux):**
```yaml
services:
  postgres:
    image: paradedb/paradedb:0.26.2-pg18
    env: { POSTGRES_USER: kb, POSTGRES_PASSWORD: kb, POSTGRES_DB: kb_test }
    ports: ['5432:5432']
    options: >-
      --health-cmd "pg_isready -U kb" --health-interval 5s --health-timeout 5s --health-retries 20
```
- The ParadeDB image is built `FROM postgres:18-trixie` and installs `postgresql-18-pgvector` from PGDG alongside pg_search, pg_cron and PostGIS, with `shared_preload_libraries='pg_search,pg_cron,pg_stat_statements'` preset ([Dockerfile.paradedb-18](https://api.pgxn.org/src/pg_search/pg_search-0.25.1/docker/Dockerfile.paradedb-18)). **pgvector is bundled**, and `CREATE EXTENSION vector; CREATE EXTENSION pg_search;` work out of the box.
- Docker Hub tags confirmed on 2026-10-16: `0.26.2-pg18`, `18-v0.26.2`, `latest-pg18`, and `pg15` to `pg17` variants.
- Pin the exact version tag (not `latest`) and add a startup or migration test asserting `SELECT extversion FROM pg_extension WHERE extname='vector'` ≥ 0.8.2.
- Use the same image locally via OrbStack so dev, CI and Mac-native installs share PG 18 + pg_search 0.26.2.
- Tests:
  - Run migrations and the hybrid query against seeded fixtures.
  - Run Procrastinate in-process with `run_worker_async(wait=False)` for deterministic job draining.
  - Fake the embedder with a deterministic hash-to-vector function.
  - Run a concurrency test of N parallel ingests of the same file, asserting exactly one document.

**Sources:** [ParadeDB extension install](https://www.paradedb.com/docs/operate/deploy/self-hosted/extension.md) · [ParadeDB create index](https://www.paradedb.com/docs/documentation/indexing/create-index.md) · [ParadeDB score](https://www.paradedb.com/docs/documentation/sorting/score.md) · [Hybrid search manual](https://paradedb.com/blog/hybrid-search-in-postgresql-the-missing-manual) · [ParadeDB Docker install](https://api.pgxn.org/src/pg_search/pg_search-0.25.5/docs/documentation/getting-started/install.mdx) · [pgvector HNSW docs](https://docs.pgedge.com/pgvector/v0-8-5/hnsw/) · [pgvector 0.8.0](https://www.postgresql.org/about/news/pgvector-080-released-2952/) · [pgvectorscale/hybrid](https://thebuild.com/blog/on-pgvectorscale-and-hybrid-search-without-an-elasticsearch-sidecar/) · [VectorChord](https://pgxn.org/dist/vchord/1.1.0/) · [LanceDB FAQ](https://docs.lancedb.com/faq/faq-oss) · [PG18 features](https://neon.com/postgresql/postgresql-18-new-features) · [Procrastinate](https://procrastinate.readthedocs.io/en/stable/discussions.html) · [pgqueuer](https://pypi.org/project/pgqueuer) · [DBOS](https://docs.dbos.dev/architecture) · [Granian benchmarks](https://github.com/emmett-framework/granian/blob/master/benchmarks/vs.md)
