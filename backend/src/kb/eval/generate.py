"""Synthetic query generation for bootstrapping qrels.

Samples chunks (deterministically for a given seed), asks an LLM to write one realistic
question each chunk answers, filters questions that copy the passage, and writes JSONL
lines compatible with :mod:`kb.eval.runner`.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

import structlog

from kb.db import connection
from kb.providers import LLM, get_llm

log = structlog.get_logger(__name__)

QUESTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"question": {"type": "string"}},
    "required": ["question"],
    "additionalProperties": False,
}

_SYSTEM = (
    "You write evaluation queries for a search engine. Given a passage from a knowledge base, "
    "write ONE realistic question or search query that a user would type and that this passage "
    "answers. Do not copy phrases verbatim; paraphrase. Vary the style (keywords, natural "
    'question). Reply as JSON {"question": "..."}.'
)
_WORD = re.compile(r"\w+")


def copies_passage(question: str, passage: str, n: int = 5, threshold: float = 0.5) -> bool:
    """True when most word n-grams of the question appear verbatim in the passage."""
    q = _WORD.findall(question.lower())
    p = _WORD.findall(passage.lower())
    if len(q) < n:
        return " ".join(q) in " ".join(p) and len(q) >= 3
    grams = {tuple(q[i : i + n]) for i in range(len(q) - n + 1)}
    pgrams = {tuple(p[i : i + n]) for i in range(len(p) - n + 1)}
    return len(grams & pgrams) / len(grams) >= threshold


async def sample_chunks(
    n: int, *, collection: str | None = None, seed: str = "kb", min_chars: int = 200
) -> list[dict[str, Any]]:
    async with connection() as conn:
        cur = await conn.execute(
            """
            select c.id, c.document_id, c.heading_path, c.content, d.title
            from chunks c
            join documents d on d.id = c.document_id
            join collections col on col.id = d.collection_id
            where length(c.content) >= %(min)s
              and (%(col)s::text is null or col.slug = %(col)s)
            order by md5(c.id::text || %(seed)s)
            limit %(n)s
            """,
            {"min": min_chars, "col": collection, "seed": seed, "n": n},
        )
        return list(await cur.fetchall())


def _parse_question(reply: str) -> str:
    try:
        data = json.loads(reply)
        if isinstance(data, dict):
            return str(data.get("question", "")).strip()
    except ValueError:
        pass
    lines = reply.strip().splitlines()
    return lines[0].strip().strip('"').strip() if lines else ""


async def generate(
    n: int,
    out: str | Path,
    *,
    collection: str | None = None,
    engine: str = "local",
    seed: str = "kb",
    concurrency: int = 4,
    min_chars: int = 200,
    llm: LLM | None = None,
) -> int:
    """Write up to ``n`` qrels to ``out``; returns how many were written."""
    llm = llm or get_llm(engine)
    chunks = await sample_chunks(n, collection=collection, seed=seed, min_chars=min_chars)
    sem = asyncio.Semaphore(concurrency)

    async def one(chunk: dict[str, Any]) -> dict[str, Any] | None:
        passage = f"{chunk['title']}\n{chunk['heading_path']}\n\n{chunk['content'][:3000]}"
        async with sem:
            try:
                reply = await llm.complete(
                    [
                        {"role": "system", "content": _SYSTEM},
                        {"role": "user", "content": f"Passage:\n{passage}"},
                    ],
                    max_tokens=200,
                    json_schema=QUESTION_SCHEMA,
                )
            except Exception as exc:
                log.warning("eval.generate_failed", chunk_id=chunk["id"], error=repr(exc))
                return None
        question = _parse_question(reply)
        if len(question) < 8 or copies_passage(question, chunk["content"]):
            return None
        return {
            "query": question,
            "relevant_chunk_ids": [chunk["id"]],
            "relevant_document_ids": [str(chunk["document_id"])],
        }

    rows = [r for r in await asyncio.gather(*(one(c) for c in chunks)) if r is not None]
    text = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    await asyncio.to_thread(Path(out).write_text, text, encoding="utf-8")
    return len(rows)
