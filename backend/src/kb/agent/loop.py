"""Agentic question answering over the knowledge base.

Pipeline (route first, loop rarely; see docs/research/raw/02-agentic-search.md):

1. **Route** with a small schema-constrained call: ``research`` / ``direct_lookup`` /
   ``chitchat`` plus 1-4 rewritten queries (keyword and natural-language variants).
2. **Search** all queries concurrently (hybrid + rerank), merge by chunk, drop chunks
   seen in earlier steps.
3. **Reflect** (research only): a sufficiency grader picks relevant evidence and, when
   something is missing, proposes new queries. Loop until sufficient, out of new queries,
   no new chunks, out of time, or ``max_steps`` search rounds.
4. **Read** neighbouring chunks of the top sources to widen their context.
5. **Answer** from numbered sources with ``[n]`` citations, streamed token by token.

Every model call degrades gracefully: a failing engine is dropped for the rest of the
request (local → Claude when configured), and when no LLM is left the answer is an
extractive list of the best passages. All failures are reported as ``error`` steps.
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from collections.abc import AsyncGenerator, Sequence
from contextlib import aclosing
from dataclasses import dataclass, field
from typing import Any, cast

import structlog

from kb.agent import prompts as P
from kb.agent.parsing import ThinkFilter, cited_numbers, parse_json_object
from kb.config import get_settings
from kb.providers import LLM, ChatMessage, ProviderError, get_llm
from kb.schemas import (
    AgentStep,
    AskRequest,
    AskResponse,
    Citation,
    SearchHit,
    SearchRequest,
    SearchResponse,
)

log = structlog.get_logger(__name__)

SEARCH_LIMIT = 8  # hits per query
MAX_QUERIES = 4  # queries per search round
MAX_EVIDENCE = 40  # evidence pool size across rounds
REFLECT_CHARS = 6000  # evidence budget of one grading prompt
REFLECT_ITEM_CHARS = 700
ANSWER_CHARS = 9000  # evidence budget of the synthesis prompt
SOURCE_CHARS = 1600
MAX_SOURCES = 8
READ_TOP = 3  # sources widened with neighbouring chunks
NEIGHBOUR_CHARS = 400
DEFAULT_TIMEOUT_S = 90.0  # whole request; the search loop gets LOOP_SHARE of it
LOOP_SHARE = 0.6
CACHE_TTL_S = 60.0
CACHE_SIZE = 128


# --- public API ----------------------------------------------------------------------


async def ask(req: AskRequest) -> AskResponse:
    """Run the agent to completion and return the final answer."""
    async with aclosing(ask_stream(req)) as stream:
        async for item in stream:
            if isinstance(item, AskResponse):
                return item
    raise RuntimeError("agent produced no response")  # pragma: no cover


async def ask_stream(req: AskRequest) -> AsyncGenerator[AgentStep | AskResponse, None]:
    """Yield :class:`AgentStep` events as they happen; the final item is the AskResponse.

    Safe to abandon midway (client disconnect): no background tasks outlive the generator.
    """
    started = time.perf_counter()
    key = _cache_key(req)
    if (cached := _cache_get(key)) is not None:
        for step in cached.steps:
            yield step
        yield cached.model_copy(update={"total_ms": _ms(started)})
        return

    agent = _Agent(req)
    async with aclosing(agent.run()) as steps:
        async for step in steps:
            yield step
    response = AskResponse(
        question=req.question,
        answer=agent.answer,
        citations=agent.citations,
        steps=agent.steps,
        engine=agent.engine_label,
        total_ms=_ms(started),
    )
    log.info(
        "agent.done",
        route=agent.route,
        rounds=agent.rounds,
        evidence=len(agent.evidence),
        citations=len(agent.citations),
        engine=agent.engine_label,
        ms=round(response.total_ms),
    )
    if not any(s.type == "error" for s in agent.steps):
        _cache_put(key, response)
    yield response


def clear_cache() -> None:
    _cache.clear()


# --- answer cache --------------------------------------------------------------------

_cache: OrderedDict[tuple[Any, ...], tuple[float, AskResponse]] = OrderedDict()


def _cache_key(req: AskRequest) -> tuple[Any, ...]:
    return (_norm(req.question), req.filters.model_dump_json(), req.engine, req.max_steps)


def _cache_get(key: tuple[Any, ...]) -> AskResponse | None:
    entry = _cache.get(key)
    if entry is None:
        return None
    stored_at, response = entry
    if time.monotonic() - stored_at > CACHE_TTL_S:
        _cache.pop(key, None)
        return None
    _cache.move_to_end(key)
    return response


def _cache_put(key: tuple[Any, ...], response: AskResponse) -> None:
    _cache[key] = (time.monotonic(), response)
    _cache.move_to_end(key)
    while len(_cache) > CACHE_SIZE:
        _cache.popitem(last=False)


# --- engines -------------------------------------------------------------------------


@dataclass
class _Engine:
    label: str
    llm: LLM
    router_model: str | None = None  # smaller model for routing / grading calls


class _Engines:
    """Ordered fallback chain of LLMs. A failing engine is dropped for the rest of the run."""

    def __init__(self, req: AskRequest) -> None:
        self.chain: list[_Engine] = []
        self.errors: list[str] = []
        s = get_settings()

        def local() -> _Engine:
            router = s.llm_router_model if s.llm_provider == "openai" else None
            return _Engine("local", get_llm("local"), router)

        def cloud() -> _Engine:
            return _Engine("cloud", get_llm("cloud"))

        has_cloud = bool(s.anthropic_api_key and s.anthropic_api_key.get_secret_value())
        if req.engine == "cloud":
            order = [cloud, local]
        else:
            order = [local, cloud] if has_cloud and s.llm_provider != "anthropic" else [local]
        for make in order:
            try:
                self.chain.append(make())
            except ProviderError as exc:
                self.errors.append(f"LLM engine unavailable: {exc}")

    def fail(self, exc: Exception) -> None:
        engine = self.chain.pop(0)
        self.errors.append(f"{engine.label} LLM failed: {exc}")
        log.warning("agent.llm_failed", engine=engine.label, error=str(exc))

    async def complete_json(
        self, messages: list[ChatMessage], schema: dict[str, Any], deadline: float
    ) -> dict[str, Any] | None:
        """Schema-constrained call on the router model. None when the reply is unparseable."""
        while self.chain:
            engine = self.chain[0]
            try:
                async with asyncio.timeout_at(deadline):
                    raw = await engine.llm.complete(
                        messages,
                        max_tokens=384,
                        temperature=0.0,
                        json_schema=schema,
                        model=engine.router_model,
                    )
            except ProviderError as exc:
                self.fail(exc)
                continue
            return parse_json_object(raw)
        raise ProviderError("no language model available")


# --- the agent -----------------------------------------------------------------------


@dataclass
class _Verdict:
    sufficient: bool = True
    missing: str = ""
    next_queries: list[str] = field(default_factory=list)
    relevant: list[int] = field(default_factory=list)  # 0-based evidence indices


class _Agent:
    def __init__(self, req: AskRequest) -> None:
        settings = get_settings()
        timeout = float(getattr(settings, "agent_timeout_s", DEFAULT_TIMEOUT_S))
        now = asyncio.get_running_loop().time()
        self.req = req
        self.question = req.question.strip()
        self.max_steps = req.max_steps or settings.agent_max_steps
        self.deadline = now + timeout
        self.loop_deadline = now + timeout * LOOP_SHARE
        self.engines = _Engines(req)
        self.steps: list[AgentStep] = []  # recorded steps (answer deltas excluded)
        self.evidence: list[SearchHit] = []  # evidence number n == index + 1
        self.seen: set[int] = set()
        self.issued: list[str] = []
        self.expanded: dict[int, str] = {}  # chunk_id -> text widened with neighbours
        self.verdict = _Verdict()
        self.route = "research"
        self.rounds = 0
        self.answer = ""
        self.citations: list[Citation] = []
        self.engine_label = "none"
        self._fresh = 0  # chunks added by the latest search round

    # -- helpers ------------------------------------------------------------------

    def _step(self, type_: str, message: str = "", **kw: Any) -> AgentStep:
        step = AgentStep(type=type_, message=message, **kw)  # type: ignore[arg-type]
        if type_ != "answer_delta":
            self.steps.append(step)
        return step

    def _errors(self) -> list[AgentStep]:
        out = [self._step("error", msg) for msg in self.engines.errors]
        self.engines.errors.clear()
        return out

    def _time_left(self, deadline: float) -> bool:
        return asyncio.get_running_loop().time() < deadline

    # -- main flow ----------------------------------------------------------------

    async def run(self) -> AsyncGenerator[AgentStep, None]:
        for err in self._errors():
            yield err

        route, queries, reason = await self._route()
        for err in self._errors():
            yield err
        self.route = route
        yield self._step(
            "plan", reason or f"Route: {route}", data={"route": route, "queries": queries}
        )

        if route == "chitchat":
            messages: list[ChatMessage] = [
                {"role": "system", "content": P.CHITCHAT_SYSTEM},
                {"role": "user", "content": self.question},
            ]
            async with aclosing(
                self._generate(messages, fallback=lambda: _CHITCHAT_FALLBACK)
            ) as steps:
                async for step in steps:
                    yield step
            return

        while queries and self.rounds < self.max_steps:
            self.rounds += 1
            async with aclosing(self._search(queries)) as steps:
                async for step in steps:
                    yield step
            if route == "direct_lookup":
                break
            if self.rounds > 1 and not self._fresh:
                yield self._step("reflect", "No new evidence found; stopping the search.")
                break
            if not self._time_left(self.loop_deadline):
                yield self._step(
                    "error", "Search time budget exhausted; answering with what was found."
                )
                break
            self.verdict = await self._reflect()
            for err in self._errors():
                yield err
            v = self.verdict
            yield self._step(
                "reflect",
                "Evidence is sufficient."
                if v.sufficient
                else (v.missing or "Evidence is incomplete."),
                data={
                    "sufficient": v.sufficient,
                    "missing": v.missing,
                    "next_queries": v.next_queries,
                    "relevant": [i + 1 for i in v.relevant],
                    "step": self.rounds,
                },
            )
            if v.sufficient:
                break
            queries = v.next_queries[:MAX_QUERIES]

        sources = self._select_sources()
        async with aclosing(self._read(sources)) as steps:
            async for step in steps:
                yield step
        async with aclosing(self._synthesize(sources)) as steps:
            async for step in steps:
                yield step

    # -- route --------------------------------------------------------------------

    async def _route(self) -> tuple[str, list[str], str]:
        q = self.question
        try:
            data = await self.engines.complete_json(
                [
                    {"role": "system", "content": P.ROUTER_SYSTEM},
                    {"role": "user", "content": P.ROUTER_USER.format(question=q)},
                ],
                P.ROUTER_SCHEMA,
                self.loop_deadline,
            )
        except TimeoutError:
            self.engines.errors.append("Router timed out; searching the question as asked.")
            data = None
        except ProviderError:
            data = None
        data = data or {}
        route = data.get("route")
        if route not in P.ROUTES:
            route = "research"
        rewrites = data.get("queries")
        rewrites = [x for x in rewrites if isinstance(x, str)] if isinstance(rewrites, list) else []
        queries = _dedupe([q, *rewrites], exclude=())[:MAX_QUERIES]
        reason = data.get("reason")
        return str(route), queries, reason.strip() if isinstance(reason, str) else ""

    # -- search -------------------------------------------------------------------

    async def _search(self, queries: Sequence[str]) -> AsyncGenerator[AgentStep, None]:
        self.issued.extend(queries)
        filters = self.req.filters

        async def one(q: str) -> SearchResponse | BaseException:
            try:
                async with asyncio.timeout_at(self.deadline):
                    return await _run_search(
                        SearchRequest(
                            query=q[:2000],
                            mode="hybrid",
                            limit=SEARCH_LIMIT,
                            filters=filters,
                            rerank=True,
                        )
                    )
            except Exception as exc:  # one failing query must not sink the others
                log.warning("agent.search_failed", query=q, error=repr(exc))
                return exc

        async with asyncio.TaskGroup() as tg:
            tasks = [tg.create_task(one(q)) for q in queries]

        merged: dict[int, SearchHit] = {}
        for q, task in zip(queries, tasks, strict=True):
            result = task.result()
            if isinstance(result, BaseException):
                detail = (
                    "timed out"
                    if isinstance(result, TimeoutError)
                    else str(result) or type(result).__name__
                )
                yield self._step("error", f"Search failed: {detail}", query=q)
                continue
            fresh = [h for h in result.hits if h.chunk_id not in self.seen]
            for hit in fresh:
                if hit.chunk_id not in merged or hit.score > merged[hit.chunk_id].score:
                    merged[hit.chunk_id] = hit
            yield self._step(
                "search",
                f"{len(fresh)} new of {len(result.hits)} results",
                query=q,
                hits=fresh,
                data={
                    "step": self.rounds,
                    "returned": len(result.hits),
                    "degraded": result.degraded,
                },
            )
        new = sorted(merged.values(), key=lambda h: h.score, reverse=True)
        new = new[: max(0, MAX_EVIDENCE - len(self.evidence))]
        self.evidence.extend(new)
        self.seen.update(h.chunk_id for h in new)
        self._fresh = len(new)

    # -- reflect ------------------------------------------------------------------

    async def _reflect(self) -> _Verdict:
        ranked = sorted(range(len(self.evidence)), key=lambda i: -self.evidence[i].score)
        lines: list[str] = []
        shown: set[int] = set()
        budget = REFLECT_CHARS
        for i in ranked:
            hit = self.evidence[i]
            line = f"[{i + 1}] {_heading(hit)}: {_clip(hit.content, REFLECT_ITEM_CHARS)}"
            if len(line) > budget and lines:
                break
            budget -= len(line)
            lines.append(line)
            shown.add(i)
        prompt = P.REFLECT_USER.format(
            question=self.question,
            evidence="\n".join(lines) or "(no evidence found)",
            issued="; ".join(f'"{q}"' for q in self.issued[-8:]),
        )
        try:
            data = await self.engines.complete_json(
                [
                    {"role": "system", "content": P.REFLECT_SYSTEM},
                    {"role": "user", "content": prompt},
                ],
                P.REFLECT_SCHEMA,
                self.loop_deadline,
            )
        except TimeoutError:
            self.engines.errors.append("Evidence check timed out; answering with what was found.")
            return _Verdict()
        except ProviderError:
            return _Verdict()
        if data is None:  # unparseable: answer with what we have rather than loop blindly
            return _Verdict()

        sufficient = data.get("sufficient")
        relevant_raw = data.get("relevant")
        relevant: list[int] = []
        if isinstance(relevant_raw, list):
            for n in relevant_raw:
                ok = isinstance(n, int) and not isinstance(n, bool) and (n - 1) in shown
                if ok and n - 1 not in relevant:
                    relevant.append(n - 1)
        next_raw = data.get("next_queries")
        next_queries = (
            _dedupe([x for x in next_raw if isinstance(x, str)], exclude=self.issued)[:3]
            if isinstance(next_raw, list)
            else []
        )
        missing = data.get("missing")
        return _Verdict(
            sufficient=sufficient if isinstance(sufficient, bool) else True,
            missing=missing.strip()[:500] if isinstance(missing, str) else "",
            next_queries=next_queries,
            relevant=relevant,
        )

    # -- read ---------------------------------------------------------------------

    def _select_sources(self) -> list[SearchHit]:
        """Graded-relevant evidence first (or best-scored when ungraded), packed to budget."""
        if self.verdict.relevant:
            order = self.verdict.relevant
        else:
            order = sorted(range(len(self.evidence)), key=lambda i: -self.evidence[i].score)
        out: list[SearchHit] = []
        budget = ANSWER_CHARS
        for i in order[:MAX_SOURCES]:
            cost = min(len(self.evidence[i].content), SOURCE_CHARS)
            if cost > budget and out:
                break
            budget -= cost
            out.append(self.evidence[i])
        return out

    async def _read(self, sources: list[SearchHit]) -> AsyncGenerator[AgentStep, None]:
        top = sources[:READ_TOP]
        if not top or not self._time_left(self.loop_deadline):
            return
        try:
            async with asyncio.timeout_at(self.loop_deadline):
                widened = await _neighbours(top)
        except Exception as exc:  # optional enrichment: never fail the answer over it
            log.debug("agent.read_skipped", error=repr(exc))
            return
        if not widened:
            return
        self.expanded.update(widened)
        yield self._step(
            "read",
            f"Read surrounding context of {len(widened)} passage(s)",
            data={"chunk_ids": list(widened)},
        )

    # -- answer -------------------------------------------------------------------

    async def _synthesize(self, sources: list[SearchHit]) -> AsyncGenerator[AgentStep, None]:
        if not sources:
            self.answer = P.NOT_FOUND
            yield self._step("answer_delta", self.answer)
            yield self._step("answer", self.answer, data={"citations": 0, "engine": "none"})
            return
        blocks = [
            f"[{n}] {_heading(h)}\n{_clip(self.expanded.get(h.chunk_id, h.content), SOURCE_CHARS, flat=False)}"
            for n, h in enumerate(sources, 1)
        ]
        note = ""
        if not self.verdict.sufficient:
            missing = f" Missing: {self.verdict.missing}" if self.verdict.missing else ""
            note = (
                f"\n\nNote: the sources may not cover everything.{missing} Say what is not covered."
            )
        messages: list[ChatMessage] = [
            {"role": "system", "content": P.ANSWER_SYSTEM},
            {
                "role": "user",
                "content": P.ANSWER_USER.format(
                    sources="\n\n".join(blocks), question=self.question, note=note
                ),
            },
        ]
        async with aclosing(
            self._generate(messages, fallback=lambda: _extractive(sources))
        ) as steps:
            async for step in steps:
                yield step
        self.citations = _citations(self.answer, sources)
        self.steps[-1].data["citations"] = len(self.citations)

    async def _generate(
        self, messages: list[ChatMessage], fallback: Any
    ) -> AsyncGenerator[AgentStep, None]:
        """Stream an answer through the engine chain, falling back to ``fallback()``."""
        parts: list[str] = []
        while self.engines.chain and not parts:
            engine = self.engines.chain[0]
            think = ThinkFilter()
            try:
                async with aclosing(
                    cast(
                        AsyncGenerator[str, None],
                        engine.llm.stream(messages, max_tokens=1024, temperature=0.1),
                    )
                ) as stream:
                    it = aiter(stream)
                    while True:
                        try:
                            async with asyncio.timeout_at(self.deadline):
                                delta = await anext(it)
                        except StopAsyncIteration:
                            break
                        if text := think.feed(delta):
                            parts.append(text)
                            yield self._step("answer_delta", text)
                if tail := think.flush():
                    parts.append(tail)
                    yield self._step("answer_delta", tail)
                self.engine_label = engine.label
                break
            except ProviderError as exc:
                if parts:
                    self.engine_label = engine.label
                    yield self._step("error", f"Answer interrupted: {exc}")
                    break
                self.engines.fail(exc)
                for err in self._errors():
                    yield err
            except TimeoutError:
                yield self._step("error", "Answer generation timed out.")
                if parts:
                    self.engine_label = engine.label
                break
        for err in self._errors():
            yield err
        self.answer = "".join(parts).strip()
        if not self.answer:
            self.answer = fallback()
            self.engine_label = "extractive"
            yield self._step("answer_delta", self.answer)
        yield self._step("answer", self.answer, data={"engine": self.engine_label})


_CHITCHAT_FALLBACK = "Hi! Ask me anything about the documents in your knowledge base."


# --- module helpers ------------------------------------------------------------------


async def _run_search(req: SearchRequest) -> SearchResponse:
    """Indirection over the search pipeline (imported lazily; patched in tests)."""
    from kb.search.pipeline import search

    return await search(req)


async def _neighbours(hits: Sequence[SearchHit]) -> dict[int, str]:
    """Each hit's text widened with the previous and next chunk of the same document."""
    from kb.db import connection

    async with connection() as conn:
        cur = await conn.execute(
            """
            select t.chunk_id, c.ord - t.ord as delta, c.content
            from unnest(%s::bigint[], %s::uuid[], %s::int[]) as t(chunk_id, document_id, ord)
            join chunks c on c.document_id = t.document_id
                         and c.ord between t.ord - 1 and t.ord + 1
                         and c.ord <> t.ord
            order by t.chunk_id, c.ord
            """,
            ([h.chunk_id for h in hits], [h.document_id for h in hits], [h.ord for h in hits]),
        )
        rows = cast(list[dict[str, Any]], await cur.fetchall())
    around: dict[int, dict[int, str]] = {}
    for row in rows:
        around.setdefault(row["chunk_id"], {})[row["delta"]] = row["content"]
    out: dict[int, str] = {}
    for hit in hits:
        ctx = around.get(hit.chunk_id)
        if not ctx:
            continue
        before = ctx.get(-1, "")[-NEIGHBOUR_CHARS:]
        after = ctx.get(1, "")[:NEIGHBOUR_CHARS]
        out[hit.chunk_id] = "\n".join(
            p for p in (before and f"…{before}", hit.content, after and f"{after}…") if p
        )
    return out


