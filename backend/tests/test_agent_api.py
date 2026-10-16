"""HTTP / SSE tests for /api/ask and the DB-backed "read" step of the agent."""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from test_agent_loop import ROUTE, FakeSearch, ScriptedLLM, hit, install

from kb.agent import loop
from kb.schemas import AskRequest, AskResponse, SearchHit


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    loop.clear_cache()


def parse_sse(text: str) -> list[tuple[str, Any]]:
    events: list[tuple[str, Any]] = []
    for block in text.replace("\r\n", "\n").split("\n\n"):
        event, data = "message", []
        for line in block.split("\n"):
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].lstrip())
        if data:
            events.append((event, json.loads("\n".join(data))))
    return events


async def test_ask_endpoint(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    install(monkeypatch, ScriptedLLM(ROUTE), FakeSearch({"*": [hit(1, "Paris is the capital.")]}))
    resp = await client.post("/api/ask", json={"question": "Capital of France?"})
    assert resp.status_code == 200
    body = AskResponse.model_validate(resp.json())
    assert body.answer == "Paris is the capital [1]."
    assert body.citations[0].chunk_id == 1


async def test_ask_stream_endpoint(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    install(monkeypatch, ScriptedLLM(ROUTE), FakeSearch({"*": [hit(1, "Paris is the capital.")]}))
    resp = await client.post("/api/ask/stream", json={"question": "Capital of France?"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(resp.text)
    names = [e for e, _ in events]
    assert names[-1] == "done" and set(names[:-1]) == {"step"}
    step_types = [d["type"] for e, d in events if e == "step"]
    assert step_types[0] == "plan" and "search" in step_types and step_types[-1] == "answer"
    deltas = "".join(d["message"] for e, d in events if e == "step" and d["type"] == "answer_delta")
    done = AskResponse.model_validate(events[-1][1])
    assert deltas == done.answer == "Paris is the capital [1]."


async def test_ask_stream_reports_errors(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    async def explode(req: Any) -> Any:
        raise RuntimeError("boom")
        yield  # pragma: no cover

    monkeypatch.setattr("kb.api.ask.ask_stream", explode)
    resp = await client.post("/api/ask/stream", json={"question": "x"})
    assert parse_sse(resp.text) == [("error", {"detail": "boom"})]


async def test_ask_validation(client: Any) -> None:
    resp = await client.post("/api/ask", json={"question": ""})
    assert resp.status_code == 422


async def test_read_step_widens_context(app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from kb.db import connection

    async with connection() as conn:
        cur = await conn.execute(
            """
            insert into documents (collection_id, title, content_hash, status)
            select id, 'Guide', %s, 'ready' from collections where slug = 'default'
            returning id, collection_id
            """,
            (uuid.uuid4().hex,),
        )
        doc = await cur.fetchone()
        ids = []
        for ord_, text in enumerate(
            ["Before: install brew.", "Run kb serve.", "After: open the UI."]
        ):
            cur = await conn.execute(
                "insert into chunks (document_id, collection_id, ord, content) values (%s, %s, %s, %s) returning id",
                (doc["id"], doc["collection_id"], ord_, text),
            )
            ids.append((await cur.fetchone())["id"])

    middle = SearchHit(
        **hit(ids[1], "Run kb serve.").model_dump() | {"document_id": doc["id"], "ord": 1}
    )
    llm = ScriptedLLM(ROUTE)
    install(monkeypatch, llm, FakeSearch({"*": [middle]}))
    resp = await loop.ask(AskRequest(question="How do I start kb?"))

    read = [s for s in resp.steps if s.type == "read"]
    assert read and read[0].data["chunk_ids"] == [ids[1]]
    prompt = llm.answer_prompts[0]
    assert (
        prompt.index("install brew") < prompt.index("Run kb serve.") < prompt.index("open the UI")
    )
