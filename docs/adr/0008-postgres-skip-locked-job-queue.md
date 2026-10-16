# 0008. Durable job queue on Postgres with SKIP LOCKED instead of Procrastinate

- Status: accepted (deviates from the storage research recommendation)
- Date: 2026-10-16
- Related: [raw/03 §2](../research/raw/03-storage-backend.md), [ADR 0001](0001-single-postgres.md), `jobs` table in `0001_init.sql`, `kb.ingest.worker`

## Context

Ingestion (parse, chunk, optionally contextualize, embed, write) is asynchronous and must:
- survive process crashes and restarts;
- run with several concurrent workers;
- never lose or duplicate work;
- be enqueued atomically with the `documents` row.

The storage research recommended **Procrastinate 3.10** on the same Postgres, citing transactional enqueue, LISTEN/NOTIFY wake-ups, retries, per-key locks and queueing locks. We need only one job kind family, at modest volume.

## Decision

Use a small built-in queue: one `jobs` table, drained with `SELECT … FOR UPDATE SKIP LOCKED`.

- **Columns:** `kind`, `payload jsonb`, `status` (queued, running, done, failed), `priority`, `attempts` / `max_attempts` (default 5), `run_after`, `locked_by`, `lease_until`, `last_error`, `dedup_key`.
- **Claim:** in one short transaction, select the next ready job (`status='queued' and run_after <= now()`, ordered by `priority desc, run_after, id`, served by the `jobs_ready_idx` partial index) `for update skip locked`, then set `running`, `locked_by` and `lease_until = now() + KB_JOB_LEASE_SECONDS` (300).
- **Leases:** a job whose lease expired is reclaimable (`jobs_lease_idx`), so a crashed worker's job is picked up again. Jobs that may outlive the lease should extend it.
- **Retries:** on failure, `attempts += 1` and `run_after = now() + backoff`. At `max_attempts` the job is marked `failed` and the document's `status='failed'` with `error` set.
- **Dedup:** a partial unique index on `dedup_key` for queued or running jobs means a document has at most one pending job. Re-enqueueing is an `on conflict do nothing`.
- **Transactional enqueue:** the job row is inserted in the same transaction as the document upsert. This is the outbox pattern for free.
- **Idempotent work:** chunk replacement is delete-then-insert in one transaction, so at-least-once delivery is safe.
- **Wake-ups:** idle workers poll. A NOTIFY wake-up is a cheap later addition.
- **Testing:** `drain_jobs()` processes the queue inline, so tests are deterministic.

## Alternatives considered

| Option | Trade-off |
|---|---|
| **Procrastinate** (research pick) | Mature, with retries, locks, periodic tasks, an admin CLI and stalled-job recovery. But it is an extra dependency with its own schema (`procrastinate_*`) and migration step, its own worker runtime and connector, and its own abstractions to learn. That is more than we need for a few job kinds. |
| pgqueuer | Lighter, but still a dependency and schema, and asyncpg-first. |
| DBOS | Durable workflows are attractive if ingestion grows into multi-step pipelines, at the cost of one database write per step and a framework. |
| Redis-based (arq, Dramatiq, Celery, Taskiq) | A second stateful service, and no transactional enqueue. |
| In-process asyncio tasks | Lost on restart. |

## Consequences

- No new dependency, one schema, and full control over the claim, lease and retry semantics, all in one table and one module.
- We own the code for leases, backoff, dead-lettering (`status='failed'`), and pruning old `done` rows. A periodic `delete from jobs where status='done' and updated_at < now() - interval '7 days'` keeps the table small.
- No built-in scheduler, admin UI or per-key concurrency limits beyond dedup. `/api/stats` exposes queue depth.
- `SKIP LOCKED` scales well past our needs (thousands of jobs per second on one node). Bloat from churn is handled by autovacuum on a small table.
- Revisit if: we need cron-style schedules, workflows with many steps, or per-tenant rate limits. Then Procrastinate or DBOS become worth their weight, and the `jobs` table maps onto either.
