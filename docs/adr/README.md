# Architecture Decision Records

The format is MADR-style: Context, Decision, Alternatives considered, Consequences. To record a new decision, copy [0000-template.md](0000-template.md) to the next number, and never edit an accepted ADR's decision in place: supersede it with a new ADR. The evidence behind these decisions is in the [research synthesis](../research/README.md), and the resulting system is in [architecture.md](../architecture.md).

| # | Decision | Status |
|---|---|---|
| [0001](0001-single-postgres.md) | One PostgreSQL 18 database for metadata, vectors (pgvector HNSW `halfvec`), BM25 (pg_search), queue and events | accepted |
| [0002](0002-hybrid-retrieval-cascade.md) | Hybrid retrieval: BM25 ‖ dense top-100 → RRF k=60 → cross-encoder top-50 → optional LLM judge top-20 → diversity → top-k | accepted |
| [0003](0003-models-and-local-serving.md) | Qwen3-Embedding/Reranker-0.6B on llama-server, qwen3.5:9b/4b on Ollama, native under launchd; Claude Haiku 5.5 optional | accepted |
| [0004](0004-agentic-search.md) | Agentic search: route first, multi-query, sufficiency check, ≤4-step loop, grounded streaming answers | accepted |
| [0005](0005-agent-access.md) | Agent access: `kb` CLI + Agent Skill primary, MCP at `/mcp` too, one shared core | accepted |
| [0006](0006-frontend-stack.md) | Frontend: Vite 8 + React 19 + TanStack + Tailwind 4 + Base UI + Streamdown, editorial design | accepted |
| [0007](0007-deployment-macos.md) | Deployment: compose for Postgres/API/worker/Caddy, launchd for native models, Tailscale Serve | accepted |
| [0008](0008-postgres-skip-locked-job-queue.md) | Built-in `SKIP LOCKED` job queue on Postgres instead of Procrastinate | accepted |
