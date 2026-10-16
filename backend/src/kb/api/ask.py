"""Agentic question answering: ``POST /api/ask`` and its SSE variant ``POST /api/ask/stream``."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

import structlog
from fastapi import APIRouter
from sse_starlette import EventSourceResponse

from kb.agent.loop import ask, ask_stream
from kb.schemas import AskRequest, AskResponse

router = APIRouter(tags=["ask"])
log = structlog.get_logger(__name__)

PING_S = 15


@router.post("/ask", response_model=AskResponse)
async def ask_endpoint(req: AskRequest) -> AskResponse:
    """Answer a question from the knowledge base with citations (non-streaming)."""
    return await ask(req)


@router.post("/ask/stream")
async def ask_stream_endpoint(req: AskRequest) -> EventSourceResponse:
    """Server-sent events: ``step`` (AgentStep) …, then ``done`` (AskResponse) or ``error``."""

    async def events() -> AsyncIterator[dict[str, str]]:
        try:
            async for item in ask_stream(req):
                if isinstance(item, AskResponse):
                    yield {"event": "done", "data": item.model_dump_json()}
                else:
                    yield {"event": "step", "data": item.model_dump_json()}
        except asyncio.CancelledError:  # client went away; ask_stream cleans up on close
            raise
        except Exception as exc:
            log.exception("ask.stream_failed")
            yield {"event": "error", "data": json.dumps({"detail": str(exc) or type(exc).__name__})}

    return EventSourceResponse(
        events(),
        ping=PING_S,
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
