"""Agent loop tests with a scripted LLM and a fake search backend (no DB or models needed)."""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest

from kb.agent import loop
from kb.agent.parsing import ThinkFilter, cited_numbers, parse_json_object, strip_think
from kb.providers import ProviderError
from kb.providers.fake import FakeLLM
from kb.schemas import AgentStep, AskRequest, AskResponse, SearchHit, SearchRequest, SearchResponse

DOC_A, DOC_B = uuid.uuid4(), uuid.uuid4()


def hit(chunk_id: int, content: str, score: float = 1.0, doc: uuid.UUID = DOC_A) -> SearchHit:
    return SearchHit(
        chunk_id=chunk_id,
        document_id=doc,
        document_title=f"Doc {chunk_id}",
        collection="default",
        source_uri=None,
        heading_path="Section",
        content=content,
        snippet=content[:50],
        score=score,
        ord=0,
    )


class FakeSearch:
    """Maps a query (or ``*`` for anything else) to hits and records every request."""

    def __init__(self, results: dict[str, list[SearchHit]]) -> None:
        self.results = results
        self.calls: list[SearchRequest] = []

    async def __call__(self, req: SearchRequest) -> SearchResponse:
        self.calls.append(req)
        hits = self.results.get(req.query, self.results.get("*", []))
        return SearchResponse(query=req.query, mode=req.mode, hits=hits, timings=[], total_ms=1.0)

    @property
    def queries(self) -> list[str]:
        return [c.query for c in self.calls]


class ScriptedLLM(FakeLLM):
    """Returns scripted router / reflection JSON and streams a fixed answer."""

    def __init__(
        self,
        route: dict[str, Any] | None = None,
        reflects: list[dict[str, Any]] | None = None,
        answer: str = "Paris is the capital [1].",
        fail: bool = False,
    ) -> None:
        self.route = route
        self.reflects = list(reflects or [])
        self.answer = answer
        self.fail = fail
        self.reflect_prompts: list[str] = []
        self.answer_prompts: list[str] = []
        self.models: list[str | None] = []
        self.stream_closed = False

    async def complete(self, messages: Any, **kw: Any) -> str:
        if self.fail:
            raise ProviderError("connection refused")
        schema = kw.get("json_schema") or {}
        self.models.append(kw.get("model"))
        props = schema.get("properties", {})
        if "route" in props and self.route is not None:
            return json.dumps(self.route)
        if "sufficient" in props and self.reflects:
            self.reflect_prompts.append(messages[-1]["content"])
            return json.dumps(self.reflects.pop(0) if len(self.reflects) > 1 else self.reflects[0])
        return await super().complete(messages, **kw)

    async def stream(self, messages: Any, **kw: Any) -> AsyncIterator[str]:
        if self.fail:
            raise ProviderError("connection refused")
        self.answer_prompts.append(messages[-1]["content"])
        try:
            for piece in re.split(r"(?<= )", self.answer):
                if piece:
                    yield piece
        finally:
            self.stream_closed = True


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    loop.clear_cache()


def install(monkeypatch: pytest.MonkeyPatch, llm: Any, search: FakeSearch) -> None:
    def get_llm(kind: str = "local") -> Any:
        if kind == "cloud":
            raise ProviderError("KB_ANTHROPIC_API_KEY is not set")
        return llm

    monkeypatch.setattr(loop, "get_llm", get_llm)
    monkeypatch.setattr(loop, "_run_search", search)


def types(resp: AskResponse) -> list[str]:
    return [s.type for s in resp.steps]


ROUTE = {
    "route": "research",
    "queries": ["capital france", "What is the capital city of France?"],
    "reason": "fact",
}


