"""Request body size limits, enforced while the body streams in.

Starlette spools a whole multipart upload to disk before the endpoint runs, so a per-file
check in the handler alone would still let one request fill the disk. This middleware
rejects oversized bodies early: from ``Content-Length`` when present, otherwise once the
streamed (chunked) body crosses the limit.
"""

from __future__ import annotations

from collections.abc import Callable

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class _TooLarge(Exception):
    pass


class BodySizeLimit:
    def __init__(self, app: ASGIApp, limit_for: Callable[[str], int]) -> None:
        self.app = app
        self.limit_for = limit_for  # path -> max bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] in ("GET", "HEAD", "OPTIONS"):
            await self.app(scope, receive, send)
            return
        limit = self.limit_for(scope["path"])
        for key, value in scope.get("headers", []):
            if key == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    declared = 0
                if declared > limit:
                    await _reject(scope, receive, send, limit)
                    return
                break

        received = 0
        started = rejected = False

        async def limited_receive() -> Message:
            nonlocal received, rejected
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit and not rejected:
                    if started:  # too late for a clean 413; abort the request
                        raise _TooLarge
                    rejected = True
                    await _reject(scope, receive, send, limit)
                    raise _TooLarge
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal started
            if rejected:  # we already answered 413; drop whatever the app sends
                return
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except _TooLarge:
            if not rejected:
                raise


async def _reject(scope: Scope, receive: Receive, send: Send, limit: int) -> None:
    response = JSONResponse(
        {"detail": f"request body larger than {limit // (1 << 20)} MB"},
        status_code=413,
        headers={"Connection": "close"},
    )
    await response(scope, receive, send)
