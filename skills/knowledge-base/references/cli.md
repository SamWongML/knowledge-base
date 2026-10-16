# `kb` CLI reference

`kb` is a thin HTTP client for the knowledge-base API. Configure it with environment variables:

| Variable | Meaning |
|---|---|
| `KB_URL` | Server base URL (default `http://127.0.0.1:8787`) |
| `KB_TOKEN` | Bearer token, which must equal the server's `KB_API_TOKEN`. `KB_TOKEN_FILE` is the alternative |
| `KB_OUTPUT=json` | Same as passing `--json` to every command |

Output rules:
- Data goes to stdout and errors to stderr.
- With `--json`, stdout carries exactly one JSON document.
- In JSON mode, errors on stderr are `{"error": {"code": "...", "message": "...", "hint": "..."}}`.
- No command ever prompts.

Exit codes:

| Code | Meaning |
|---|---|
| 0 | ok |
| 1 | error (server error, auth, ingestion failed, timeout) |
| 2 | usage or invalid input (including HTTP 400/409/413/422) |
| 3 | not found |
| 4 | server unreachable |

## Commands

### `kb search QUERY [-n N] [-c SLUG]... [-t TAG]... [--mode hybrid|lexical|semantic] [--no-rerank] [--json]`
Hybrid search (BM25 + vectors + reranker). `-c` and `-t` can be repeated; tags match any.
```json
{"query": "vacuum", "degraded": [], "hint": null,
 "hits": [{"chunk_id": 7, "document_id": "uuid", "title": "Postgres vacuum",
           "heading_path": "Tuning > Autovacuum", "snippet": "plain text…",
           "score": 0.9123, "collection": "notes", "source_uri": "note:pg-vacuum"}]}
```
`degraded` lists the pipeline stages that were skipped because a model server was down.

### `kb ask QUESTION [--cloud] [--stream] [--json]`
Runs the agentic research loop and answers with `[n]` citations.
- `--stream` prints the answer as it is generated, with the agent trace on stderr.
- `--cloud` uses Claude, if the server has it configured.
```json
{"answer": "Tune autovacuum_vacuum_scale_factor … [1].", "engine": "local", "hint": null,
 "sources": [{"n": 1, "document_id": "uuid", "chunk_id": 7, "title": "Postgres vacuum",
              "heading_path": "Tuning", "snippet": "…"}]}
```

### `kb add [PATH|-] [--title T] [-c SLUG] [-t TAG]... [--source-uri URI] [--wait] [--timeout S] [--json]`
Adds a document from stdin (`-`, the default) or from a file.
- Text from stdin and `.md`/`.txt` files that come with `--title` or `--source-uri` are sent as JSON.
- Other files are uploaded and parsed on the server (PDF, Office, HTML, …).
- Using the same `--source-uri` again updates that document. Content that already exists unchanged returns `duplicate: true`.
- `--wait` polls until the document is `ready` or `failed`. A `failed` status exits with 1.
```json
{"id": "uuid", "status": "pending|processing|ready|failed", "duplicate": false, "hint": null}
```

### `kb get ID [--json]`
Text mode prints a metadata header and then the Markdown body. `--json` prints the full API `Document`:
```json
{"id": "uuid", "collection_id": "uuid", "collection": "notes", "title": "…", "source_uri": "…",
 "mime_type": "text/markdown", "tags": ["…"], "status": "ready", "error": null, "chunk_count": 4,
 "created_at": "…", "updated_at": "…", "body": "# Markdown…", "metadata": {}}
```

### `kb ls [-c SLUG] [-t TAG] [-q TITLE] [-n N] [--offset K] [--json]`
Lists documents, newest first.
```json
{"items": [{"id": "uuid", "title": "…", "collection": "notes", "tags": [], "status": "ready",
            "source_uri": null, "chunk_count": 4, "updated_at": "2026-10-01T12:00:00"}],
 "total": 42, "offset": 0, "next_offset": 20, "hint": "More documents: rerun with --offset 20."}
```

### `kb update ID [--title T] [-t TAG]... [--content-file PATH|-] [--json]`
- `-t` replaces the whole tag list.
- `--content-file` replaces the body and re-indexes the document.
```json
{"id": "uuid", "title": "…", "status": "pending", "tags": ["…"], "hint": null}
```

### `kb rm ID --yes [--json]`
Deletes a document permanently. Without `--yes` the command exits with 2. Output: `{"id": "uuid", "deleted": true}`.

### `kb collections [--json]` / `kb collections create SLUG NAME [-d DESC] [--json]`
```json
{"collections": [{"slug": "notes", "name": "Notes", "description": "", "document_count": 12}]}
```
`create` prints a single collection object. A slug must match `^[a-z0-9][a-z0-9-]{0,62}$`.

### `kb status [--json]`
Shows server health and corpus statistics. It exits with 1 when the server reports `down`.
```json
{"health": {"status": "ok|degraded|down", "version": "0.1.0",
            "components": [{"name": "db", "ok": true, "latency_ms": 1.2, "detail": null}]},
 "stats": {"documents": 3, "chunks": 9, "collections": 1, "queued_jobs": 0,
           "failed_jobs": 0, "queries_24h": 4}}
```

### `kb mcp-config [--client claude|codex|cursor|all] [--json]`
Prints configuration snippets that register the server's MCP endpoint (`$KB_URL/mcp`) with coding agents.

## MCP equivalents
The server exposes the same operations as MCP tools at `/mcp`: `kb_search`, `kb_ask`, `kb_read`, `kb_list`, `kb_add`, `kb_update`, `kb_delete` and `kb_collections`. Documents are also available as resources at `kb://documents/{id}`.

The MCP tools return the same JSON shapes as structured content, with these differences:
- `kb_read` returns `{document_id, title, collection, tags, source_uri, status, content, total_chunks, start_chunk, end_chunk, truncated, next_start_chunk, hint}`. Long documents come back in chunk windows; to page through, pass `start_chunk=next_start_chunk`.
- `kb_delete` returns `{id, deleted}`.