async def test_route_search_sufficient_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    search = FakeSearch(
        {"*": [hit(1, "Paris is the capital of France.", 0.9), hit(2, "Lyon is big.", 0.5)]}
    )
    llm = ScriptedLLM(
        ROUTE, [{"relevant": [1], "sufficient": True, "missing": "", "next_queries": []}]
    )
    install(monkeypatch, llm, search)

    resp = await loop.ask(AskRequest(question="What is the capital of France?"))

    assert search.queries == ["What is the capital of France?", *ROUTE["queries"]]
    assert all(c.rerank and c.mode == "hybrid" and c.limit == 8 for c in search.calls)
    assert types(resp) == ["plan", "search", "search", "search", "reflect", "answer"]
    assert resp.steps[0].data["route"] == "research"
    # Overlapping hits across queries of one round are merged into a single evidence pool.
    assert [len(s.hits or []) for s in resp.steps if s.type == "search"] == [2, 2, 2]
    assert resp.answer == "Paris is the capital [1]."
    assert [(c.n, c.chunk_id) for c in resp.citations] == [(1, 1)]
    assert resp.engine == "local"
    # The grader restricted the sources to evidence [1].
    assert "Lyon" not in llm.answer_prompts[0]
    assert "Paris is the capital of France." in llm.answer_prompts[0]


async def test_insufficient_triggers_second_round(monkeypatch: pytest.MonkeyPatch) -> None:
    search = FakeSearch(
        {
            "population of paris": [hit(3, "Paris has 2.1 million people.", 0.8)],
            "*": [hit(1, "Paris is the capital.")],
        }
    )
    llm = ScriptedLLM(
        {"route": "research", "queries": [], "reason": ""},
        [
            {
                "relevant": [1],
                "sufficient": False,
                "missing": "population",
                "next_queries": ["population of paris"],
            },
            {"relevant": [1, 2], "sufficient": True, "missing": "", "next_queries": []},
        ],
        answer="Paris [1] has 2.1 million people [2].",
    )
    install(monkeypatch, llm, search)

    resp = await loop.ask(AskRequest(question="Capital of France and its population?"))

    assert search.queries == ["Capital of France and its population?", "population of paris"]
    assert types(resp) == ["plan", "search", "reflect", "search", "reflect", "answer"]
    assert resp.steps[2].data["sufficient"] is False
    assert [(c.n, c.chunk_id) for c in resp.citations] == [(1, 1), (2, 3)]
    # Evidence numbering is stable across rounds: the second grader sees [1] and [2].
    assert "[1] Doc 1" in llm.reflect_prompts[1] and "[2] Doc 3" in llm.reflect_prompts[1]


async def test_seen_chunks_dropped_and_repeated_queries_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    search = FakeSearch(
        {"more": [hit(1, "old chunk", 0.9), hit(4, "new chunk", 0.7)], "*": [hit(1, "old chunk")]}
    )
    llm = ScriptedLLM(
        {"route": "research", "queries": [], "reason": ""},
        [
            # "Q?" was already issued and must be dropped; "more" is new.
            {
                "relevant": [],
                "sufficient": False,
                "missing": "x",
                "next_queries": ["q?", "more", "MORE"],
            },
            {"relevant": [], "sufficient": False, "missing": "x", "next_queries": ["Q?", "more"]},
        ],
    )
    install(monkeypatch, llm, search)

    resp = await loop.ask(AskRequest(question="Q?", max_steps=4))

    assert search.queries == ["Q?", "more"]  # the third round had no new queries
    second = [s for s in resp.steps if s.type == "search"][1]
    assert [h.chunk_id for h in second.hits or []] == [4]
    assert second.data["returned"] == 2


async def test_no_new_chunks_stops_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    search = FakeSearch({"*": [hit(1, "same")]})
    llm = ScriptedLLM(
        {"route": "research", "queries": [], "reason": ""},
        [{"relevant": [], "sufficient": False, "missing": "x", "next_queries": ["another"]}],
    )
    install(monkeypatch, llm, search)
    resp = await loop.ask(AskRequest(question="Q?", max_steps=4))
    assert search.queries == ["Q?", "another"]
    assert "No new evidence" in [s for s in resp.steps if s.type == "reflect"][-1].message


