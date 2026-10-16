# State of the art for a self-hosted knowledge base (October 2026)

This is a synthesis of six research reports written on 2026-10-16 for a knowledge base (kb) that runs on one Apple Silicon Mac mini (M4 / M4 Pro, 32–64 GB). Each section below covers what the research found, what we chose and why, what we rejected, and what is still open. The raw reports hold the detail, version pins and full source lists:

| # | Track | Raw report |
|---|---|---|
| 1 | Hybrid retrieval and reranking | [raw/01-retrieval-reranking.md](raw/01-retrieval-reranking.md) |
| 2 | Agentic search with small local models | [raw/02-agentic-search.md](raw/02-agentic-search.md) |
| 3 | Storage, job queue, API stack, single-node HA | [raw/03-storage-backend.md](raw/03-storage-backend.md) |
| 4 | Agent access: MCP vs CLI + Agent Skills | [raw/04-agent-access.md](raw/04-agent-access.md) |
| 5 | Frontend stack and design | [raw/05-frontend.md](raw/05-frontend.md) |
| 6 | Deployment on macOS | [raw/06-deployment.md](raw/06-deployment.md) |

The decisions are recorded as ADRs in [../adr/](../adr/README.md). The resulting system is described in [../architecture.md](../architecture.md).

**One-paragraph summary.** All state lives in a single PostgreSQL 18 database: pgvector HNSW over `halfvec(1024)`, ParadeDB pg_search for BM25, and a small `SKIP LOCKED` job table. Retrieval is a cascade: BM25 and dense, 100 candidates each, are fused with RRF (k=60), reranked by a local Qwen3-Reranker-0.6B cross-encoder, and optionally judged listwise by Claude Haiku 5.5. Small local Qwen3.5 models run the agentic layer. It routes first and loops rarely, with a hard budget of 4 steps. Coding agents reach the kb through a `kb` CLI plus an Agent Skill, with an MCP server on the same core as a second path. The UI is a Vite 8 + React 19 SPA with an editorial look. Model servers run natively under launchd for Metal; everything else runs in compose.

---

## 1. Hybrid retrieval and reranking

