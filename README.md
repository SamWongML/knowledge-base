# kb — a self-hosted, state-of-the-art knowledge base

A private knowledge base for one Apple Silicon Mac mini. It ingests notes and documents, finds them with a
hybrid retrieval cascade, answers questions with a small local model that searches step by step, and is
usable from coding agents through a CLI + Agent Skill or an MCP server. It is built for many concurrent
insertions and queries on a single box.

```
            ┌──────────── web app (Vite 8 · React 19 · TanStack · Tailwind 4) ───────────┐
 agents ──► │  kb CLI + Agent Skill   ·   MCP (Streamable HTTP, /mcp)   ·   REST + SSE    │
            └────────────────────────────── FastAPI (kb-api ×N) ─────────────────────────┘
                     │ search                     │ ask                      │ ingest
     BM25 (pg_search) ‖ dense (pgvector HNSW)   router → multi-query →     parse → chunk →
       → RRF → Qwen3-Reranker → Haiku judge     sufficiency loop (≤4) →    context → embed
                     │                          cited streaming answer     (kb-worker ×N)
            ┌──────── PostgreSQL 18: documents · chunks · vectors · BM25 · job queue ────────┐
            └──────── llama.cpp (embed, rerank) + Ollama (Qwen3.5) native on Metal ──────────┘
```

## Highlights

- **Hybrid retrieval cascade.** BM25 with English stemming and a dense HNSW search over `halfvec`
  Qwen3-Embedding vectors run in parallel and are merged by Reciprocal Rank Fusion. A Qwen3-Reranker
  cross-encoder reranks the merged list, and Claude Haiku 5.5 or a local model can optionally judge the
  final ordering. Per-stage scores and timings are returned with every query, and the cascade degrades
  gracefully when a model server is down.
- **Agentic search with a small local model.** The model first decides whether a question needs search,
  rewrites it into several queries, checks with schema-constrained output whether the evidence is
  sufficient, and searches again (at most 4 steps) only when it is not. Answers stream with `[n]`
  citations. `engine=cloud` switches to Claude.
- **Contextual Retrieval.** Each chunk keeps a heading breadcrumb plus an optional LLM-written context,
  both used for BM25 and embeddings.
- **Concurrency and availability.** One Postgres (MVCC) holds everything. A durable job queue uses
  `SKIP LOCKED` with leases and retries. Ingestion is idempotent: content-hash dedup, plus upsert by
  `source_uri`. Circuit breakers guard every model call, `LISTEN/NOTIFY` pushes live SSE progress, and
  launchd and Compose supervise the processes.
- **Agent access both ways.** The `kb` CLI is token-efficient, emits JSON and uses stable exit codes, and
  pairs with an Agent Skill for Claude Code, Codex, Cursor and Gemini CLI. The same core is exposed as an
  MCP server with 8 annotated tools.
- **Evaluation built in.** `just eval` generates synthetic qrels and compares lexical, semantic, hybrid,
  hybrid + rerank and hybrid + rerank + LLM on recall@k, MRR@10 and nDCG@10.

## Quick start (Mac mini)

```sh
git clone https://github.com/SamWongML/knowledge-base ~/kb && cd ~/kb
scripts/macos-setup.sh   # brew deps, power settings, models, launchd agents, .env
just up                  # db + api + workers + caddy (OrbStack/Colima), waits for healthy
just smoke               # add → index → search → ask
open https://localhost   # web app
```

Full runbook (sizing per RAM, upgrades, backups, Tailscale, troubleshooting): [deploy/README.md](deploy/README.md).

### Use it from coding agents

```sh
uv tool install ./backend            # installs the `kb` CLI
ln -s "$PWD/skills/knowledge-base" ~/.claude/skills/knowledge-base
export KB_URL=https://localhost KB_TOKEN=...   # token from .env
kb search "how do we rotate the backup keys" -n 5
kb add notes/decision.md -c engineering -t adr --wait
kb ask "what did we decide about the job queue?" --stream

# or MCP
claude mcp add --transport http kb http://127.0.0.1:8787/mcp --header "Authorization: Bearer $KB_TOKEN"
```

See [skills/knowledge-base/README.md](skills/knowledge-base/README.md).

## Development

```sh
just dev            # Postgres in Docker; API (reload), worker and Vite dev server on the host
just test           # backend (ruff, pytest against Postgres) + frontend (biome, tsc, vitest)
```

Tests and CI use deterministic fake models (`KB_*_PROVIDER=fake`), so no GPU or network is needed.

## Documentation

- [Research synthesis (October 2026)](docs/research/README.md) and the [raw reports](docs/research/raw/)
- [Architecture](docs/architecture.md) · [Architecture decision records](docs/adr/README.md)
- [HTTP API](docs/api.md) (OpenAPI at `/api/docs`)

## Layout

| Path | What |
|---|---|
| `backend/src/kb/ingest` | parsers, chunking, contextual headers, job queue, workers |
| `backend/src/kb/search` | lexical, dense, fusion, rerank cascade, snippets, cache |
| `backend/src/kb/agent` | agentic search loop |
| `backend/src/kb/{mcp,cli}` | MCP server, `kb` CLI |
| `backend/src/kb/eval` | retrieval evaluation harness |
| `frontend/` | web app |
| `skills/knowledge-base` | Agent Skill |
| `deploy/`, `scripts/`, `compose.yaml`, `justfile` | Mac mini deployment |