async def test_max_steps_respected(monkeypatch: pytest.MonkeyPatch) -> None:
    counter = iter(range(100, 200))

    class Growing(FakeSearch):
        async def __call__(self, req: SearchRequest) -> SearchResponse:
            self.results = {"*": [hit(next(counter), "fresh")]}
            return await super().__call__(req)

    reflects = [
        {"relevant": [], "sufficient": False, "missing": "x", "next_queries": [f"q{i}"]}
        for i in range(10)
    ]
    search = Growing({})
    install(
        monkeypatch,
        ScriptedLLM({"route": "research", "queries": [], "reason": ""}, reflects),
        search,
    )

    resp = await loop.ask(AskRequest(question="Q?", max_steps=2))
    assert search.queries == ["Q?", "q0"]
    assert types(resp).count("reflect") == 2

    loop.clear_cache()
    search.calls.clear()
    monkeypatch.setattr(loop.get_settings(), "agent_max_steps", 3)
    await loop.ask(AskRequest(question="Q?"))
    assert len(search.calls) == 3


async def test_local_llm_down_gives_extractive_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    search = FakeSearch({"*": [hit(1, "Alpha fact.", 0.9), hit(2, "Beta fact.", 0.8)]})
    install(monkeypatch, ScriptedLLM(fail=True), search)

    resp = await loop.ask(AskRequest(question="Tell me about alpha"))

    assert search.queries == ["Tell me about alpha"]  # router default: the question itself
    assert resp.engine == "extractive"
    errors = [s.message for s in resp.steps if s.type == "error"]
    assert errors and "connection refused" in errors[0]
    assert "Alpha fact. [1]" in resp.answer and "Beta fact. [2]" in resp.answer
    assert [(c.n, c.chunk_id) for c in resp.citations] == [(1, 1), (2, 2)]
    # Responses produced in degraded mode are not cached.
    assert not loop._cache


