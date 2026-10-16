"""Ingestion worker: N concurrent consumers of the Postgres job queue.

Run standalone with ``kb-worker`` (one process per machine is plenty; scale with
``KB_WORKER_CONCURRENCY``), or inside the API process with ``KB_EMBEDDED_WORKER=true``
via :func:`start_embedded_worker`.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

import structlog
from psycopg import AsyncConnection

from kb.config import get_settings
from kb.db import close_pool, connection, migrate, open_pool
from kb.ingest import pipeline, queue
from kb.ingest.parsers import ParseError

log = structlog.get_logger(__name__)

POLL_INTERVAL_S = 5.0
MAINTENANCE_INTERVAL_S = 30.0
SWEEP_INTERVAL_S = 600.0
SHUTDOWN_GRACE_S = 30.0
BUSY_RETRY_S = 3.0

Handler = Callable[[dict[str, Any]], Awaitable[str]]


async def _ingest(payload: dict[str, Any]) -> str:
    return await pipeline.ingest_document(UUID(payload["document_id"]))


async def _reembed(payload: dict[str, Any]) -> str:
    return await pipeline.reembed_document(UUID(payload["document_id"]))


HANDLERS: dict[str, Handler] = {
    queue.INGEST_DOCUMENT: _ingest,
    queue.REEMBED_DOCUMENT: _reembed,
}


async def process_job(job: queue.Job, *, heartbeat: bool = True) -> str:
    """Run one claimed job to completion and record the outcome in the queue."""
    handler = HANDLERS.get(job.kind)
    hb_task = asyncio.create_task(_heartbeat(job)) if heartbeat else None
    try:
        if handler is None:
            raise ParseError(f"unknown job kind {job.kind!r}")
        outcome = await handler(job.payload)
    except pipeline.DocumentBusy:
        async with connection() as conn:
            await queue.release(conn, job, delay_s=BUSY_RETRY_S)
        return "busy"
    except asyncio.CancelledError:
        # Shutdown mid-job: hand it back so another worker picks it up immediately.
        with contextlib.suppress(Exception):
            async with connection() as conn:
                await queue.release(conn, job)
        raise
    except ParseError as exc:  # permanent: no point retrying
        await _record_failure(job, str(exc), permanent=True)
        return "failed"
    except Exception as exc:
        log.exception("job.error", job_id=job.id, kind=job.kind, attempt=job.attempts)
        await _record_failure(job, f"{type(exc).__name__}: {exc}", permanent=False)
        return "error"
    finally:
        if hb_task is not None:
            hb_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await hb_task
    async with connection() as conn:
        await queue.complete(conn, job)
    log.debug("job.done", job_id=job.id, kind=job.kind, outcome=outcome)
    return outcome


async def _record_failure(job: queue.Job, error: str, *, permanent: bool) -> None:
    async with connection() as conn:
        if permanent:
            job = queue.Job(
                job.id, job.kind, job.payload, job.max_attempts, job.max_attempts, job.locked_by
            )
        final = await queue.fail(conn, job, error)
    if final and job.kind == queue.INGEST_DOCUMENT and "document_id" in job.payload:
        await pipeline.mark_failed(UUID(job.payload["document_id"]), error)


async def _heartbeat(job: queue.Job) -> None:
    interval = max(1.0, get_settings().job_lease_seconds / 3)
    while True:
        await asyncio.sleep(interval)
        try:
            async with connection() as conn:
                if not await queue.heartbeat(conn, job):
                    log.warning("job.lease_lost", job_id=job.id)
                    return
        except Exception as exc:
            log.warning("job.heartbeat_failed", job_id=job.id, error=str(exc))


async def drain_jobs(*, max_jobs: int = 10_000) -> int:
    """Process every due job inline, one at a time; returns how many were run.

    Jobs that become due later (backoff, delayed re-embedding) are left queued.
    """
    worker_id = queue.make_worker_id()
    done = 0
    while done < max_jobs:
        async with connection() as conn:
            job = await queue.claim(conn, worker_id)
        if job is None:
            break
        await process_job(job, heartbeat=False)
        done += 1
    return done


class Worker:
    def __init__(self, concurrency: int, worker_id: str | None = None) -> None:
        self.concurrency = max(1, concurrency)
        self.worker_id = worker_id or queue.make_worker_id()
        self._wake = asyncio.Event()

    async def run(self, stop: asyncio.Event) -> None:
        log.info("worker.start", worker_id=self.worker_id, concurrency=self.concurrency)
        background: list[asyncio.Task[Any]] = [
            asyncio.create_task(self._listen(), name="kb-worker-listen"),
            asyncio.create_task(self._maintenance(), name="kb-worker-maintenance"),
        ]
        consumers = [
            asyncio.create_task(self._consume(stop), name=f"kb-worker-{i}")
            for i in range(self.concurrency)
        ]
        stopper: asyncio.Task[Any] = asyncio.create_task(stop.wait())
        try:
            await asyncio.wait([stopper, *consumers], return_when=asyncio.FIRST_COMPLETED)
            self._wake.set()  # unblock idle consumers so they notice ``stop``
            _, pending = await asyncio.wait(consumers, timeout=SHUTDOWN_GRACE_S)
            for task in pending:  # in-flight jobs past the grace period are released
                task.cancel()
            await asyncio.gather(*consumers, return_exceptions=True)
        finally:
            for task in [*background, stopper, *consumers]:
                task.cancel()
            await asyncio.gather(*background, stopper, return_exceptions=True)  # pyright: ignore[reportCallIssue, reportArgumentType]
            log.info("worker.stopped", worker_id=self.worker_id)

    async def _consume(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            self._wake.clear()  # clear *before* claiming so no notification is lost
            try:
                async with connection() as conn:
                    job = await queue.claim(conn, self.worker_id)
            except Exception as exc:
                log.warning("worker.claim_failed", error=str(exc))
                job = None
            if job is not None:
                await process_job(job)
                continue
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), POLL_INTERVAL_S)

    async def _listen(self) -> None:
        backoff = 1.0
        while True:
            try:
                async with await AsyncConnection.connect(
                    get_settings().database_url, autocommit=True
                ) as conn:
                    await conn.execute(f"listen {queue.CHANNEL}")
                    backoff = 1.0
                    async for _ in conn.notifies():
                        self._wake.set()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # polling keeps us going meanwhile
                log.warning("worker.listen_down", error=str(exc), retry_in=backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    async def _maintenance(self) -> None:
        last_sweep = 0.0
        loop = asyncio.get_running_loop()
        while True:
            try:
                async with connection() as conn:
                    if await queue.reclaim_expired(conn):
                        self._wake.set()
                if loop.time() - last_sweep >= SWEEP_INTERVAL_S:
                    last_sweep = loop.time()
                    await pipeline.enqueue_missing_embeddings()
                    async with connection() as conn:
                        await queue.prune(conn)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("worker.maintenance_failed", error=str(exc))
            await asyncio.sleep(MAINTENANCE_INTERVAL_S)


async def run_worker(stop: asyncio.Event, concurrency: int) -> None:
    """Consume jobs until ``stop`` is set. Requires an open pool (``kb.db.open_pool``)."""
    await Worker(concurrency).run(stop)


async def start_embedded_worker() -> Callable[[], Awaitable[None]]:
    """Run the worker as a task of the current (API) process; returns an async stopper.

    Call after ``open_pool()`` in the app lifespan and await the stopper before closing it.
    """
    stop = asyncio.Event()
    task = asyncio.create_task(
        run_worker(stop, get_settings().worker_concurrency), name="kb-embedded-worker"
    )

    async def stopper() -> None:
        stop.set()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    return stopper


def embedded_worker_enabled() -> bool:
    return os.environ.get("KB_EMBEDDED_WORKER", "").lower() in ("1", "true", "yes", "on")


async def _main() -> None:
    settings = get_settings()
    await migrate(settings)
    await open_pool(settings)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)
    try:
        await run_worker(stop, settings.worker_concurrency)
    finally:
        await close_pool()


def main() -> None:
    from kb.logging import setup_logging

    settings = get_settings()
    setup_logging(settings.log_level, settings.log_json)
    asyncio.run(_main())


if __name__ == "__main__":
    main()
