"""Situating context per chunk (Anthropic "Contextual Retrieval").

The context is prepended to the chunk for embedding and indexed for BM25, so a chunk like
"Revenue grew 3%" becomes findable as "ACME Q2 2026 report, regional results: …".

With ``KB_CONTEXTUAL_CHUNKS=true`` an LLM writes 1-2 sentences per chunk. The whole
document goes in the system prompt (cached by Claude across all chunks of a document;
llama.cpp / MLX reuse the common prefix too) and the chunk goes in the user message.
Whenever the LLM is unavailable the deterministic heading context is used instead.
"""

from __future__ import annotations

import asyncio
import re

import structlog

from kb.config import get_settings
from kb.ingest.chunking import Chunk
from kb.providers import LLM, ProviderError, get_llm

log = structlog.get_logger(__name__)

CONCURRENCY = 4
MAX_CONTEXT_CHARS = 600
# Local models usually run with 8-32k token windows; Claude has 1M.
LOCAL_DOC_CHARS = 48_000
CLOUD_DOC_CHARS = 600_000

DOCUMENT_PROMPT = "<document>\n{document}\n</document>"
CHUNK_PROMPT = (
    "Here is the chunk we want to situate within the whole document\n"
    "<chunk>\n{chunk}\n</chunk>\n"
    "Please give a short succinct context to situate this chunk within the overall document "
    "for the purposes of improving search retrieval of the chunk. "
    "Answer only with the succinct context and nothing else."
)


def heading_context(title: str, heading_path: str) -> str:
    """Deterministic fallback: ``"<doc title> — <heading path>"``."""
    title = title.strip()
    if heading_path and heading_path != title:
        return f"{title} — {heading_path}" if title else heading_path
    return title


def _choose_llm() -> tuple[LLM, int]:
    s = get_settings()
    if s.anthropic_api_key is not None and s.anthropic_api_key.get_secret_value():
        return get_llm("cloud"), CLOUD_DOC_CHARS
    return get_llm("local"), LOCAL_DOC_CHARS


def _document_window(body: str, chunk: Chunk, limit: int) -> str:
    """The whole document when it fits, else a window centred on the chunk."""
    if len(body) <= limit:
        return body
    centre = (chunk.char_start + chunk.char_end) // 2
    start = max(0, min(centre - limit // 2, len(body) - limit))
    return body[start : start + limit]


def _clean(text: str) -> str:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)  # reasoning models
    text = " ".join(text.split())
    return text[:MAX_CONTEXT_CHARS].strip()


async def build_contexts(title: str, body: str, chunks: list[Chunk]) -> list[str]:
    """One context string per chunk, same order. Never raises on model failures."""
    fallback = [heading_context(title, c.heading_path) for c in chunks]
    if not chunks or not get_settings().contextual_chunks:
        return fallback
    try:
        llm, limit = _choose_llm()
    except ProviderError as exc:
        log.warning("contextual.llm_unavailable", error=str(exc))
        return fallback

    sem = asyncio.Semaphore(CONCURRENCY)
    whole = len(body) <= limit
    system = DOCUMENT_PROMPT.format(document=body) if whole else None
    failures = 0

    async def one(i: int, chunk: Chunk) -> str:
        nonlocal failures
        doc = system or DOCUMENT_PROMPT.format(document=_document_window(body, chunk, limit))
        async with sem:
            try:
                text = await llm.complete(
                    [
                        {"role": "system", "content": doc},
                        {"role": "user", "content": CHUNK_PROMPT.format(chunk=chunk.content)},
                    ],
                    max_tokens=160,
                    temperature=0.0,
                )
            except ProviderError as exc:
                failures += 1
                if failures == 1:
                    log.warning("contextual.fallback", error=str(exc))
                return fallback[i]
        return _clean(text) or fallback[i]

    return list(await asyncio.gather(*(one(i, c) for i, c in enumerate(chunks))))
