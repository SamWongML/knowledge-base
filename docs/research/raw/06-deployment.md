# 06 — Deployment on a single Apple Silicon Mac mini (always-on)

Researched 2026-10-16. Scope: Python API + workers, PostgreSQL (pgvector + ParadeDB pg_search), static React SPA, and local embedding, rerank and chat model servers, all on one Mac mini (32 GB assumed).

## 1. Containers vs native on macOS

**Main constraint:** every macOS container runtime runs a Linux VM (Virtualization.framework or libkrun), and Linux guests cannot use Metal. GPU inference therefore has to run natively on the host.

- **Podman + libkrun/krunkit (RamaLama):** this is the only real "GPU in a container" path on macOS. It exposes a Vulkan device (Venus) to the guest. RamaLama's macOS man page says you need the libkrun machine provider, and that running natively (`--nocontainer`, which uses MLX) performs "better than the podman krunkit solution" (https://www.mankier.com/7/ramalama-macos, https://podman-desktop.io/docs/podman/gpu).
  - A llama.cpp benchmark on an M2 Max measured a Vulkan-in-krunkit container at about 57% of native Metal for prompt processing and about 50% for token generation. Flash attention made it worse (https://github.com/ggml-org/llama.cpp/discussions/12985).
  - **Verdict:** it works, but you give up roughly half the throughput. Do not use it for production serving.
- **Apple `container` 1.0 (macOS 26):** released around July 2026. It runs one lightweight VM per container, supports OCI images, and adds `container machine`, `config.toml` and `container cp` (https://www.helpnetsecurity.com/2026/07/07/apple-container-open-source-linux-mac/).
  - No GPU passthrough is documented, and there is no first-party Compose support.
  - It is promising, but for a compose-based stack it is still less convenient than Docker-API-compatible runtimes.
- **OrbStack:** Docker-API compatible and fast to start. It has dynamic memory (it returns RAM to macOS), low idle CPU and power, and host networking. Containers reach the host via `host.docker.internal` (https://docs.orbstack.dev/compare/colima).
  - It is free for personal use; commercial use needs a paid license.
  - **Best choice for this box.**
- **Colima:** free and open source (Lima VM), and fully scriptable and headless (`colima start` can run from launchd).
  - It has fixed VM memory, slower networking and limited DNS.
  - It is the best fully open-source, headless option.
- **Docker Desktop:** heavier, and needs a paid license in larger orgs. It is fine, but offers no advantage here.

**Headless caveat:** Docker Desktop and OrbStack are GUI apps that start at user login. On an always-on server, enable automatic login, or use Colima via a LaunchAgent.

**FileVault caveat:** with FileVault on, the Mac waits at the pre-boot unlock screen after a power loss, and nothing starts until someone types the password. Either turn FileVault off on this dedicated box (and encrypt backups instead), or accept manual unlock after a power cut.

## 2. Native model serving (embeddings + rerank + chat)

| Runtime | Embeddings | Rerank | Chat | Notes |
|---|---|---|---|---|
| **llama.cpp `llama-server`** (Metal) | `/v1/embeddings` (`--embedding`, `--pooling`) | `/v1/rerank` (`--reranking`, `--pooling rank`) | `/v1/chat/completions` | Has a Prometheus `/metrics` (`--metrics`) endpoint and router mode (`--models-dir`, `--models-preset`, `--models-max`) (https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md) |
| Ollama | yes (`/api/embed`, `/v1/embeddings`) | **no native rerank endpoint**; workarounds score each pair through `/api/generate` (https://www.codewords.ai/blog/ollama-reranker) | yes | Easiest model pulls, but rerank is the missing piece |
| vllm-mlx | yes | yes | yes | MLX-native, `pip install vllm-mlx`, OpenAI-compatible, has metrics and health endpoints. Newer and less battle-tested (https://vllm-mlx.is-a.dev/) |
| LM Studio | yes | no stable rerank API | yes | GUI-centric; `lms` CLI server exists |
| mlx-lm / mlx-embeddings | chat / embeddings libraries | DIY | — | You would write the HTTP layer yourself |
| Infinity | yes | yes | no | Runs on Torch MPS on Mac; no chat, and Docker means CPU-only |

**Answer: llama.cpp `llama-server` is the one mature runtime that serves all three with OpenAI- and Cohere/Jina-style APIs on Metal.**

Router mode can put all three behind one port by choosing the model from the request's `model` field. Its preset format is documented as not yet stable, and eviction behaviour varies by build (https://glukhov.org/llm-hosting/llama-cpp/llama-server-router-mode/).

**Recommendation:** run **three `llama-server` processes on three ports** (8081 embed, 8082 rerank, 8083 chat). Each keeps its own flags, restarts independently, has no load/unload latency, and its memory is easy to reason about.

**GGUF gotcha for Qwen3-Reranker:** many community conversions lose the classification head. The server then returns HTTP 200 with ~0.0 scores. Use a conversion made with recent `convert_hf_to_gguf.py` (or one verified like https://huggingface.co/cicaba/Qwen3-Reranker-0.6B-GGUF-llamacpp-fixed), run with `--embedding --pooling rank --reranking`, and add a startup smoke test that asserts the relevant document scores above 0.5.

Qwen publishes an official `Qwen/Qwen3-Embedding-0.6B-GGUF`. Use last-token pooling for it (`--pooling last`).

**Memory sizing on 32 GB:**

| Component | Weights | KV / runtime | Total |
|---|---|---|---|
| Qwen3-Embedding-0.6B Q8_0 | ~0.64 GB | ctx 8k | ~1.0–1.5 GB |
| Qwen3-Reranker-0.6B Q8_0 | ~0.64 GB | ctx 8–16k | ~1.0–1.5 GB |
| Qwen3-8B Q4_K_M (16k ctx) | ~5.0 GB | ~2.3 GB (36 layers × 8 KV heads × 128 dim × 2 × fp16 ≈ 144 KB/token) | ~7.5 GB |
| Qwen3-4B Q4_K_M (alternative) | ~2.5 GB | ~1.2 GB | ~4 GB |

- The models total about 10 GB with 8B chat, or about 6.5 GB with 4B chat.
- That leaves about 20 GB for macOS (~4 GB), the container VM with Postgres (give it 6–8 GB; `shared_buffers` ~2 GB), and the API and workers.
- 32 GB is comfortable with Qwen3-8B. Use q8_0 KV cache (`-ctk q8_0 -ctv q8_0`) if you raise the context.
- macOS caps GPU-wired memory at roughly 65–75% of RAM by default; `sysctl iogpu.wired_limit_mb` raises it. That cap is not binding at these sizes.

## 3. Process supervision and staying up

- **launchd** is the native supervisor (`man launchd.plist`).
  - **LaunchAgents** (`~/Library/LaunchAgents`) run in the user session and need a logged-in user, so pair them with auto-login.
  - **LaunchDaemons** (`/Library/LaunchDaemons`, with a `UserName` key) start at boot without a login. Prefer them for model servers if FileVault is off.
  - Keys to set:
    - `RunAtLoad=true`.
    - `KeepAlive=true`, or a dict such as `{SuccessfulExit=false}`.
    - `ThrottleInterval` (minimum seconds between respawns; default 10).
    - `StandardOutPath` / `StandardErrorPath`.
    - `EnvironmentVariables`.
    - `WorkingDirectory`.
    - `ProcessType=Interactive`, to avoid background QoS throttling of inference.
  - Manage with `launchctl bootstrap gui/$UID <plist>`, `launchctl kickstart -k gui/$UID/<label>` and `launchctl bootout`.
- **`brew services`** just generates launchd plists. That is fine for Homebrew daemons (Caddy, Colima), but a custom plist gives you the flags you need for llama-server.
- **Compose:** set `restart: unless-stopped` on every service, plus `healthcheck` with `depends_on: condition: service_healthy`. OrbStack and Colima bring the daemon up at login or boot, and containers then restart automatically.
- **Power and sleep:**
  - `sudo pmset -a sleep 0 displaysleep 10 disksleep 0 powernap 0 womp 1 autorestart 1`, where `autorestart` means power on after a power failure.
  - `sudo systemsetup -setrestartfreeze on`.
  - In System Settings > Energy, enable "Prevent automatic sleeping" and "Start up automatically after a power failure".
  - `caffeinate -s` is only a stopgap; pmset is the durable setting.
  - Add a small UPS if you can.

## 4. Exposure

- **Bind everything to `127.0.0.1`.** This covers the model servers, Postgres (`127.0.0.1:5432:5432` in compose, or no published port at all) and the API. Only the reverse proxy listens beyond localhost.
- **Caddy** is the single front door. It serves the static SPA (`file_server` with `try_files {path} /index.html`), reverse-proxies `/api/*` to the API, and handles TLS.
  - On the LAN, `tls internal` issues certificates from Caddy's local CA; install its root on clients.
  - Alternatively, use a real domain with the DNS-01 challenge.
- **Tailscale Serve** gives remote access with no open ports. `tailscale serve --bg --https=443 localhost:8080` publishes Caddy at `https://<host>.<tailnet>.ts.net` with an automatic Let's Encrypt certificate. Check it with `tailscale serve status`; undo it with `tailscale serve reset` (https://tailscale.com/docs/reference/tailscale-cli/serve.md).
  - Serve injects `Tailscale-User-Login` identity headers, which the API can trust because only Serve can reach it.
  - Do not use Funnel (public internet) unless you need it.
  - Use the standalone `tailscaled` (Homebrew or pkg) rather than the GUI app so it runs as a daemon before login.

## 5. Backups

- **Time Machine is not a database backup.** Postgres docs say a file-level copy of a running cluster is only valid if it is an atomic, consistent snapshot that includes WAL (https://dev.begriffs.com/ai-pg/blob/HEAD:/full-docs/txt/backup-file.txt, mirroring postgresql.org "File System Level Backup").
  - Time Machine copies files over time and is not atomic.
  - The data also sits inside the container VM's disk image, which is a huge file that changes constantly.
  - Exclude the VM image (`~/.orbstack`, `~/.colima`) from Time Machine and back up dumps instead.
- **Tier 1 (simple, sufficient):** run a nightly `pg_dump -Fc` (custom format, compressed, parallel restore with `pg_restore -j`) to `~/backups/pg`.
  - Keep 7 daily and 4 weekly copies.
  - Check that restore works by restoring into a scratch database weekly.
  - pgvector and pg_search indexes are rebuilt on restore. BM25 index builds take time, so measure it.
- **Tier 2 (point-in-time recovery):** use pgBackRest (or WAL-G) with WAL archiving to an external disk, inside the Postgres container or a sidecar. It is worth adding once the knowledge base becomes hard to recreate.
- **Offsite:** `restic` (Homebrew), encrypted and deduplicated. It backs up the dump directory, uploaded source documents and `.env`/configs to an external SSD and to S3/B2.
  - Run `restic forget --keep-daily 7 --keep-weekly 4 --keep-monthly 6 --prune` and `restic check` on a schedule.
  - Model weights are re-downloadable, so exclude them.
- Schedule backups with a launchd plist that uses `StartCalendarInterval`. Unlike cron, launchd runs missed jobs after wake.

## 6. Observability lite

- **Structured logs:**
  - The API and workers emit JSON to stdout (structlog or python-json-logger) with `request_id`, `job_id` and latency. Compose collects them; set `logging: {driver: json-file, options: {max-size: 10m, max-file: "5"}}` per service.
  - llama-server logs go to `~/Library/Logs/kb/*.log`. Rotate them with `newsyslog` (`/etc/newsyslog.d/kb.conf`).
- **Metrics:**
  - Expose `/metrics` from the API (prometheus-client or prometheus-fastapi-instrumentator), worker queue depth and job durations, and `llama-server --metrics` (tokens/s, KV usage, queue).
  - Add postgres_exporter if wanted.
  - An optional compose `observability` profile runs Prometheus and Grafana bound to localhost. Prometheus scrapes `host.docker.internal:808x/metrics` for the native servers.
- **Tracing:** add OpenTelemetry SDK auto-instrumentation (FastAPI, httpx, psycopg) that exports OTLP. Keep it off by default, and enable it with `OTEL_EXPORTER_OTLP_ENDPOINT` pointing at Jaeger or Grafana Tempo in the observability profile.
- **Uptime:**
  - An `/healthz` endpoint (liveness) and a `/readyz` endpoint (checks the DB, plus each model server's `/health`).
  - An external check from a Tailscale-connected device or Uptime Kuma (compose profile), plus a healthchecks.io-style ping from the nightly backup job, so a missed backup alerts you.

---

## RECOMMENDED DEPLOYMENT

**Topology (single Mac mini, macOS 26, 32 GB, FileVault off or accepted manual unlock, auto-login on):**

| Layer | Where | Why |
|---|---|---|
| llama-server ×3 (embed :8081, rerank :8082, chat :8083) | **Native**, LaunchDaemon/Agent, bound to 127.0.0.1 | Needs Metal |
| PostgreSQL 17 + pgvector + pg_search | **Compose** (`paradedb/paradedb` image, which bundles pgvector), named volume | Avoids building extensions on macOS |
| API (uvicorn), workers | **Compose** | Reproducible; reach models via `host.docker.internal` |
| Caddy (SPA + reverse proxy + TLS) | **Compose** (publishes 80/443) | One front door |
| Tailscale | **Native** daemon, `tailscale serve` → Caddy | Remote access with no open ports |
| Backups (pg_dump + restic) | Native launchd timer calling `docker compose exec` | Survives container restarts |
| Container runtime | OrbStack (or Colima for fully open source and headless) | — |

**launchd plist** (`/Library/LaunchDaemons/com.kb.llama-embed.plist`; copy it for rerank and chat):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.kb.llama-embed</string>
  <key>UserName</key><string>kb</string>
  <key>ProgramArguments</key><array>
    <string>/opt/homebrew/bin/llama-server</string>
    <string>-m</string><string>/Users/kb/models/Qwen3-Embedding-0.6B-Q8_0.gguf</string>
    <string>--host</string><string>127.0.0.1</string><string>--port</string><string>8081</string>
    <string>--embedding</string><string>--pooling</string><string>last</string>
    <string>-ngl</string><string>999</string><string>-c</string><string>8192</string>
    <string>-ub</string><string>8192</string><string>-np</string><string>4</string>
    <string>--metrics</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>ProcessType</key><string>Interactive</string>
  <key>StandardOutPath</key><string>/Users/kb/Library/Logs/kb/llama-embed.log</string>
  <key>StandardErrorPath</key><string>/Users/kb/Library/Logs/kb/llama-embed.log</string>
</dict></plist>
```

- **Rerank** uses the same plist with `--embedding --pooling rank --reranking`.
- **Chat** uses `-m Qwen3-8B-Q4_K_M.gguf -c 16384 -np 2 --jinja -fa on`.

**docker-compose.yml (core):**

```yaml
services:
  db:
    image: paradedb/paradedb:latest   # pin a version tag in practice
    environment: { POSTGRES_PASSWORD: ${PG_PASSWORD}, POSTGRES_DB: kb }
    volumes: [pgdata:/var/lib/postgresql/data, ./backups:/backups]
    ports: ["127.0.0.1:5432:5432"]
    healthcheck: { test: ["CMD-SHELL","pg_isready -U postgres"], interval: 10s, retries: 5 }
    restart: unless-stopped
  api:
    build: .
    command: uvicorn kb.api:app --host 0.0.0.0 --port 8000
    environment: &env
      DATABASE_URL: postgresql://postgres:${PG_PASSWORD}@db/kb
      EMBED_URL: http://host.docker.internal:8081/v1
      RERANK_URL: http://host.docker.internal:8082/v1
      CHAT_URL: http://host.docker.internal:8083/v1
    depends_on: { db: { condition: service_healthy } }
    healthcheck: { test: ["CMD","curl","-fs","localhost:8000/healthz"], interval: 15s }
    restart: unless-stopped
  worker:
    build: .
    command: python -m kb.worker
    environment: *env
    depends_on: { db: { condition: service_healthy } }
    restart: unless-stopped
  caddy:
    image: caddy:2
    ports: ["80:80", "443:443"]   # or 127.0.0.1:8080:80 if Tailscale-only
    volumes: [./Caddyfile:/etc/caddy/Caddyfile, ./web/dist:/srv, caddy_data:/data]
    restart: unless-stopped
volumes: { pgdata: {}, caddy_data: {} }
```

`Caddyfile`:

```
kb.lan {
  tls internal
  handle /api/* { reverse_proxy api:8000 }
  handle { root * /srv; try_files {path} /index.html; file_server }
}
```

**Justfile:**

```just
set dotenv-load

up:
    ./scripts/models-load.sh      # launchctl bootstrap the 3 llama-server plists (idempotent)
    docker compose up -d --build
down:
    docker compose down
    ./scripts/models-unload.sh
logs svc="":
    docker compose logs -f --tail=200 {{svc}}
model-logs:
    tail -F ~/Library/Logs/kb/llama-*.log
status:
    docker compose ps
    for p in 8081 8082 8083; do curl -fs localhost:$p/health && echo " :$p ok"; done
models-pull:
    hf download Qwen/Qwen3-Embedding-0.6B-GGUF --include "*Q8_0*" --local-dir ~/models
    hf download $RERANKER_GGUF_REPO --include "*Q8_0*" --local-dir ~/models   # a verified rank-pooling conversion
    hf download Qwen/Qwen3-8B-GGUF --include "*Q4_K_M*" --local-dir ~/models
models-restart:
    for l in embed rerank chat; do sudo launchctl kickstart -k system/com.kb.llama-$l; done
backup:
    docker compose exec -T db pg_dump -U postgres -Fc kb > backups/kb-$(date +%F).dump
    restic -r $RESTIC_REPO backup backups uploads .env
    restic -r $RESTIC_REPO forget --keep-daily 7 --keep-weekly 4 --keep-monthly 6 --prune
restore file:
    docker compose exec -T db pg_restore -U postgres -d kb --clean -j4 < {{file}}
smoke:
    ./scripts/smoke.sh   # embed dim check, rerank score > 0.5 on known pair, 1-token chat reply
```

**One-time host setup:**
- `sudo pmset -a sleep 0 disksleep 0 powernap 0 autorestart 1 womp 1`
- Enable auto-login and set OrbStack (or Colima) to start at login.
- `brew install llama.cpp just restic tailscale` (install Tailscale as a system daemon), then `tailscale serve --bg --https=443 localhost:443`. Alternatively, point it at a Caddy port bound to localhost.
- Load the three plists with `sudo launchctl bootstrap system /Library/LaunchDaemons/com.kb.llama-*.plist`, plus a `com.kb.backup` plist with `StartCalendarInterval` 03:00.