**Findings.**
- Native Postgres `tsvector` ranking is not BM25. It scored nDCG@10 0.33 on SciFact, against ~0.68 for the BM25 extensions ([Particula](https://particula.tech/blog/pg-search-vs-pg-textsearch-bm25-postgres)). A BM25 extension is therefore mandatory.
- **Qwen3-Embedding-0.6B** is the best permissively licensed sub-1B embedder: Apache-2.0, 32K context, MRL down to 32 dims, MTEB-Eng-v2 retrieval 61.83. Leaving out the query instruction costs 1–5% ([model card](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B)).
  - jina-embeddings-v5-small scores higher but is CC-BY-NC.
  - The 4B Qwen model adds about 6 points at roughly 7× the indexing cost.
- **Qwen3-Reranker-0.6B** reaches MTEB-R 65.80, against 57.03 for the old default bge-reranker-v2-m3 ([model card](https://huggingface.co/Qwen/Qwen3-Reranker-0.6B)). It is served by `llama-server --reranking` at `/v1/rerank`.
  - Many community GGUFs lack the classifier tensors and return garbage scores (llama.cpp #16407). Use the [ggml-org build](https://huggingface.co/ggml-org/Qwen3-Reranker-0.6B-Q8_0-GGUF).
- **Fusion.** RRF (k=60) is the robust zero-config default. A tuned convex combination beats it ([Bruch et al.](https://arxiv.org/abs/2210.11934v2)), but with a strong reranker downstream, candidate recall@100 matters more than the fusion function.
- **Contextual Retrieval** prepends an LLM-written situating context to each chunk before both embedding and BM25. It cut top-20 retrieval failures by 49%, and by 67% with reranking ([Anthropic](https://www.anthropic.com/news/contextual-retrieval)). It is the highest-ROI indexing improvement.
- ColBERT and learned sparse models (SPLADE, OpenSearch neural sparse v3) give small marginal gains over contextual BM25 + dense + rerank. The storage cost of ColBERT on Postgres is prohibitive.

**What we chose.**
- **BM25 top-100 ‖ dense top-100 → RRF k=60 → cross-encoder over the top 50 → optional Claude Haiku 5.5 (`claude-haiku-5-5`) listwise judge over the top 20 → per-document diversity → top-k.**
  - Haiku is cheap enough to act as a reranker: about $0.001 per query at $0.10 / $0.50 per MTok ([models overview](https://platform.claude.com/docs/en/models/overview)).
- Chunks are heading-aware, about 384 tokens with 48 overlap, and carry a `heading_path` breadcrumb.
- Contextual chunk headers are optional (`KB_CONTEXTUAL_CHUNKS`). The `context` column is kept separate so it can be regenerated.
- Neighbour expansion (small-to-big) is available per request.

**Rejected.**
- ColBERT as a first-stage index.
- SPLADE (non-commercial licence and query-side inference).
- jina v5 and jina-reranker-v3.5 (CC-BY-NC).
- Native tsvector ranking.
- Learned fusion.

**Open questions / measure.**
- recall@100 before rerank, nDCG@10 and MRR@10 after, and p50/p95 latency per stage. Use an LLM-generated plus real-query golden set and run an ablation grid (raw/01 §7).
- Whether convex fusion beats RRF on our data.
- Whether the 4B embedder or reranker is worth the latency.
- Actual reranker latency on M4: the estimate of 0.3–1 s for 50 pairs is extrapolated.

## 2. Agentic search with small local models

**Findings.**
- Qwen3.5 small dense models (Apache-2.0, 262K context) shipped in March 2026. On BFCL-v4, **9B scores 0.661 ("usable") and 4B 0.503 ("marginal")**; ≤2B is unreliable ([d-central](https://d-central.tech/local-llm-agent-capability/)).
- Small models are good **graders and routers** when output is short and schema-constrained, and fragile at free-form ReAct ([Tiny-Critic RAG](https://arxiv.org/abs/2603.00846)).
- *Is Agentic RAG worth it?* found:
  - Query rewriting adds +2.8 nDCG@10.
  - **Extra retrieval rounds add nothing** over one pass plus a reranker.
  - Unnecessary retrieval costs 49 F1 on FEVER.
  - Cost is 2.7–3.9× the tokens.

  The conclusion is to route first and loop rarely ([arXiv 2601.07711](https://alphaxiv.org/overview/2601.07711v1)).
- Removing duplicate already-seen documents between steps gives +5.6% EM and −10.5% turns ([Adobe Research](https://research.adobe.com/publication/test-time-strategies-for-more-efficient-and-accurate-agentic-rag/)).
- Small Qwen models hallucinate about 80% closed-book, so grounding with validated citations is essential.
- Decode speed is bandwidth-bound: the M4 has 120 GB/s and the M4 Pro 273 GB/s. Prefill dominates RAG latency (about 900 tok/s), so per-step evidence must stay small.
- Ollama (MLX backend since 0.19) gives OpenAI-compatible tools and grammar-constrained JSON. It has no rerank endpoint.

**What we chose.**
- **qwen3.5:9b** as agent and synthesizer, and **qwen3.5:4b** as router and grader, both served by Ollama in non-thinking mode with JSON-schema output.
- The loop:
  1. The **router** classifies the question as lookup, simple, agentic or chitchat.
  2. **Multi-query** expansion is fused with RRF.
  3. A **sufficiency check** returns `{sufficient, missing}`.
  4. If evidence is insufficient, a bounded loop runs for **at most 4 steps** with novelty and stall detection.
  5. The final answer is a **streamed, grounded answer** with `[n]` citations, validated against the evidence set.
- `engine=cloud` switches synthesis to `claude-haiku-5-5`.
- With insufficient evidence, the answer says "not found in the knowledge base" instead of drawing on parametric memory.

**Rejected.**
- Free-form ReAct, Self-RAG and Search-R1-style policies (need fine-tuned checkpoints).
- Open-ended deep-research loops.
- Thinking mode for local steps (token burn).
- The 27B dense model (about 8 tok/s on M4 Pro).

**Open / measure.**
- A 50–100 question golden set covering answer faithfulness, citation precision, steps per question and escalation rate.
- An A/B of `qwen3.6:35b` (MoE, about 3B active) on 48 GB+ machines.
- Tuning the route thresholds.
- Haiku's daily spend ceiling.

## 3. Storage, job queue and API stack

**Findings.**
- One Postgres gives ACID across metadata, vectors and the BM25 index. A chunk can never be lexically searchable without its row, delete cascades, and one backup covers everything ([ParadeDB hybrid manual](https://paradedb.com/blog/hybrid-search-in-postgresql-the-missing-manual)).
- **PostgreSQL 18** adds async I/O and native `uuidv7()`.
- pgvector 0.8 brings `halfvec` (half the memory, negligible recall loss) and iterative index scans for filtered kNN ([pgvector 0.8.0](https://www.postgresql.org/about/news/pgvector-080-released-2952/)). CVE-2026-3172 means pgvector must be ≥ 0.8.2.
- BM25 options are pg_search (AGPL, Tantivy, phrase, fuzzy and snippets), pg_textsearch (PostgreSQL licence, no phrase or fuzzy yet) and VectorChord-bm25. pg_search and pg_textsearch cannot coexist because both register an access method named `bm25`.
- Second datastores were all rejected for one box. Qdrant brings dual writes. LanceDB fails under concurrent writers ([FAQ](https://docs.lancedb.com/faq/faq-oss)). SQLite has a single writer.
- For the queue, the research recommended Procrastinate on the same Postgres, with pgqueuer and DBOS as alternatives. Redis-based queues lose transactional enqueue.

**What we chose.**
- A **single Postgres 18** with pgvector HNSW (`m=16, ef_construction=128`, `halfvec_cosine_ops`) and ParadeDB pg_search, using English stemming over `content`, `context` and `heading_path`, with the filter columns included in the index.
- **pg_search over pg_textsearch.** It ships in the ParadeDB image together with pgvector, it installs natively on macOS through the ParadeDB pkg on Postgres.app, and it supports phrase, fuzzy and snippets. AGPL-3.0 is acceptable for an unmodified, self-hosted personal deployment.
- **Deliberate deviation:** instead of Procrastinate, a **built-in `jobs` table drained with `FOR UPDATE SKIP LOCKED`**.
  - It needs no extra dependency.
  - Enqueue is transactional.
  - Jobs hold leases (`lease_until`) and retry with backoff (`max_attempts`).
  - A partial unique `dedup_key` index acts as a queueing lock.
  - See [ADR 0008](../adr/0008-postgres-skip-locked-job-queue.md).
- FastAPI, psycopg 3.3 async with a pool, raw SQL, Uvicorn, and sse-starlette for SSE fed by `LISTEN/NOTIFY`.

**Rejected.**
- Qdrant, LanceDB, Milvus Lite, SQLite + sqlite-vec, and Elasticsearch/OpenSearch.
- pgvectorscale and VectorChord. They are easy later swaps if HNSW insert cost or RAM becomes the bottleneck.
- Procrastinate and Redis-backed queues.

**Open / measure.**
- HNSW insert throughput under concurrent ingest.
- BM25 index rebuild time on `pg_restore`.
- Whether to split the `api` and `ingest` roles with separate connection caps.
- An embedding cache keyed by `(model, chunk_sha256)` for re-embeds.

## 4. Agent access

**Findings.**
- MCP **2026-07-28** is a breaking revision: a stateless core, Streamable HTTP only, JSON Schema 2020-12, and deterministic tool order. The Python SDK `mcp` 2.3 implements it and still serves 2025-11-25 clients ([changelog](https://modelcontextprotocol.net/specification/2026-07-28/changelog)).
- MCP schemas cost context: seven servers took 67k tokens ([claude-code #11364](https://github.com/anthropics/claude-code/issues/11364)). Tool search mitigates this.
- **Agent Skills** (`SKILL.md`) are an open standard with 30+ adopting tools. They load by progressive disclosure, about 100 idle tokens. One folder in `~/.agents/skills/` plus a symlink in `~/.claude/skills/` covers Claude Code, Codex CLI, Cursor and Gemini CLI.
- MCP still wins for agents with no shell, such as hosted connectors and IDE chat modes.
- Anthropic's guidance is a few workflow-level, namespaced tools with concise output, pagination and actionable errors ([Writing tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents)).

**What we chose.**
- A **shared Python core** with three thin adapters: REST, the `kb` CLI and MCP at `/mcp`.
- **Primary path:** the `kb` CLI plus the Agent Skill. The CLI has `--json` output, stable exit codes, `--dry-run` writes, and content-hash idempotency.
- **Secondary path:** an MCP server mounted in the same ASGI app, exposing eight `kb_*` tools that match the CLI one-to-one, behind the same bearer token.

**Rejected.**
- MCP-only.
- `FastMCP.from_fastapi` auto-generated tools, which are endpoint wrappers.
- OAuth 2.1, deferred until hosted agents need access.

**Open / measure.** Run 10–20 realistic agent tasks through both paths, tracking tokens and tool calls per task, and decide whether `kb_delete` should stay exposed over MCP.

## 5. Frontend

**Findings.**
- Vite 8 (Rolldown + Oxc), React 19.3, TanStack Router (type-safe search params) and TanStack Query are a mature SPA stack with no Node runtime in production.
- Since July 2026, **Base UI is shadcn's default primitive layer** ([changelog](https://ui.shadcn.com/docs/changelog/2026-07-base-ui-default)).
- **Streamdown** renders incomplete streaming Markdown safely.
- `fetch` + `eventsource-parser` is required for POST SSE, because native `EventSource` cannot send a body or headers.
- TypeScript 7 (Go-native) has no programmatic API until 7.1, which pushes linting to Biome.

**What we chose.**
- Vite 8 + React 19 + TanStack Router/Query + Tailwind 4 + Base UI (via shadcn) + Streamdown + cmdk.
- An **"editorial reading room" visual direction**: warm paper and ink, Instrument Serif / Newsreader / Inter / Commit Mono, and a single vermilion accent for citations. It borrows Linear-style keyboard-first interaction (Cmd-K, `j/k`).
- The SPA is built to `frontend/dist` and served by the API (static_dir) or by Caddy.

**Rejected.**
- Next.js (RSC and a Node server buy nothing here).
- TanStack Start.
- SvelteKit 3: two weeks old, and its ecosystem is not React-first.
- SolidStart.
- The Linear-clone and terminal-brutalist aesthetics.

**Open / measure.**
- Stability of the Oxc React Compiler; fall back to Babel if needed.
- Bundle size and LCP on the reader.
- Accessibility of the citation hover cards.

## 6. Deployment on macOS

**Findings.**
- Every macOS container runtime is a Linux VM without Metal. Vulkan-in-krunkit reaches about 50–57% of native throughput ([llama.cpp #12985](https://github.com/ggml-org/llama.cpp/discussions/12985)). Model servers therefore run natively.
- `llama-server` is the one mature runtime that serves embeddings, rerank and chat on Metal ([server README](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)).
- OrbStack is the best compose runtime, and Colima is the fully open-source, headless alternative.
- Time Machine is not a database backup. Use `pg_dump -Fc` nightly, restic offsite, and pgBackRest for PITR later.
- **Tailscale Serve** gives HTTPS remote access with no open ports ([docs](https://tailscale.com/docs/reference/tailscale-cli/serve.md)).
- FileVault blocks unattended reboot. `pmset autorestart 1` plus a UPS is the cheapest HA.

**What we chose.**
- Native launchd services:
  - `llama-server` ×2: embed on :8081, rerank on :8082.
  - **Ollama** for chat on :11434. The deployment report suggested a third llama-server, but the agentic report favours Ollama for MLX speed, schema-constrained JSON and model management, and the app's OpenAI-compatible client makes either a config change.
- Compose services: Postgres (ParadeDB image), API, worker and Caddy.
- Everything binds to 127.0.0.1. Caddy is the single front door, published over Tailscale Serve.

**Rejected.**
- GPU-in-container (half the throughput).
- Apple `container` 1.0 (no compose support).
- Docker Desktop.
- Tailscale Funnel, which exposes the service publicly.
- Time Machine as a database backup.

**Open / measure.**
- Memory headroom on 32 GB with the 9B and 4B chat models resident alongside Postgres.
- Cold-boot recovery time after a power cut.
- Restore drill timings, including the BM25 rebuild.

---

## Cross-cutting principles

1. **Pluggable everything.** Every model stage is a provider behind an OpenAI-compatible or llama.cpp endpoint, configured by `KB_*` environment variables. Tests run on deterministic fakes.
2. **Degrade, don't die.** Circuit breakers on every model call. Search falls back to BM25-only and reports `degraded: [...]`.
3. **Measure before adding stages.** Optional stages (LLM judge, contextual headers, cloud answers) stay off by default until the eval harness shows a gain.
4. **One source of truth.** Postgres holds the data, the queue and the event fan-out.