def _citations(answer: str, sources: Sequence[SearchHit]) -> list[Citation]:
    out: list[Citation] = []
    for n in cited_numbers(answer):
        if 1 <= n <= len(sources):
            hit = sources[n - 1]
            out.append(
                Citation(
                    n=n,
                    chunk_id=hit.chunk_id,
                    document_id=hit.document_id,
                    document_title=hit.document_title,
                    heading_path=hit.heading_path,
                    snippet=hit.snippet or _clip(hit.content, 300),
                )
            )
    return out


def _extractive(sources: Sequence[SearchHit]) -> str:
    lines = [
        "The language model is unavailable, so here are the most relevant passages "
        "from the knowledge base:",
        "",
    ]
    for n, hit in enumerate(sources[:3], 1):
        lines.append(f"- **{hit.document_title}**: {_clip(hit.content, 300)} [{n}]")
    return "\n".join(lines)


def _heading(hit: SearchHit) -> str:
    return f"{hit.document_title} › {hit.heading_path}" if hit.heading_path else hit.document_title


def _clip(text: str, limit: int, *, flat: bool = True) -> str:
    if flat:
        text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _norm(text: str) -> str:
    return " ".join(text.lower().split())


def _dedupe(queries: Sequence[str], exclude: Sequence[str]) -> list[str]:
    seen = {_norm(q) for q in exclude}
    out: list[str] = []
    for q in queries:
        q = " ".join(q.split())[:2000]
        key = _norm(q)
        if key and key not in seen:
            seen.add(key)
            out.append(q)
    return out


def _ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)
