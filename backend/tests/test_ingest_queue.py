import asyncio
import uuid

import pytest

from kb.db import connection
from kb.ingest import queue, worker

pytestmark = pytest.mark.usefixtures("app")


@pytest.fixture(autouse=True)
async def _cleanup_test_jobs():
    yield
    async with connection() as conn:
        await conn.execute("delete from jobs where kind like 'test-%'")


def _kind() -> str:
    return f"test-{uuid.uuid4().hex[:8]}"


async def _job(job_id: int) -> dict:
    async with connection() as conn:
        cur = await conn.execute("select * from jobs where id = %s", (job_id,))
        return await cur.fetchone()


async def _make_due(job_id: int) -> None:
    async with connection() as conn:
        await conn.execute("update jobs set run_after = now() where id = %s", (job_id,))


async def test_concurrent_claimers_never_double_claim():
    kind = _kind()
    async with connection() as conn, conn.transaction():
        for i in range(20):
            await queue.enqueue(conn, kind, {"i": i})
    claimed: list[int] = []

    async def claimer(n: int) -> None:
        while True:
            async with connection() as conn:
                job = await queue.claim(conn, f"claimer-{n}", kinds=[kind])
            if job is None:
                return
            claimed.append(job.id)
            await asyncio.sleep(0.001)
            async with connection() as conn:
                await queue.complete(conn, job)

    await asyncio.gather(*(claimer(n) for n in range(5)))
    assert len(claimed) == 20
    assert len(set(claimed)) == 20
    async with connection() as conn:
        cur = await conn.execute(
            "select count(*) as n from jobs where kind = %s and status = 'done'", (kind,)
        )
        assert (await cur.fetchone())["n"] == 20


async def test_dedup_key_collapses_queued_jobs_only():
    kind = _kind()
    key = f"dedup-{kind}"
    async with connection() as conn:
        first = await queue.enqueue(conn, kind, {}, dedup_key=key)
        assert first is not None
        assert await queue.enqueue(conn, kind, {}, dedup_key=key) is None
        job = await queue.claim(conn, "w", kinds=[kind])
        assert job is not None and job.id == first
        # While the first run is in flight, new work for the same key can be queued.
        second = await queue.enqueue(conn, kind, {}, dedup_key=key)
        assert second is not None and second != first


async def test_expired_lease_is_reclaimed():
    kind = _kind()
    async with connection() as conn:
        job_id = await queue.enqueue(conn, kind, {})
        crashed = await queue.claim(conn, "crashed-worker", kinds=[kind])
        assert crashed is not None and crashed.id == job_id
        assert await queue.claim(conn, "other", kinds=[kind]) is None
        await conn.execute(
            "update jobs set lease_until = now() - interval '1 second' where id = %s", (job_id,)
        )
        assert await queue.reclaim_expired(conn) >= 1
        row = await _job(job_id)
        assert row["status"] == "queued" and "lease expired" in row["last_error"]
        again = await queue.claim(conn, "other", kinds=[kind])
        assert again is not None and again.id == job_id and again.attempts == 2
        # The crashed worker lost its lease: heartbeats and completion are rejected.
        assert await queue.heartbeat(conn, crashed) is False
        assert await queue.heartbeat(conn, again) is True
        await queue.complete(conn, crashed)
        assert (await _job(job_id))["status"] == "running"


async def test_retry_with_backoff_then_failed():
    kind = _kind()
    async with connection() as conn:
        job_id = await queue.enqueue(conn, kind, {}, max_attempts=3)
        for attempt in (1, 2, 3):
            job = await queue.claim(conn, "w", kinds=[kind])
            assert job is not None and job.attempts == attempt
            final = await queue.fail(conn, job, f"boom {attempt}")
            row = await _job(job_id)
            if attempt < 3:
                assert not final and row["status"] == "queued"
                cur = await conn.execute(
                    "select extract(epoch from run_after - now()) as s from jobs where id = %s",
                    (job_id,),
                )
                delay = (await cur.fetchone())["s"]
                expected = queue.BACKOFF_BASE_S * 2 ** (attempt - 1)
                assert expected * 0.7 <= float(delay) <= expected * 1.3
                assert await queue.claim(conn, "w", kinds=[kind]) is None  # not due yet
                await _make_due(job_id)
            else:
                assert final and row["status"] == "failed" and row["last_error"] == "boom 3"


async def test_release_does_not_count_attempt():
    kind = _kind()
    async with connection() as conn:
        await queue.enqueue(conn, kind, {})
        job = await queue.claim(conn, "w", kinds=[kind])
        assert job is not None
        await queue.release(conn, job)
        again = await queue.claim(conn, "w", kinds=[kind])
        assert again is not None and again.id == job.id and again.attempts == 1


def test_backoff_grows_and_caps():
    assert 3.7 <= queue.backoff_seconds(1) <= 6.3
    assert queue.backoff_seconds(30) <= queue.BACKOFF_CAP_S * 1.25


async def test_worker_wakes_on_notify_and_stops_gracefully(monkeypatch):
    kind = _kind()
    seen: list[dict] = []
    done = asyncio.Event()

    async def handler(payload: dict) -> str:
        seen.append(payload)
        if len(seen) == 3:
            done.set()
        return "ok"

    monkeypatch.setitem(worker.HANDLERS, kind, handler)
    monkeypatch.setattr(worker, "POLL_INTERVAL_S", 30.0)  # prove LISTEN/NOTIFY wakes us
    stop = asyncio.Event()
    task = asyncio.create_task(worker.run_worker(stop, 2))
    await asyncio.sleep(0.5)  # let the listener connect and consumers go idle
    async with connection() as conn:
        for i in range(3):
            await queue.enqueue(conn, kind, {"i": i})
    await asyncio.wait_for(done.wait(), 5)
    stop.set()
    await asyncio.wait_for(task, 10)
    assert sorted(p["i"] for p in seen) == [0, 1, 2]


async def test_shutdown_releases_in_flight_job(monkeypatch):
    kind = _kind()
    started = asyncio.Event()

    async def slow(payload: dict) -> str:
        started.set()
        await asyncio.sleep(60)
        return "never"

    monkeypatch.setitem(worker.HANDLERS, kind, slow)
    monkeypatch.setattr(worker, "SHUTDOWN_GRACE_S", 0.2)
    async with connection() as conn:
        job_id = await queue.enqueue(conn, kind, {})
    stop = asyncio.Event()
    task = asyncio.create_task(worker.run_worker(stop, 1))
    await asyncio.wait_for(started.wait(), 10)
    stop.set()
    await asyncio.wait_for(task, 10)
    row = await _job(job_id)
    assert row["status"] == "queued" and row["attempts"] == 0 and row["locked_by"] is None
    async with connection() as conn:
        await conn.execute("delete from jobs where id = %s", (job_id,))