async def test_cloud_engine_unavailable_falls_back_to_local(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install(monkeypatch, ScriptedLLM(ROUTE), FakeSearch({"*": [hit(1, "Paris.")]}))
    resp = await loop.ask(AskRequest(question="Capital?", engine="cloud"))
    assert resp.steps[0].type == "error" and "ANTHROPIC" in resp.steps[0].message
    assert resp.engine == "local"


async def test_router_uses_router_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(loop.get_settings(), "llm_provider", "openai")
    llm = ScriptedLLM(
        ROUTE, [{"relevant": [1], "sufficient": True, "missing": "", "next_queries": []}]
    )
    install(monkeypatch, llm, FakeSearch({"*": [hit(1, "Paris.")]}))
    await loop.ask(AskRequest(question="Capital?"))
    assert set(llm.models) == {loop.get_settings().llm_router_model}


async def test_citations_follow_source_order(monkeypatch: pytest.MonkeyPatch) -> None:
    search = FakeSearch(
        {"*": [hit(10, "ten", 0.9), hit(20, "twenty", 0.8), hit(30, "thirty", 0.7)]}
    )
    llm = ScriptedLLM(
        ROUTE,
        [{"relevant": [3, 1, 99], "sufficient": True, "missing": "", "next_queries": []}],
        answer="Thirty [1], ten [2][3] and [1, 2].",
    )
    install(monkeypatch, llm, search)
    resp = await loop.ask(AskRequest(question="numbers"))
    # Sources are renumbered in grader order: [1] = chunk 30, [2] = chunk 10; [3] is invalid.
    assert [(c.n, c.chunk_id) for c in resp.citations] == [(1, 30), (2, 10)]
    assert resp.citations[0].snippet == "thirty"


async def test_chitchat_skips_search(monkeypatch: pytest.MonkeyPatch) -> None:
    search = FakeSearch({})
    llm = ScriptedLLM(
        {"route": "chitchat", "queries": ["hi"], "reason": "greeting"}, answer="Hello there!"
    )
    install(monkeypatch, llm, search)
    resp = await loop.ask(AskRequest(question="hi!"))
    assert search.calls == []
    assert types(resp) == ["plan", "answer"]
    assert resp.answer == "Hello there!" and resp.citations == []


async def test_no_evidence_says_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    llm = ScriptedLLM({"route": "direct_lookup", "queries": ["x"], "reason": ""})
    install(monkeypatch, llm, FakeSearch({}))
    resp = await loop.ask(AskRequest(question="Where is the unicorn doc?"))
    assert resp.answer == loop.P.NOT_FOUND
    assert "reflect" not in types(resp)  # direct lookups are single-shot
    assert llm.answer_prompts == []


async def test_garbage_json_uses_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    class Garbage(ScriptedLLM):
        async def complete(self, messages: Any, **kw: Any) -> str:
            return "<think>hmm</think> not json at all"

    search = FakeSearch({"*": [hit(1, "fact")]})
    install(monkeypatch, Garbage(answer="<think>let me see</think>\n\nFact [1]."), search)
    resp = await loop.ask(AskRequest(question="Q?"))
    assert search.queries == ["Q?"]
    assert resp.answer == "Fact [1]."
    assert len(resp.citations) == 1


async def test_stream_emits_deltas_and_cache_replays(monkeypatch: pytest.MonkeyPatch) -> None:
    search = FakeSearch({"*": [hit(1, "Paris.")]})
    install(monkeypatch, ScriptedLLM(ROUTE, answer="It is Paris [1]."), search)

    items = [i async for i in loop.ask_stream(AskRequest(question="Capital?"))]
    assert isinstance(items[-1], AskResponse)
    deltas = [i.message for i in items if isinstance(i, AgentStep) and i.type == "answer_delta"]
    assert "".join(deltas) == "It is Paris [1]."
    assert len(deltas) > 1
    assert "answer_delta" not in types(items[-1])

    calls = len(search.calls)
    again = await loop.ask(AskRequest(question="  capital? "))
    assert len(search.calls) == calls  # served from cache
    assert again.answer == items[-1].answer


async def test_abandoned_stream_cleans_up(monkeypatch: pytest.MonkeyPatch) -> None:
    llm = ScriptedLLM(ROUTE, answer="one two three four five")
    install(monkeypatch, llm, FakeSearch({"*": [hit(1, "x")]}))
    before = asyncio.all_tasks()
    gen = loop.ask_stream(AskRequest(question="Q?"))
    async for item in gen:
        if isinstance(item, AgentStep) and item.type == "answer_delta":
            break
    await gen.aclose()
    assert llm.stream_closed
    assert asyncio.all_tasks() == before


async def test_search_failure_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    async def broken(req: SearchRequest) -> SearchResponse:
        raise RuntimeError("index offline")

    install(monkeypatch, ScriptedLLM(ROUTE), FakeSearch({}))
    monkeypatch.setattr(loop, "_run_search", broken)
    resp = await loop.ask(AskRequest(question="Q?"))
    assert any("index offline" in s.message for s in resp.steps if s.type == "error")
    assert resp.answer == loop.P.NOT_FOUND


# --- parsing helpers -----------------------------------------------------------------


def test_parse_json_object_tolerates_noise() -> None:
    assert parse_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_object('<think>{"no": 1}</think>Sure: {"a": [1]} done') == {"a": [1]}
    assert parse_json_object('reasoning...</think>{"a": 2}') == {"a": 2}
    assert parse_json_object("nope") is None
    assert parse_json_object("[1, 2]") is None
    assert strip_think("<think>unterminated") == ""


def test_think_filter_across_deltas() -> None:
    f = ThinkFilter()
    pieces = ["<thi", "nk>secret ", "stuff</th", "ink>\n\nHello", " <", "b>world</b>"]
    out = "".join(f.feed(p) for p in pieces) + f.flush()
    assert out == "Hello <b>world</b>"


def test_cited_numbers() -> None:
    assert cited_numbers("a [2] b [1, 3][2] c [x] [10]") == [2, 1, 3, 10]
