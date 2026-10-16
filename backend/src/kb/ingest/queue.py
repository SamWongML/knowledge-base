"""Durable Postgres job queue on the ``jobs`` table.

* ``enqueue`` runs on the caller's connection, so a job is committed atomically with the
  row it refers to (transactional outbox). ``dedup_key`` collapses identical *queued*
  jobs; the key is cleared when a job is claimed, so new work for the same document can
  be queued while an older run is still in flight (the pipeline detects stale runs).
* ``claim`` uses ``FOR UPDATE SKIP LOCKED`` so any number of workers never double-claim.
* Running jobs hold a lease (``lease_until``) that a heartbeat extends; leases of crashed
  workers expire and ``reclaim_expired`` puts those jobs back in the queue.
* Failures retry with exponential backoff and jitter until ``max_attempts``.
* ``pg_notify('kb_jobs', kind)`` wakes idle workers; they also poll as a fallback.
"""

from __future__ import annotations

import json
import os
import random
import socket
import uuid
from dataclasses import dataclass
from typing import Any

from kb.config import get_settings
from kb.db import Conn

CHANNEL = "kb_jobs"
INGEST_DOCUMENT = "ingest_document"
REEMBED_DOCUMENT = "reembed_document"

BACKOFF_BASE_S = 5.0
BACKOFF_CAP_S = 3600.0


def make_worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"


@dataclass(frozen=True, slots=True)
class Job:
    id: int
    kind: str
    payload: dict[str, Any]
    attempts: int
    max_attempts: int
    locked_by: str

    @property
    def final_attempt(self) -> bool:
        return self.attempts >= self.max_attempts


def backoff_seconds(attempts: int) -> float:
    """Exponential backoff with ±25% jitter: 5s, 10s, 20s … capped at an hour."""
    delay = min(BACKOFF_CAP_S, BACKOFF_BASE_S * 2 ** max(0, attempts - 1))
    return delay * random.uniform(0.75, 1.25)


async def enqueue(
    conn: Conn,
    kind: str,
    payload: dict[str, Any],
    *,
    dedup_key: str | None = None,
    delay_s: float = 0.0,
    priority: int = 0,
    max_attempts: int | None = None,
) -> int | None:
    """Queue a job; returns its id, or None when an identical job is already queued."""
    cur = await conn.execute(
        """
        insert into jobs (kind, payload, dedup_key, priority, max_attempts, run_after)
        values (%s, %s, %s, %s, %s, now() + make_interval(secs => %s))
        on conflict (dedup_key) where dedup_key is not null and status in ('queued', 'running')
        do nothing
        returning id
        """,
        (
            kind,
            json.dumps(payload),
            dedup_key,
            priority,
            max_attempts or get_settings().job_max_attempts,
            delay_s,
        ),
    )
    row = await cur.fetchone()
    if row is None:
        return None
    # Delivered on commit when called inside a transaction.
    await conn.execute("select pg_notify(%s, %s)", (CHANNEL, kind))
    return _get(row, "id")


def _get(row: Any, key: str) -> Any:
    return row[key] if isinstance(row, dict) else row[0]


async def claim(
    conn: Conn,
    worker_id: str,
    *,
    kinds: list[str] | None = None,
    lease_s: int | None = None,
) -> Job | None:
    """Atomically take the next due job (highest priority, oldest first) or return None."""
    lease = lease_s or get_settings().job_lease_seconds
    cur = await conn.execute(
        """
        with next as (
            select id from jobs
            where status = 'queued' and run_after <= now()
              and (%(kinds)s::text[] is null or kind = any(%(kinds)s::text[]))
            order by priority desc, run_after, id
            for update skip locked
            limit 1
        )
        update jobs j
        set status = 'running', attempts = j.attempts + 1, locked_by = %(worker)s,
            lease_until = now() + make_interval(secs => %(lease)s), dedup_key = null
        from next
        where j.id = next.id
        returning j.id, j.kind, j.payload, j.attempts, j.max_attempts, j.locked_by
        """,
        {"kinds": kinds, "worker": worker_id, "lease": lease},
    )
    row = await cur.fetchone()
    if row is None:
        return None
    return Job(
        id=row["id"],
        kind=row["kind"],
        payload=row["payload"],
        attempts=row["attempts"],
        max_attempts=row["max_attempts"],
        locked_by=row["locked_by"],
    )


async def heartbeat(conn: Conn, job: Job, *, lease_s: int | None = None) -> bool:
    """Extend the lease. False means the lease was lost (expired and reclaimed elsewhere)."""
    lease = lease_s or get_settings().job_lease_seconds
    cur = await conn.execute(
        """
        update jobs set lease_until = now() + make_interval(secs => %s)
        where id = %s and status = 'running' and locked_by = %s
        """,
        (lease, job.id, job.locked_by),
    )
    return cur.rowcount == 1


async def complete(conn: Conn, job: Job) -> None:
    await conn.execute(
        """
        update jobs set status = 'done', lease_until = null, last_error = null
        where id = %s and locked_by = %s and status = 'running'
        """,
        (job.id, job.locked_by),
    )


async def fail(conn: Conn, job: Job, error: str) -> bool:
    """Record a failure: re-queue with backoff, or mark failed. Returns True if final."""
    final = job.final_attempt
    await conn.execute(
        """
        update jobs
        set status = case when %(final)s then 'failed' else 'queued' end,
            run_after = now() + make_interval(secs => %(delay)s),
            locked_by = null, lease_until = null, last_error = %(error)s
        where id = %(id)s and locked_by = %(worker)s and status = 'running'
        """,
        {
            "final": final,
            "delay": 0 if final else backoff_seconds(job.attempts),
            "error": error[:4000],
            "id": job.id,
            "worker": job.locked_by,
        },
    )
    return final


async def release(conn: Conn, job: Job, *, delay_s: float = 0.0) -> None:
    """Hand a job back without counting the attempt (shutdown, resource busy)."""
    await conn.execute(
        """
        update jobs
        set status = 'queued', attempts = greatest(attempts - 1, 0),
            run_after = now() + make_interval(secs => %s), locked_by = null, lease_until = null
        where id = %s and locked_by = %s and status = 'running'
        """,
        (delay_s, job.id, job.locked_by),
    )
    await conn.execute("select pg_notify(%s, %s)", (CHANNEL, job.kind))


async def reclaim_expired(conn: Conn) -> int:
    """Re-queue (or fail, when out of attempts) jobs whose worker stopped heartbeating."""
    cur = await conn.execute(
        """
        update jobs
        set status = case when attempts >= max_attempts then 'failed' else 'queued' end,
            run_after = now(), locked_by = null, lease_until = null,
            last_error = coalesce(last_error || '; ', '') || 'lease expired (worker lost)'
        where status = 'running' and lease_until < now()
        """
    )
    if cur.rowcount:
        await conn.execute("select pg_notify(%s, %s)", (CHANNEL, "reclaimed"))
    return cur.rowcount


async def prune(conn: Conn, *, keep_days: int = 7) -> int:
    """Delete finished jobs older than ``keep_days`` (failed jobs are kept for inspection)."""
    cur = await conn.execute(
        "delete from jobs where status = 'done' and updated_at < now() - make_interval(days => %s)",
        (keep_days,),
    )
    return cur.rowcount
