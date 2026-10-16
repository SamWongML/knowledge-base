# kb operator + developer commands. `just` lists them. Runbook: deploy/README.md.

set dotenv-load
set shell := ["bash", "-euo", "pipefail", "-c"]

compose := "docker compose"

# List recipes
default:
    @just --list --unsorted

# ---- production stack (native model servers + compose) --------------------------------------

# Start model servers (launchd) and the compose stack; waits until healthy
up:
    scripts/models.sh start
    {{ compose }} up -d --build --wait --wait-timeout 300
    {{ compose }} ps

# Stop the compose stack (model servers keep running; `just models-stop` for those)
down:
    {{ compose }} down

# Restart one compose service, or all of them
restart svc="":
    {{ compose }} restart {{ svc }}

# Follow logs: a compose service (api, worker, db, caddy), "models", or everything
logs svc="":
    if [ "{{ svc }}" = models ]; then scripts/models.sh logs; else {{ compose }} logs -f --tail=200 {{ svc }}; fi

# Compose service status
ps:
    {{ compose }} ps

# Full status: launchd model servers, containers, and the API's own health report
status:
    scripts/models.sh status
    {{ compose }} ps
    curl -fsS -m 10 http://127.0.0.1:8787/api/health | jq . || echo "API not answering on :8787"

# Download GGUFs + pull Ollama models (idempotent; no power/launchd changes)
models-pull:
    #!/usr/bin/env bash
    set -euo pipefail
    . scripts/lib.sh
    for repo_link in "${KB_EMBED_GGUF_REPO:-Qwen/Qwen3-Embedding-0.6B-GGUF}:embed" "${KB_RERANK_GGUF_REPO:-ggml-org/Qwen3-Reranker-0.6B-Q8_0-GGUF}:rerank"; do
      repo="${repo_link%:*}" link="$KB_HOME/models/${repo_link##*:}.gguf" dir="$KB_HOME/models/${repo//\//__}"
      mkdir -p "$dir"
      uvx --from huggingface_hub hf download "$repo" --include "*Q8_0*.gguf" --include "*q8_0*.gguf" --local-dir "$dir"
      ln -sfn "$(find "$dir" -name '*.gguf' -type f | head -n1)" "$link"
      ok "$link -> $(readlink "$link")"
    done
    for m in "${KB_LLM_MODEL:-qwen3.5:9b}" "${KB_LLM_ROUTER_MODEL:-qwen3.5:4b}"; do ollama pull "$m"; done

# Start (or kickstart) the native model servers
models-start:
    scripts/models.sh start

# Stop the native model servers
models-stop:
    scripts/models.sh stop

# Restart the native model servers (e.g. after `brew upgrade llama.cpp`)
models-restart:
    scripts/models.sh restart

# Functional model checks: embedding dim, reranker discriminates, chat answers
models-check:
    scripts/check-models.sh

# Back up the database now (pg_dump -Fc + retention + restic if configured)
backup:
    scripts/backup.sh

# Restore: `just restore --drill` (scratch DB) or `just restore path/to.dump` (replaces live DB)
restore *args:
    scripts/restore.sh {{ args }}

# End-to-end smoke test (health, add, search, ask). Pass --models to check model servers too
smoke *args:
    scripts/smoke.sh {{ args }}

# Apply pending SQL migrations (the API also does this at startup)
migrate:
    {{ compose }} run --rm --no-deps api python -c 'import asyncio; from kb.db import migrate; print(asyncio.run(migrate()) or "up to date")'

# psql shell on the kb database
psql *args:
    {{ compose }} exec db psql -U kb -d kb {{ args }}

# ---- development --------------------------------------------------------------------------------

# Dev loop: Postgres in Docker; API (reload), worker and Vite dev server on the host
dev:
    #!/usr/bin/env bash
    set -euo pipefail
    {{ compose }} up -d --wait db
    export KB_ENV=dev KB_LOG_JSON=false KB_WORKERS=1
    trap 'kill 0' EXIT INT TERM
    scripts/run-native.sh uvicorn kb.api.app:create_app --factory --reload --host 127.0.0.1 --port 8787 &
    scripts/run-native.sh kb-worker &
    (cd frontend && pnpm install --frozen-lockfile >/dev/null && pnpm dev) &
    echo "API http://127.0.0.1:8787/api/docs   UI http://localhost:5173   (Ctrl-C stops all)"
    wait

# Run backend + frontend tests (backend needs Postgres: `docker compose up -d db`)
test: test-backend test-frontend

# Backend: ruff + pytest against the compose/CI Postgres
test-backend *args:
    cd backend && uv run ruff check src tests && uv run ruff format --check src tests
    cd backend && KB_TEST_DATABASE_URL="${KB_TEST_DATABASE_URL:-postgresql://kb:${POSTGRES_PASSWORD:-kb}@127.0.0.1:${KB_PG_PORT:-5432}/postgres}" uv run pytest -q {{ args }}

# Frontend: lint, typecheck, unit tests
test-frontend:
    cd frontend && pnpm install --frozen-lockfile && pnpm lint && pnpm typecheck && pnpm exec vitest run

# Retrieval eval on the host: `just eval generate -n 100 --out q.jsonl`, then `just eval run --qrels q.jsonl`
eval *args="--help":
    scripts/run-native.sh python -m kb.eval {{ args }}

# Build the application image without starting anything
build:
    {{ compose }} build
