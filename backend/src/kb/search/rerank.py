"""Two-stage reranking cascade.

Stage 1 – cross-encoder (``get_reranker()``, e.g. Qwen3-Reranker-0.6B behind llama-server
``/v1/rerank``) over the top fused candidates.

Stage 2 – optional listwise LLM judge (Claude when ``KB_ANTHROPIC_API_KEY`` is set, else the
local router model) over the top N. The model returns graded relevance 0–3 per passage via
JSON-schema constrained output; grades are blended with the stage-1 scores and passages
graded 0 are demoted below everything else. Each stage raises :class:`ProviderError` on
failure so the pipeline can skip it and report the stage as degraded.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from kb.config import get_settings
from kb.providers import LLM, ChatMessage, ProviderError, get_llm, get_reranker
from kb.search.candidates import Candidate
from kb.search.fusion import minmax

LLM_PASSAGE_CHARS = 1200
LLM_WEIGHT = 0.6  # weight of the LLM grade vs the normalised cross-encoder score

LLM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "ranking": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "relevance": {"type": "integer", "enum": [0, 1, 2, 3]},
                },
                "required": ["id", "relevance"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["ranking"],
    "additionalProperties": False,
}

_SYSTEM = (
    "You are a meticulous search relevance judge. Grade how well each passage answers the "
    "user's query: 3 = directly and fully answers it, 2 = highly relevant / partially answers, "
    "1 = related topic but does not answer, 0 = irrelevant. Judge only the passage text. "
    'Return JSON {"ranking":[{"id":<passage id>,"relevance":<0-3>}, ...]} listing every '
    "passage, most relevant first."
)


# --- stage 1 ----------------------------------------------------------------------------


async def cross_encode(query: str, cands: list[Candidate], window: int) -> list[Candidate]:
    """Rerank the first ``window`` candidates; the rest keep their order behind them."""
    reranker = get_reranker()
    if reranker is None or not cands:
        return cands
    head, tail = cands[:window], cands[window:]
    async with asyncio.timeout(get_settings().model_timeout_s + 5):
        scores = await reranker.rerank(query, [c.rerank_text() for c in head])
    if len(scores) != len(head):
        raise ProviderError(f"reranker returned {len(scores)} scores for {len(head)} documents")
    for cand, s in zip(head, scores, strict=True):
        cand.scores["rerank"] = float(s)
        cand.score = float(s)
    head.sort(key=lambda c: c.score, reverse=True)
    return head + tail


# --- stage 2 ----------------------------------------------------------------------------


def choose_llm() -> tuple[LLM, str | None]:
    """Claude when configured, else the local router model (smaller, fast, JSON-capable)."""
    s = get_settings()
    if s.anthropic_api_key is not None and s.anthropic_api_key.get_secret_value():
        return get_llm("cloud"), None
    return get_llm("local"), s.llm_router_model if s.llm_provider == "openai" else None


def build_messages(query: str, cands: list[Candidate]) -> list[ChatMessage]:
    blocks = []
    for i, c in enumerate(cands, start=1):
        text = c.rerank_text(LLM_PASSAGE_CHARS)
        blocks.append(f"[{i}]\n{text}")
    user = f"Query: {query}\n\nPassages:\n\n" + "\n\n".join(blocks)
    return [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": user}]


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def _first_json(text: str) -> Any:
    m = _FENCE.search(text)
    if m:
        text = m.group(1)
    decoder = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch in "{[":
            try:
                return decoder.raw_decode(text, i)[0]
            except ValueError:
                continue
    return None


def parse_ranking(text: str, n: int) -> dict[int, int] | None:
    """Parse the judge's reply into ``{passage_index (0-based): relevance 0..3}``.

    Tolerates code fences, chatter around the JSON, a bare list, string ids ("3", "[3]"),
    float grades and unknown/duplicate ids. Returns None when nothing usable was found.
    """
    data = _first_json(text or "")
    if isinstance(data, dict):
        data = data.get("ranking", data.get("results"))
    if not isinstance(data, list):
        return None
    out: dict[int, int] = {}
    for item in data:
        if not isinstance(item, dict):
            continue
        raw_id, raw_rel = item.get("id"), item.get("relevance", item.get("score"))
        try:
            idx = int(str(raw_id).strip("[] ")) - 1
            rel = round(float(raw_rel))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        if 0 <= idx < n and idx not in out:
            out[idx] = min(max(rel, 0), 3)
    return out or None


def blend(cands: list[Candidate], window: int, grades: dict[int, int]) -> list[Candidate]:
    """Judged-relevant (graded ≥1 or ungraded) > unjudged tail > judged irrelevant (0)."""
    head, tail = cands[:window], cands[window:]
    prior = minmax([(i, c.score) for i, c in enumerate(head)])
    relevant: list[Candidate] = []
    irrelevant: list[Candidate] = []
    for i, cand in enumerate(head):
        grade = grades.get(i)
        if grade is not None:
            cand.scores["llm"] = float(grade)
        if grade == 0:
            cand.score = 0.5 * prior[i]
            irrelevant.append(cand)
            continue
        g = 1 if grade is None else grade
        cand.score = 2.0 + LLM_WEIGHT * (g / 3) + (1 - LLM_WEIGHT) * prior[i]
        relevant.append(cand)
    tail_norm = minmax([(i, c.score) for i, c in enumerate(tail)])
    for i, cand in enumerate(tail):
        cand.score = 1.0 + 0.999 * tail_norm[i]
    relevant.sort(key=lambda c: c.score, reverse=True)
    irrelevant.sort(key=lambda c: c.score, reverse=True)
    return relevant + tail + irrelevant


async def llm_rerank(query: str, cands: list[Candidate], top_n: int) -> list[Candidate]:
    window = min(top_n, len(cands))
    if window == 0:
        return cands
    llm, model = choose_llm()
    messages = build_messages(query, cands[:window])
    async with asyncio.timeout(max(get_settings().model_timeout_s, 60.0)):
        reply = await llm.complete(
            messages,
            max_tokens=64 + 24 * window,
            temperature=0.0,
            json_schema=LLM_SCHEMA,
            model=model,
        )
    grades = parse_ranking(reply, window)
    if grades is None:
        raise ProviderError("LLM reranker returned no usable ranking")
    return blend(cands, window, grades)
