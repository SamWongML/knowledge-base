"""``kb``: agent-friendly command-line client for the knowledge-base HTTP API.

A thin HTTP client (``KB_URL``, ``KB_TOKEN``): it never touches the database, so it
starts fast and works from any machine that can reach the server.

Conventions
- Concise text by default; ``--json`` (or ``KB_OUTPUT=json``) prints one stable JSON
  document on stdout. Diagnostics and errors go to stderr.
- Never prompts. Destructive commands need ``--yes``.
- Exit codes: 0 ok, 1 error, 2 usage/invalid input, 3 not found, 4 server unreachable.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, NoReturn

import typer

from kb.mcp import shapes

if TYPE_CHECKING:
    import httpx

EXIT_OK, EXIT_ERROR, EXIT_USAGE, EXIT_NOT_FOUND, EXIT_UNREACHABLE = 0, 1, 2, 3, 4
DEFAULT_URL = "http://127.0.0.1:8787"
TEXT_SUFFIXES = {".md", ".markdown", ".txt", ".rst", ".org", ".text"}

app = typer.Typer(
    name="kb",
    help="Search, read and write the knowledge base. Exit codes: 0 ok, 1 error, 2 usage, "
    "3 not found, 4 server unreachable. Env: KB_URL, KB_TOKEN, KB_OUTPUT=json.",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
    rich_markup_mode=None,
    context_settings={"help_option_names": ["-h", "--help"]},
)
collections_app = typer.Typer(
    help="List collections, or create one with `kb collections create SLUG NAME`.",
    invoke_without_command=True,
    rich_markup_mode=None,
)
app.add_typer(collections_app, name="collections")


class _State:
    json: bool = False


STATE = _State()

JsonOpt = Annotated[bool, typer.Option("--json", help="Print a stable JSON shape on stdout.")]


# --- plumbing --------------------------------------------------------------------------


def _use_json(flag: bool) -> bool:
    STATE.json = flag or os.environ.get("KB_OUTPUT", "").lower() == "json"
    return STATE.json


def _base_url() -> str:
    return os.environ.get("KB_URL", DEFAULT_URL).rstrip("/")


def _token() -> str | None:
    token = os.environ.get("KB_TOKEN") or os.environ.get("KB_API_TOKEN")
    if not token and (path := os.environ.get("KB_TOKEN_FILE")):
        try:
            token = Path(path).expanduser().read_text().strip()
        except OSError as exc:
            fail("config", f"cannot read KB_TOKEN_FILE: {exc}", code=EXIT_ERROR)
    return token or None


def fail(kind: str, message: str, *, hint: str | None = None, code: int = EXIT_ERROR) -> NoReturn:
    """Report an error on stderr (JSON in --json mode) and exit with ``code``."""
    if STATE.json:
        err: dict[str, Any] = {"code": kind, "message": message}
        if hint:
            err["hint"] = hint
        sys.stderr.write(json.dumps({"error": err}) + "\n")
    else:
        sys.stderr.write(f"error: {message}\n" + (f"hint: {hint}\n" if hint else ""))
    raise typer.Exit(code)


def emit(data: Any, text: str) -> None:
    """Print ``data`` as JSON in --json mode, otherwise the concise ``text``."""
    if STATE.json:
        sys.stdout.write(json.dumps(data, ensure_ascii=False, default=str) + "\n")
    else:
        sys.stdout.write(text.rstrip("\n") + "\n")


def _client(timeout: float = 60.0) -> httpx.Client:
    import httpx

    headers = {"Accept": "application/json", "User-Agent": "kb-cli"}
    if token := _token():
        headers["Authorization"] = f"Bearer {token}"
    return httpx.Client(
        base_url=_base_url(), headers=headers, timeout=httpx.Timeout(timeout, connect=5.0)
    )


def _detail(resp: httpx.Response) -> str:
    try:
        body = resp.json()
    except ValueError:
        return resp.text[:300] or resp.reason_phrase
    detail = body.get("detail", body) if isinstance(body, dict) else body
    if isinstance(detail, list):  # FastAPI validation errors
        return "; ".join(
            f"{'.'.join(str(p) for p in d.get('loc', [])[1:])}: {d.get('msg')}"
            if isinstance(d, dict)
            else str(d)
            for d in detail
        )
    return str(detail)


def _check(resp: httpx.Response, what: str = "") -> httpx.Response:
    if resp.is_success:
        return resp
    status = resp.status_code
    msg = f"{what + ': ' if what else ''}{_detail(resp)} (HTTP {status})"
    if status == 404:
        fail("not_found", msg, hint="Find ids with `kb search` or `kb ls`.", code=EXIT_NOT_FOUND)
    if status in (401, 403):
        fail("auth", msg, hint="Set KB_TOKEN to the server's KB_API_TOKEN.")
    if status in (400, 409, 413, 422):
        fail("invalid", msg, code=EXIT_USAGE)
    fail("server", msg)


def request(method: str, path: str, *, timeout: float = 60.0, what: str = "", **kw: Any) -> Any:
    """Send a request and return the decoded JSON body (``None`` for 204)."""
    import httpx

    try:
        with _client(timeout) as client:
            resp = _check(client.request(method, path, **kw), what)
    except httpx.TransportError as exc:
        _transport_fail(exc)
    if resp.status_code == 204 or not resp.content:
        return None
    return resp.json()


def _transport_fail(exc: Exception) -> NoReturn:
    import httpx

    if isinstance(exc, httpx.ConnectError | httpx.ConnectTimeout):
        fail(
            "unreachable",
            f"cannot reach the knowledge base at {_base_url()} ({exc})",
            hint="Is the server running? Check KB_URL. Do not retry in a loop.",
            code=EXIT_UNREACHABLE,
        )
    if isinstance(exc, httpx.TimeoutException):
        fail("timeout", f"request timed out ({exc})", hint="Narrow the request or retry later.")
    fail("transport", str(exc))


# --- search / ask ----------------------------------------------------------------------


@app.command()
def search(
    query: Annotated[str, typer.Argument(help="Question or keywords.")],
    limit: Annotated[int, typer.Option("-n", "--limit", min=1, max=100)] = 10,
    collection: Annotated[
        list[str] | None, typer.Option("-c", "--collection", help="Collection slug (repeat).")
    ] = None,
    tag: Annotated[list[str] | None, typer.Option("-t", "--tag", help="Tag, match any.")] = None,
    mode: Annotated[str, typer.Option(help="hybrid | lexical | semantic")] = "hybrid",
    no_rerank: Annotated[bool, typer.Option("--no-rerank", help="Skip reranking.")] = False,
    as_json: JsonOpt = False,
) -> None:
    """Hybrid search; prints ranked passages with document ids.

    Example: kb search "postgres vacuum tuning" -n 5 --json | jq -r '.hits[].document_id'
    """
    _use_json(as_json)
    if mode not in ("hybrid", "lexical", "semantic"):
        fail("invalid", f"unknown mode {mode!r}", hint="Use hybrid, lexical or semantic.", code=2)
    body = {
        "query": query,
        "mode": mode,
        "limit": limit,
        "rerank": not no_rerank,
        "filters": {"collections": collection or None, "tags": tag or None},
    }
    out = shapes.search_result(request("POST", "/api/search", json=body, what="search"))
    emit(out, shapes.render_search(out))


@app.command()
def ask(
    question: Annotated[str, typer.Argument(help="Question to research.")],
    cloud: Annotated[bool, typer.Option("--cloud", help="Use the cloud engine (Claude).")] = False,
    stream: Annotated[
        bool, typer.Option("--stream", help="Stream the answer; agent trace on stderr.")
    ] = False,
    as_json: JsonOpt = False,
) -> None:
    """Agentic answer with [n] citations (slower than search)."""
    _use_json(as_json)
    body = {"question": question, "engine": "cloud" if cloud else "local"}
    if not stream:
        out = shapes.ask_result(request("POST", "/api/ask", json=body, timeout=300, what="ask"))
        emit(out, shapes.render_ask(out))
        return
    _ask_stream(body)


def _ask_stream(body: dict[str, Any]) -> None:
    import httpx

    final: dict[str, Any] | None = None
    streamed = False
    try:
        with (
            _client(300) as client,
            client.stream(
                "POST", "/api/ask/stream", json=body, headers={"Accept": "text/event-stream"}
            ) as resp,
        ):
            if not resp.is_success:
                resp.read()
                _check(resp, "ask")
            for event, data in _sse(resp.iter_lines()):
                if event == "step":
                    step = json.loads(data)
                    if step.get("type") == "answer_delta":
                        if not STATE.json:
                            sys.stdout.write(step.get("message", ""))
                            sys.stdout.flush()
                            streamed = True
                    else:
                        _trace(step)
                elif event == "done":
                    final = json.loads(data)
                elif event == "error":
                    try:
                        message = json.loads(data).get("message", data)
                    except ValueError:
                        message = data
                    fail("agent", str(message))
    except httpx.TransportError as exc:
        _transport_fail(exc)
    if final is None:
        fail("agent", "stream ended without an answer")
    out = shapes.ask_result(final)
    if STATE.json:
        emit(out, "")
    elif streamed:
        src = shapes.render_sources(out["sources"])
        sys.stdout.write("\n\n" + src + "\n" if src else "\n")
    else:
        emit(out, shapes.render_ask(out))


def _sse(lines: Any) -> Any:
    """Yield ``(event, data)`` pairs from an iterator of SSE lines."""
    event, data = "message", []
    for line in lines:
        if not line:
            if data:
                yield event, "\n".join(data)
            event, data = "message", []
        elif line.startswith(":"):
            continue
        elif line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].removeprefix(" "))
    if data:
        yield event, "\n".join(data)


def _trace(step: dict[str, Any]) -> None:
    kind = step.get("type", "?")
    detail = step.get("query") or step.get("message") or ""
    hits = step.get("hits")
    suffix = f" ({len(hits)} hits)" if isinstance(hits, list) else ""
    sys.stderr.write(f"· {kind}: {detail}{suffix}\n")
    sys.stderr.flush()


# --- documents -------------------------------------------------------------------------


@app.command()
def add(
    path: Annotated[str, typer.Argument(help="File to upload, or '-' for stdin text.")] = "-",
    title: Annotated[str | None, typer.Option("--title")] = None,
    collection: Annotated[str, typer.Option("-c", "--collection")] = "default",
    tag: Annotated[list[str] | None, typer.Option("-t", "--tag", help="Tag (repeat).")] = None,
    source_uri: Annotated[
        str | None,
        typer.Option("--source-uri", help="Stable origin; re-adding the same URI updates it."),
    ] = None,
    wait: Annotated[bool, typer.Option("--wait", help="Wait until indexed.")] = False,
    timeout: Annotated[float, typer.Option(help="Seconds to wait with --wait.")] = 120.0,
    as_json: JsonOpt = False,
) -> None:
    """Add a document from a file or stdin (Markdown preferred).

    Examples: kb add notes.md -t postgres  |  echo "# Fix ..." | kb add - --source-uri note:fix
    """
    _use_json(as_json)
    tags = tag or []
    if path == "-":
        if sys.stdin.isatty():
            fail("invalid", "no input on stdin", hint="Pipe text in or pass a file path.", code=2)
        content = sys.stdin.read()
        result = _add_text(content, title, collection, tags, source_uri, "text/markdown")
    else:
        file = Path(path).expanduser()
        if not file.is_file():
            fail("invalid", f"no such file: {path}", code=EXIT_USAGE)
        result = _add_file(file, title, collection, tags, source_uri)
    status = result["status"]
    if wait and status not in ("ready", "failed"):
        status = _wait_ready(str(result["id"]), timeout)
    out = {
        "id": str(result["id"]),
        "status": status,
        "duplicate": bool(result.get("duplicate", False)),
        "hint": "Identical content already exists; returned its id."
        if result.get("duplicate")
        else None,
    }
    verb = "duplicate of" if out["duplicate"] else "added"
    emit(out, f"{verb} {out['id']} ({status})")
    if status == "failed":
        raise typer.Exit(EXIT_ERROR)


def _add_text(
    content: str,
    title: str | None,
    collection: str,
    tags: list[str],
    source_uri: str | None,
    mime_type: str,
) -> dict[str, Any]:
    if not content.strip():
        fail("invalid", "content is empty", code=EXIT_USAGE)
    body = {
        "content": content,
        "title": title,
        "collection": collection,
        "tags": tags,
        "source_uri": source_uri,
        "mime_type": mime_type,
    }
    return request("POST", "/api/documents", json=body, what="add")


def _add_file(
    file: Path, title: str | None, collection: str, tags: list[str], source_uri: str | None
) -> dict[str, Any]:
    import mimetypes

    mime = mimetypes.guess_type(file.name)[0]
    if source_uri is not None or title is not None:
        # Title/source_uri travel in the JSON body, so send text files that way.
        if file.suffix.lower() in TEXT_SUFFIXES:
            mime_type = "text/plain" if file.suffix.lower() == ".txt" else "text/markdown"
            text = file.read_text(encoding="utf-8", errors="replace")
            return _add_text(text, title, collection, tags, source_uri, mime_type)
        if source_uri is not None:
            fail(
                "invalid",
                "--source-uri is only supported for text files and stdin",
                code=EXIT_USAGE,
            )
    files = [("files", (file.name, file.read_bytes(), mime or "application/octet-stream"))]
    data = {"collection": collection, "tags": ",".join(tags)}
    accepted = request(
        "POST", "/api/documents/upload", files=files, data=data, timeout=300, what="upload"
    )
    result = accepted[0] if isinstance(accepted, list) else accepted
    if title is not None:
        request("PATCH", f"/api/documents/{result['id']}", json={"title": title}, what="title")
    return result


def _wait_ready(doc_id: str, timeout: float) -> str:
    deadline = time.monotonic() + timeout
    delay = 0.25
    while True:
        doc = request("GET", f"/api/documents/{doc_id}", what="status")
        status = doc["status"]
        if status in ("ready", "failed"):
            if status == "failed" and not STATE.json:
                sys.stderr.write(f"ingestion failed: {doc.get('error')}\n")
            return status
        if time.monotonic() >= deadline:
            fail("timeout", f"document {doc_id} still {status} after {timeout:.0f}s")
        time.sleep(delay)
        delay = min(delay * 1.5, 2.0)


@app.command()
def get(
    doc_id: Annotated[str, typer.Argument(metavar="ID", help="Document id.")],
    as_json: JsonOpt = False,
) -> None:
    """Print a document (metadata header + Markdown body)."""
    _use_json(as_json)
    doc = request("GET", f"/api/documents/{doc_id}", what="get")
    emit(doc, shapes.doc_header(doc) + "\n\n" + doc.get("body", ""))


@app.command("ls")
def list_docs(
    collection: Annotated[str | None, typer.Option("-c", "--collection")] = None,
    tag: Annotated[str | None, typer.Option("-t", "--tag")] = None,
    query: Annotated[str | None, typer.Option("-q", "--query", help="Title filter.")] = None,
    limit: Annotated[int, typer.Option("-n", "--limit", min=1, max=200)] = 20,
    offset: Annotated[int, typer.Option("--offset", min=0)] = 0,
    as_json: JsonOpt = False,
) -> None:
    """List documents, newest first."""
    _use_json(as_json)
    params = {"collection": collection, "tag": tag, "q": query, "limit": limit, "offset": offset}
    page = request(
        "GET",
        "/api/documents",
        params={k: v for k, v in params.items() if v is not None},
        what="list",
    )
    out = shapes.list_result(page)
    if out["hint"]:
        out["hint"] = f"More documents: rerun with --offset {out['next_offset']}."
    emit(out, shapes.render_list(out))


@app.command()
def rm(
    doc_id: Annotated[str, typer.Argument(metavar="ID")],
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Confirm deletion.")] = False,
    as_json: JsonOpt = False,
) -> None:
    """Delete a document permanently (requires --yes)."""
    _use_json(as_json)
    if not yes:
        fail("invalid", "refusing to delete without --yes", code=EXIT_USAGE)
    request("DELETE", f"/api/documents/{doc_id}", what="delete")
    emit({"id": doc_id, "deleted": True}, f"deleted {doc_id}")


@app.command()
def update(
    doc_id: Annotated[str, typer.Argument(metavar="ID")],
    title: Annotated[str | None, typer.Option("--title")] = None,
    tag: Annotated[
        list[str] | None, typer.Option("-t", "--tag", help="Replacement tags (repeat).")
    ] = None,
    content_file: Annotated[
        str | None, typer.Option("--content-file", help="New body from a file or '-' (stdin).")
    ] = None,
    as_json: JsonOpt = False,
) -> None:
    """Update title, tags or content (content replaces the body and re-indexes)."""
    _use_json(as_json)
    patch: dict[str, Any] = {}
    if title is not None:
        patch["title"] = title
    if tag is not None:
        patch["tags"] = tag
    if content_file is not None:
        if content_file == "-":
            patch["content"] = sys.stdin.read()
        else:
            file = Path(content_file).expanduser()
            if not file.is_file():
                fail("invalid", f"no such file: {content_file}", code=EXIT_USAGE)
            patch["content"] = file.read_text(encoding="utf-8")
    if not patch:
        fail("invalid", "nothing to update", hint="Pass --title, --tag or --content-file.", code=2)
    doc = request("PATCH", f"/api/documents/{doc_id}", json=patch, what="update")
    out = {
        "id": str(doc["id"]),
        "title": doc["title"],
        "status": doc["status"],
        "tags": doc.get("tags", []),
        "hint": None,
    }
    emit(out, f"updated {out['id']} ({out['status']}): {out['title']}")


# --- collections / system --------------------------------------------------------------


@collections_app.callback()
def collections_list(ctx: typer.Context, as_json: JsonOpt = False) -> None:
    """List collections."""
    _use_json(as_json)
    if ctx.invoked_subcommand is not None:
        return
    rows = [shapes.collection_row(c) for c in request("GET", "/api/collections", what="list")]
    emit({"collections": rows}, shapes.render_collections(rows))


@collections_app.command("create")
def collections_create(
    slug: Annotated[str, typer.Argument(help="lowercase-slug")],
    name: Annotated[str, typer.Argument(help="Display name")],
    description: Annotated[str, typer.Option("--description", "-d")] = "",
    as_json: JsonOpt = False,
) -> None:
    """Create a collection."""
    _use_json(as_json or STATE.json)
    body = {"slug": slug, "name": name, "description": description}
    row = shapes.collection_row(request("POST", "/api/collections", json=body, what="create"))
    emit(row, f"created collection {row['slug']}")


@app.command()
def status(as_json: JsonOpt = False) -> None:
    """Server health and corpus statistics (exit 1 when the server reports 'down')."""
    _use_json(as_json)
    health = request("GET", "/api/health", what="health")
    stats = request("GET", "/api/stats", what="stats")
    lines = [f"{health['status']} (v{health.get('version', '?')}) at {_base_url()}"]
    for c in health.get("components", []):
        lat = f" {c['latency_ms']:.0f}ms" if c.get("latency_ms") is not None else ""
        det = f" - {c['detail']}" if c.get("detail") else ""
        lines.append(f"  {'ok ' if c['ok'] else 'ERR'} {c['name']}{lat}{det}")
    lines.append(
        "  " + " ".join(f"{k}={v}" for k, v in stats.items() if isinstance(v, int | float))
    )
    emit({"health": health, "stats": stats}, "\n".join(lines))
    if health.get("status") == "down":
        raise typer.Exit(EXIT_ERROR)


@app.command("mcp-config")
def mcp_config(
    client: Annotated[str, typer.Option(help="claude | codex | cursor | all")] = "all",
    as_json: JsonOpt = False,
) -> None:
    """Print snippets that register this server's MCP endpoint with coding agents."""
    _use_json(as_json)
    if client not in ("claude", "codex", "cursor", "all"):
        fail("invalid", f"unknown client {client!r}", code=EXIT_USAGE)
    url = f"{_base_url()}/mcp"
    auth = {"Authorization": "Bearer ${KB_TOKEN}"}
    configs = {
        "claude": {
            "command": f'claude mcp add --transport http kb {url} --header "Authorization: Bearer $KB_TOKEN"',
            "mcp.json": {"mcpServers": {"kb": {"type": "http", "url": url, "headers": auth}}},
        },
        "codex": {
            "file": "~/.codex/config.toml",
            "toml": f'[mcp_servers.kb]\nurl = "{url}"\nbearer_token_env_var = "KB_TOKEN"\n',
        },
        "cursor": {
            "file": "~/.cursor/mcp.json",
            "mcp.json": {"mcpServers": {"kb": {"url": url, "headers": auth}}},
        },
    }
    selected = configs if client == "all" else {client: configs[client]}
    parts = []
    if "claude" in selected:
        c = selected["claude"]
        parts.append(
            "# Claude Code (run once, or put the JSON in .mcp.json)\n"
            + c["command"]
            + "\n"
            + json.dumps(c["mcp.json"], indent=2)
        )
    if "codex" in selected:
        parts.append(f"# Codex CLI ({selected['codex']['file']})\n{selected['codex']['toml']}")
    if "cursor" in selected:
        c = selected["cursor"]
        parts.append(f"# Cursor ({c['file']})\n" + json.dumps(c["mcp.json"], indent=2))
    emit(selected, "\n\n".join(parts))


def main() -> None:  # pragma: no cover - console entry alternative
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
