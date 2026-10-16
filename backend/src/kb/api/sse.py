"""Server-sent-event helpers shared by ``/api/events`` and ``/api/ask/stream``."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Mapping
from typing import Any

import anyio
from sse_starlette import EventSourceResponse

SHUTDOWN_GRACE_S = 1.0


async def until_shutdown[T](source: AsyncIterator[T], shutdown: anyio.Event) -> AsyncIterator[T]:
    """Relay ``source`` until it ends or ``shutdown`` is set, then return cleanly.

    Returning (rather than being cancelled) lets sse-starlette finish the HTTP response,
    so a server restart closes streams properly instead of logging
    "ASGI callable returned without completing response".
    """
    stop = asyncio.ensure_future(shutdown.wait())
    nxt: asyncio.Future[T] | None = None
    try:
        while True:
            nxt = asyncio.ensure_future(anext(source))
            await asyncio.wait({nxt, stop}, return_when=asyncio.FIRST_COMPLETED)
            if not nxt.done():
                return  # shutting down; ``finally`` cancels the pending read
            try:
                item = nxt.result()
            except StopAsyncIteration:
                return
            nxt = None
            yield item
    finally:
        # Also reached when the client disconnects and this generator is cancelled: the
        # pending read must be cancelled too, or it would keep running detached.
        stop.cancel()
        if nxt is not None and not nxt.done():
            nxt.cancel()
            with contextlib.suppress(asyncio.CancelledError, StopAsyncIteration):
                await nxt
        aclose = getattr(source, "aclose", None)
        if aclose is not None:
            with contextlib.suppress(Exception):
                await aclose()


def sse_response(
    stream: AsyncIterator[Any], *, ping: int, headers: Mapping[str, str], **kwargs: Any
) -> EventSourceResponse:
    """An ``EventSourceResponse`` whose stream ends gracefully on server shutdown."""
    shutdown = anyio.Event()
    return EventSourceResponse(
        until_shutdown(stream, shutdown),
        ping=ping,
        headers=dict(headers),
        shutdown_event=shutdown,
        shutdown_grace_period=SHUTDOWN_GRACE_S,
        **kwargs,
    )
