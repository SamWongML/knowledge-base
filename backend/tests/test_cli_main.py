"""CLI: output shapes, --json, exit codes and error reporting against a mocked API."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import respx
from typer.testing import CliRunner

from kb.cli.main import app

BASE = "http://kb.test"
DOC_ID = "11111111-1111-4111-8111-111111111111"
ENV = {"KB_URL": BASE, "KB_TOKEN": "tok", "KB_OUTPUT": ""}

DOC = {
    "id": DOC_ID,
    "collection_id": "22222222-2222-4222-8222-222222222222",
    "collection": "default",
    "title": "Postgres vacuum",
    "source_uri": "note:vacuum",
    "mime_type": "text/markdown",
    "tags": ["postgres"],
    "status": "ready",
    "error": None,
    "chunk_count": 2,
    "created_at": "2026-10-01T12:00:00Z",
    "updated_at": "2026-10-01T12:00:00Z",
    "body": "# Vacuum\n\nTune autovacuum.",
    "metadata": {},
}


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def api() -> Iterator[respx.MockRouter]:
    with respx.mock(base_url=BASE, assert_all_called=False) as router:
        yield router


def run(runner: CliRunner, *args: str, input: str | None = None):
    return runner.invoke(app, list(args), env=ENV, input=input)


def test_search_text_and_json(runner: CliRunner, api: respx.MockRouter) -> None:
    route = api.post("/api/search").respond(
        json={
            "query": "vacuum",
            "mode": "hybrid",
            "hits": [
                {
                    "chunk_id": 7,
                    "document_id": DOC_ID,
                    "document_title": "Postgres vacuum",
                    "collection": "default",
                    "source_uri": None,
                    "heading_path": "Tuning",
                    "content": "c",
                    "snippet": "Tune <mark>autovacuum</mark>",
                    "score": 0.91234,
                    "scores": {},
                    "ord": 0,
                    "tags": [],
                }
            ],
            "timings": [],
            "degraded": [],
            "total_ms": 4.2,
        }
    )
    res = run(runner, "search", "vacuum", "-n", "3", "-c", "ops", "-t", "pg", "--no-rerank")
    assert res.exit_code == 0, res.output
    assert "[1] Postgres vacuum › Tuning (score 0.912)" in res.stdout
    sent = json.loads(route.calls.last.request.content)
    assert sent["limit"] == 3 and sent["rerank"] is False
    assert sent["filters"] == {"collections": ["ops"], "tags": ["pg"]}
    assert route.calls.last.request.headers["authorization"] == "Bearer tok"

    res = run(runner, "search", "vacuum", "--json")
    out = json.loads(res.stdout)
    assert out["hits"][0] == {
        "chunk_id": 7,
        "document_id": DOC_ID,
        "title": "Postgres vacuum",
        "heading_path": "Tuning",
        "snippet": "Tune autovacuum",
        "score": 0.9123,
        "collection": "default",
        "source_uri": None,
    }


def test_unreachable_exit_4(runner: CliRunner, api: respx.MockRouter) -> None:
    api.get("/api/documents").mock(side_effect=httpx.ConnectError("refused"))
    res = run(runner, "ls", "--json")
    assert res.exit_code == 4
    assert res.stdout == ""
    assert json.loads(res.stderr)["error"]["code"] == "unreachable"


def test_not_found_exit_3(runner: CliRunner, api: respx.MockRouter) -> None:
    api.get(f"/api/documents/{DOC_ID}").respond(404, json={"detail": "document not found"})
    res = run(runner, "get", DOC_ID)
    assert res.exit_code == 3
    assert "document not found" in res.stderr and res.stdout == ""


def test_validation_error_exit_2(runner: CliRunner, api: respx.MockRouter) -> None:
    api.post("/api/collections").respond(
        422, json={"detail": [{"loc": ["body", "slug"], "msg": "bad slug"}]}
    )
    res = run(runner, "collections", "create", "Bad Slug", "Name")
    assert res.exit_code == 2
    assert "slug: bad slug" in res.stderr


def test_server_error_exit_1(runner: CliRunner, api: respx.MockRouter) -> None:
    api.get("/api/collections").respond(500, text="boom")
    res = run(runner, "collections")
    assert res.exit_code == 1


def test_usage_errors(runner: CliRunner) -> None:
    assert run(runner, "rm", DOC_ID).exit_code == 2  # no --yes
    assert run(runner, "search").exit_code == 2  # missing argument
    assert run(runner, "update", DOC_ID).exit_code == 2  # nothing to update
    assert run(runner, "search", "x", "--mode", "fuzzy").exit_code == 2


def test_get_and_ls(runner: CliRunner, api: respx.MockRouter) -> None:
    api.get(f"/api/documents/{DOC_ID}").respond(json=DOC)
    res = run(runner, "get", DOC_ID)
    assert res.exit_code == 0
    assert res.stdout.startswith("# Postgres vacuum\n(doc=" + DOC_ID)
    assert "Tune autovacuum." in res.stdout
    assert json.loads(run(runner, "get", DOC_ID, "--json").stdout)["body"] == DOC["body"]

    summary = {k: v for k, v in DOC.items() if k not in ("body", "metadata")}
    route = api.get("/api/documents").respond(
        json={"items": [summary], "total": 3, "limit": 1, "offset": 0}
    )
    res = run(runner, "ls", "-t", "postgres", "-n", "1", "--json")
    out = json.loads(res.stdout)
    assert out["next_offset"] == 1 and out["total"] == 3
    assert out["items"][0]["id"] == DOC_ID
    assert "--offset 1" in out["hint"]
    params = route.calls.last.request.url.params
    assert params["tag"] == "postgres" and params["limit"] == "1" and "collection" not in params


def test_add_stdin_and_wait(runner: CliRunner, api: respx.MockRouter) -> None:
    create = api.post("/api/documents").respond(
        202, json={"id": DOC_ID, "status": "pending", "duplicate": False}
    )
    api.get(f"/api/documents/{DOC_ID}").mock(
        side_effect=[
            httpx.Response(200, json={**DOC, "status": "processing"}),
            httpx.Response(200, json=DOC),
        ]
    )
    res = run(
        runner,
        "add",
        "-",
        "--title",
        "T",
        "-t",
        "a",
        "-t",
        "b",
        "--source-uri",
        "note:x",
        "--wait",
        "--json",
        input="# Hello\n\nworld",
    )
    assert res.exit_code == 0, res.stderr
    assert json.loads(res.stdout) == {
        "id": DOC_ID,
        "status": "ready",
        "duplicate": False,
        "hint": None,
    }
    sent = json.loads(create.calls.last.request.content)
    assert sent["content"] == "# Hello\n\nworld" and sent["tags"] == ["a", "b"]
    assert sent["source_uri"] == "note:x" and sent["title"] == "T"


def test_add_file_multipart(runner: CliRunner, api: respx.MockRouter, tmp_path: Path) -> None:
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.7 fake")
    upload = api.post("/api/documents/upload").respond(
        202, json=[{"id": DOC_ID, "status": "pending", "duplicate": True}]
    )
    res = run(runner, "add", str(pdf), "-c", "papers", "-t", "ml")
    assert res.exit_code == 0, res.stderr
    assert res.stdout.strip() == f"duplicate of {DOC_ID} (pending)"
    body = upload.calls.last.request.content
    assert b'filename="paper.pdf"' in body and b"papers" in body

    res = run(runner, "add", str(pdf), "--source-uri", "x")
    assert res.exit_code == 2
    assert run(runner, "add", str(tmp_path / "missing.md")).exit_code == 2


def test_update_and_rm(runner: CliRunner, api: respx.MockRouter, tmp_path: Path) -> None:
    body_file = tmp_path / "new.md"
    body_file.write_text("# New body")
    patch = api.patch(f"/api/documents/{DOC_ID}").respond(
        json={**DOC, "title": "New", "status": "pending"}
    )
    res = run(
        runner, "update", DOC_ID, "--title", "New", "--content-file", str(body_file), "--json"
    )
    assert json.loads(res.stdout)["title"] == "New"
    assert json.loads(patch.calls.last.request.content) == {
        "title": "New",
        "content": "# New body",
    }
    api.delete(f"/api/documents/{DOC_ID}").respond(204)
    res = run(runner, "rm", DOC_ID, "--yes", "--json")
    assert json.loads(res.stdout) == {"id": DOC_ID, "deleted": True}


def test_ask_stream(runner: CliRunner, api: respx.MockRouter) -> None:
    final = {
        "question": "q",
        "answer": "Tune it [1].",
        "citations": [
            {
                "n": 1,
                "chunk_id": 7,
                "document_id": DOC_ID,
                "document_title": "Postgres vacuum",
                "heading_path": "",
                "snippet": "x",
            }
        ],
        "steps": [],
        "engine": "cloud",
        "total_ms": 5,
    }
    sse = (
        'event: step\ndata: {"type": "search", "query": "vacuum", "hits": []}\n\n'
        'event: step\ndata: {"type": "answer_delta", "message": "Tune "}\n\n'
        'event: step\ndata: {"type": "answer_delta", "message": "it [1]."}\n\n'
        f"event: done\ndata: {json.dumps(final)}\n\n"
    )
    route = api.post("/api/ask/stream").respond(
        text=sse, headers={"content-type": "text/event-stream"}
    )
    res = run(runner, "ask", "q", "--cloud", "--stream")
    assert res.exit_code == 0, res.stderr
    assert res.stdout.startswith("Tune it [1].\n\nSources:\n[1] Postgres vacuum")
    assert "· search: vacuum (0 hits)" in res.stderr
    assert json.loads(route.calls.last.request.content)["engine"] == "cloud"

    res = run(runner, "ask", "q", "--stream", "--json")
    assert json.loads(res.stdout)["sources"][0]["n"] == 1

    api.post("/api/ask").respond(json=final)
    res = run(runner, "ask", "q")
    assert res.stdout.startswith("Tune it [1].")


def test_ask_stream_error_event(runner: CliRunner, api: respx.MockRouter) -> None:
    api.post("/api/ask/stream").respond(
        text='event: error\ndata: {"message": "llm down"}\n\n',
        headers={"content-type": "text/event-stream"},
    )
    res = run(runner, "ask", "q", "--stream")
    assert res.exit_code == 1 and "llm down" in res.stderr


def test_status_and_collections(runner: CliRunner, api: respx.MockRouter) -> None:
    api.get("/api/health").respond(
        json={
            "status": "degraded",
            "version": "0.1.0",
            "components": [
                {"name": "db", "ok": True, "latency_ms": 1.2},
                {"name": "reranker", "ok": False, "detail": "connection refused"},
            ],
        }
    )
    api.get("/api/stats").respond(
        json={
            "documents": 3,
            "chunks": 9,
            "collections": 1,
            "queued_jobs": 0,
            "failed_jobs": 0,
            "queries_24h": 4,
        }
    )
    res = run(runner, "status")
    assert res.exit_code == 0
    assert "degraded" in res.stdout and "ERR reranker" in res.stdout
    assert "documents=3" in res.stdout

    api.get("/api/collections").respond(
        json=[
            {
                "id": DOC_ID,
                "slug": "default",
                "name": "Default",
                "description": "",
                "created_at": "2026-10-01T12:00:00Z",
                "document_count": 3,
            }
        ]
    )
    res = run(runner, "collections", "--json")
    assert json.loads(res.stdout) == {
        "collections": [
            {"slug": "default", "name": "Default", "description": "", "document_count": 3}
        ]
    }


def test_auth_error_message(runner: CliRunner, api: respx.MockRouter) -> None:
    api.delete(f"/api/documents/{DOC_ID}").respond(401, json={"detail": "invalid token"})
    res = run(runner, "rm", DOC_ID, "--yes")
    assert res.exit_code == 1 and "KB_TOKEN" in res.stderr


def test_mcp_config(runner: CliRunner) -> None:
    res = run(runner, "mcp-config", "--json")
    out = json.loads(res.stdout)
    assert out["claude"]["mcp.json"]["mcpServers"]["kb"]["url"] == f"{BASE}/mcp"
    assert 'bearer_token_env_var = "KB_TOKEN"' in out["codex"]["toml"]
    res = run(runner, "mcp-config", "--client", "claude")
    assert "claude mcp add --transport http kb http://kb.test/mcp" in res.stdout
