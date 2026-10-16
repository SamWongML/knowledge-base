"""Process-local fan-out of Postgres ``NOTIFY kb_events`` messages to SSE subscribers.

Every API worker process holds one dedicated LISTEN connection (outside the pool) and
pushes each event into the bounded queue of every connected client. Slow clients drop
events instead of applying back-pressure to the database.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from typing import Any

import structlog
from psycopg import AsyncConnection

from kb.config import get_settings

log = structlog.get_logger(__name__)

CHANNEL = "kb_events"


class EventBroker:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="kb-event-listener")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _run(self) -> None:
        backoff = 1.0
        while True:
            try:
                async with await AsyncConnection.connect(
                    get_settings().database_url, autocommit=True
                ) as conn:
                    await conn.execute(f"listen {CHANNEL}")
                    backoff = 1.0
                    async for note in conn.notifies():
                        self.publish_local(json.loads(note.payload))
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # reconnect on any connection failure
                log.warning("events.listener_down", error=str(exc), retry_in=backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    def publish_local(self, event: dict[str, Any]) -> None:
        for queue in list(self._subscribers):
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(event)

    @contextlib.asynccontextmanager
    async def subscribe(self) -> AsyncIterator[asyncio.Queue[dict[str, Any]]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=256)
        self._subscribers.add(queue)
        try:
            yield queue
        finally:
            self._subscribers.discard(queue)


broker = EventBroker()


async def notify(conn: AsyncConnection, event: dict[str, Any]) -> None:
    """Publish an event to every API process (payloads must stay under 8 kB)."""
    await conn.execute("select pg_notify(%s, %s)", (CHANNEL, json.dumps(event, default=str)))
