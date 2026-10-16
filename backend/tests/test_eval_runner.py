"""Evaluation harness end to end on a tiny seeded corpus."""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from kb.providers.base import LLM
from kb.providers.fake import FakeEmbedder

SLUG = f"eval-{uuid.uuid4().hex[:6]}"
DOCS = {
    "solar": "Solar panels convert sunlight into electricity using photovoltaic cells on the roof.",
    "coffee": "Espresso is brewed by forcing hot water through finely ground coffee beans under pressure.",
    "chess": "The Sicilian defence is an aggressive chess opening that answers e4 with c5 for black.",
}


@pytest.fixture(scope="module")
async def seeded(app: object) -> dict[str, Any]:
    from kb.db import connection

    emb = FakeEmbedder(256)
    out: dict[str, Any] = {}
    async with connection() as conn, conn.transaction():
        cur = await conn.execute(
            "insert into collections (slug, name) values (%s, %s) returning id", (SLUG, SLUG)
        )
        col = (await cur.fetchone())["id"]
        for key, text in DOCS.items():
            cur = await conn.execute(
                "insert into documents (collection_id, title, content_hash, status)"
                " values (%s, %s, %s, 'ready') returning id",
                (col, key.title(), f"{SLUG}-{key}"),
            )
            doc = (await cur.fetchone())["id"]
            vec = (await emb.embed_documents([text]))[0]
            cur = await conn.execute(
                "insert into chunks (document_id, collection_id, ord, content, embedding)"
                " values (%s, %s, 0, %s, %s::halfvec) returning id",
                (doc, col, text, str(vec)),
            )
            out[key] = {"doc": str(doc), "chunk": (await cur.fetchone())["id"]}
    return out


async def test_runner_compares_configs(seeded, tmp_path, capsys):
    from kb.eval.runner import evaluate, format_table, load_qrels

    path = tmp_path / "qrels.jsonl"
    filters = {"collections": [SLUG]}
    lines = [
        {
            "query": "photovoltaic solar electricity",
            "relevant_document_ids": [seeded["solar"]["doc"]],
            "filters": filters,
        },
        {
            "query": "espresso coffee beans",
            "relevant_chunk_ids": [seeded["coffee"]["chunk"]],
            "filters": filters,
        },
        {
            "query": "sicilian chess opening",
            "relevant_document_ids": [seeded["chess"]["doc"]],
            "filters": filters,
        },
    ]
    path.write_text("# comment\n" + "\n".join(json.dumps(x) for x in lines) + "\n\n")
    qrels = load_qrels(path)
    assert len(qrels) == 3
    results = await evaluate(
        qrels, ["lexical", "semantic", "hybrid", "hybrid-convex", "hybrid+rerank"], ks=(1, 10)
    )
    for r in results:
        assert r.metrics["recall@1"] == 1.0, (r.name, r.per_query)
        assert r.metrics["mrr@10"] == 1.0 and r.metrics["ndcg@10"] == 1.0
        assert r.degraded == 0
    table = format_table(results)
    assert "hybrid+rerank" in table and "ndcg@10" in table
    with pytest.raises(ValueError):
        await evaluate(qrels, ["nope"])


class _QuestionLLM(LLM):
    name = "llm:question"
    model = "q"

    async def complete(self, messages, **kw):
        passage = messages[-1]["content"]
        if "Espresso" in passage:
            # Copies the passage verbatim → must be filtered out.
            return json.dumps({"question": "forcing hot water through finely ground coffee beans"})
        if "Solar" in passage:
            raise RuntimeError("model crashed")
        return json.dumps(
            {"question": "What should black play against 1.e4 to fight for the center?"}
        )

    async def stream(self, messages, **kw):  # pragma: no cover
        yield ""


async def test_generate_writes_filtered_qrels(seeded, tmp_path):
    from kb.eval.generate import generate
    from kb.eval.runner import load_qrels

    out = tmp_path / "gen.jsonl"
    written = await generate(1000, out, collection=SLUG, llm=_QuestionLLM(), seed="x", min_chars=10)
    assert written == 1
    qrels = load_qrels(out)
    assert qrels[0].level == "chunk"
    assert qrels[0].relevant == {str(seeded["chess"]["chunk"]): 1.0}
    row = json.loads(out.read_text())
    assert row["relevant_document_ids"] == [seeded["chess"]["doc"]]


def test_cli_parser():
    from kb.eval.__main__ import _parser

    args = _parser().parse_args(["run", "--qrels", "q.jsonl", "--configs", "lexical,hybrid"])
    assert args.cmd == "run" and args.configs == "lexical,hybrid"
    args = _parser().parse_args(["generate", "--out", "o.jsonl", "-n", "5", "--engine", "cloud"])
    assert args.n == 5 and args.engine == "cloud"
