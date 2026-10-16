"""Post-processing: per-document diversity, small-to-big context expansion and snippets."""

from __future__ import annotations

import html
import re
from collections import Counter

from kb.db import connection
from kb.search.candidates import Candidate
from kb.search.query import stem

SNIPPET_CHARS = 240
_WORD = re.compile(r"\w+", re.UNICODE)


def diversify(cands: list[Candidate], limit: int, per_document: int | None) -> list[Candidate]:
    """Keep at most ``per_document`` hits per document (None = no cap), best first."""
    if per_document is None:
        return cands[:limit]
    seen: Counter = Counter()
    out: list[Candidate] = []
    for cand in cands:
        if seen[cand.document_id] >= per_document:
            continue
        seen[cand.document_id] += 1
        out.append(cand)
        if len(out) == limit:
            break
    return out


def make_snippet(text: str, terms: list[str], width: int = SNIPPET_CHARS) -> str:
    """Pick the window with the most distinct query-term matches; escape everything and wrap
    matches in ``<mark>``. Works for any hit (lexical, dense-only or reranked)."""
    wanted = {t.lower() for t in terms} | {stem(t) for t in terms}
    matches = [
        m for m in _WORD.finditer(text) if m.group().lower() in wanted or stem(m.group()) in wanted
    ]
    start = 0
    if matches and len(text) > width:
        best, best_score = 0, -1
        for i, m in enumerate(matches):
            lo = max(0, m.start() - width // 4)
            window = [x for x in matches[i:] if x.end() <= lo + width]
            score = len({stem(x.group()) for x in window}) * 100 + len(window)
            if score > best_score:
                best, best_score = lo, score
        start = best
        # Snap to a word boundary.
        if start > 0:
            space = text.rfind(" ", max(0, start - 20), start)
            start = space + 1 if space >= 0 else start
    end = min(len(text), start + width)
    if end < len(text):
        space = text.rfind(" ", start + width // 2, end)
        end = space if space > 0 else end

    out: list[str] = ["…" if start > 0 else ""]
    pos = start
    for m in matches:
        if m.start() < start or m.end() > end:
            continue
        out.append(html.escape(text[pos : m.start()]))
        out.append(f"<mark>{html.escape(m.group())}</mark>")
        pos = m.end()
    out.append(html.escape(text[pos:end]))
    if end < len(text):
        out.append("…")
    return "".join(out).replace("\n", " ").strip()


def _strip_overlap(left: str, right: str, max_overlap: int = 1500) -> str:
    """Return ``right`` without the prefix it shares with the end of ``left`` (chunk overlap)."""
    limit = min(len(left), len(right), max_overlap)
    for size in range(limit, 20, -1):
        if left.endswith(right[:size]):
            return right[size:].lstrip()
    return right


async def expand_context(cands: list[Candidate]) -> None:
    """Small-to-big: replace each hit's content with chunks ``ord-1 .. ord+1`` joined."""
    if not cands:
        return
    docs, ords = [], []
    for c in cands:
        for o in (c.ord - 1, c.ord + 1):
            if o >= 0:
                docs.append(c.document_id)
                ords.append(o)
    async with connection() as conn:
        cur = await conn.execute(
            """
            select c.document_id, c.ord, c.content
            from chunks c
            join unnest(%s::uuid[], %s::int[]) as t(d, o) on c.document_id = t.d and c.ord = t.o
            """,
            (docs, ords),
        )
        neighbours = {(r["document_id"], r["ord"]): r["content"] for r in await cur.fetchall()}
    for c in cands:
        text = c.content
        prev = neighbours.get((c.document_id, c.ord - 1))
        nxt = neighbours.get((c.document_id, c.ord + 1))
        if prev:
            text = prev.rstrip() + "\n\n" + _strip_overlap(prev, text)
        if nxt:
            text = text.rstrip() + "\n\n" + _strip_overlap(c.content, nxt)
        c.content = text
