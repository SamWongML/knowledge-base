# Operator runbook: kb on one Apple Silicon Mac mini

```
                       LAN / tailnet
                            │ 80/443 (LAN)  ·  127.0.0.1:8080 (tailscale serve)
┌───────────────────────────┼──────────────── Docker VM (OrbStack / Colima) ─┐
│  caddy ── api (kb-api, 2 uvicorn workers) ── db (ParadeDB 0.26.2, PG 18)   │
│                   worker ×2 (kb-worker) ─────┘                             │
└────────────┬───────────────────────────────────────────────────────────────┘
             │ host.docker.internal
┌────────────┴─ macOS host, launchd (Metal) ─────────────────────────────────┐
│ llama-server :8081 embed (Qwen3-Embedding-0.6B Q8_0, --pooling last)        │
│ llama-server :8082 rerank (Qwen3-Reranker-0.6B Q8_0, --reranking)           │
│ ollama       :11434 chat (qwen3.5:9b) + router (qwen3.5:4b)                 │
│ com.kb.backup 03:15 nightly pg_dump (+ restic)                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

The model servers run natively because no macOS container runtime can use Metal. Inside a container
they would run on the CPU, or at about half speed through Vulkan with krunkit. Everything else runs in
Compose with `restart: unless-stopped` and healthchecks. The model servers, Postgres and the API are
bound to `127.0.0.1`. Only Caddy listens on the network.

| Path | What |
|---|---|
| `compose.yaml`, `.env` | containers and all settings (`.env.example` documents every `KB_*`) |
| `deploy/caddy/Caddyfile` | TLS (`tls internal`), compression, security headers, SSE passthrough |
| `deploy/launchd/*.plist` | LaunchAgent templates (`__HOME__`/`__REPO__`/`__BREW__` are rendered by setup) |
| `scripts/macos-setup.sh` | idempotent host setup; re-run it after changing plists or `.env` model settings |
| `~/.kb/models`, `~/.kb/backups` | GGUF weights, database dumps |
| `~/Library/Logs/kb/*.log` | model server and backup logs (`just logs models`) |

## 1. Install (first boot)

1. **macOS.** Create the admin user that will own the service. Enable **automatic login** for that
   user (System Settings > Users & Groups), because LaunchAgents and OrbStack only start after login.
   Decide about FileVault now: see [Troubleshooting](#7-troubleshooting).
2. **Install Homebrew and a container runtime.** Run `brew install --cask orbstack`, open OrbStack
   once, and enable *Start at login*. OrbStack's commercial use needs a licence. The open-source,
   headless alternative is `brew install colima docker docker-compose && brew services start colima`,
   then `colima start --cpu 4 --memory 8 --vm-type vz`.
3. **Clone and set up the host.**
   ```sh
   git clone <repo> ~/kb && cd ~/kb
   scripts/macos-setup.sh          # sudo for pmset; prompts once
   ```
   The setup script does the following:
   - checks for Apple Silicon;
   - runs `brew install llama.cpp ollama just uv jq restic`;
   - sets always-on power settings: `pmset sleep 0 disksleep 0 powernap 0 womp 1 autorestart 1`,
     plus restart-on-freeze;
   - creates `.env` with random `POSTGRES_PASSWORD` and `KB_API_TOKEN`;
   - downloads the GGUFs to `~/.kb/models`;
   - installs and loads the LaunchAgents, then pulls the Ollama models;
   - **verifies that the reranker separates a relevant passage from irrelevant ones.**
4. **Start the stack.**
   ```sh
   just up        # builds the image, starts db/api/worker×2/caddy, waits for healthy
   just smoke     # health → add a doc → indexed → search finds it → ask answers
   ```
5. **Point clients at it.** Add `KB_DOMAIN` (default `kb.local`) to your LAN DNS, or to each client's
   `/etc/hosts`, then trust Caddy's local CA:
   ```sh
   docker compose cp caddy:/data/caddy/pki/authorities/local/root.crt ./kb-root.crt
   # macOS client: open kb-root.crt → Keychain Access → Always Trust. iOS: install profile + enable in
   # Settings > General > About > Certificate Trust Settings.
   ```
   MCP clients on the Mac itself can use `http://127.0.0.1:8787/mcp` with `Authorization: Bearer $KB_API_TOKEN`.

## 2. Day to day

| Command | What it does |
|---|---|
| `just status` | launchd models + containers + `/api/health` per component |
| `just logs [api\|worker\|db\|caddy\|models]` | follow logs |
| `just models-check` | embedding dimension, reranker discriminates, chat answers |
| `just models-restart` | restart the model servers, e.g. after `brew upgrade llama.cpp` |
| `just restart api` | restart one container |
| `just psql` | SQL shell |
| `just backup` / `just restore --drill` | backup now / test the latest backup |

If a model server is down, the stack is degraded rather than down. Search falls back to BM25 when the
embedder is unavailable and skips reranking when the reranker is unavailable. Each search response
reports this in `degraded`, and `/api/health` reports `degraded`.

## 3. Upgrade

```sh
cd ~/kb && just backup                      # always first
git pull
docker compose pull db caddy                # only when compose.yaml changed tags
just up                                     # rebuilds the image; migrations run at API start
just smoke
brew upgrade llama.cpp ollama && just models-restart && just models-check
```

- **Rollback:** run `git checkout <previous>` and then `just up`.
- **Migrations:** they are additive and forward-only, so rolling back across a schema change means
  restoring the pre-upgrade dump (see section 4).
- **ParadeDB and Postgres upgrades:** a ParadeDB minor upgrade (a new image tag) may need
  `ALTER EXTENSION pg_search UPDATE`; read the release notes. A Postgres **major** upgrade (PG 18 → 19)
  uses a dump and restore: `just backup`, change the tag, `docker compose down`,
  `docker volume rm kb_pgdata`, `just up`, then `just restore --yes <dump>`.

## 4. Backups and the restore drill

- **Nightly job.** `com.kb.backup` (launchd, 03:15, and it catches up after sleep) runs
  `scripts/backup.sh`:
  - takes a `pg_dump -Fc` (zstd) and verifies it with `pg_restore --list`;
  - keeps 7 daily and 4 weekly dumps in `~/.kb/backups`;
  - when `RESTIC_REPOSITORY` is set, runs an encrypted, deduplicated `restic backup` of the dumps and
    `.env`, followed by `forget --prune` (7 daily, 4 weekly, 6 monthly) and a weekly `restic check`.
    Initialise it once with `restic init`.
  - when `KB_BACKUP_PING_URL` is set (healthchecks.io), pings it so that a *missed* backup alerts you.
- **Monthly drill.** Run `just restore --drill`. It restores the newest dump into `kb_restore_drill`,
  prints the document, chunk and collection counts, then drops the scratch database. It also measures
  how long index rebuilds take. HNSW and BM25 indexes are rebuilt during restore, and that dominates
  restore time.
- **Real restore.** Run `just restore ~/.kb/backups/daily/kb-<ts>.dump`. It asks you to confirm, stops
  api and worker, recreates `kb` from `template0`, runs `pg_restore -j 4`, then starts api and worker.
- **Time Machine.** It is not a database backup. Exclude `~/.orbstack` (or `~/.colima`) and
  `~/.kb/models` from it, and let it back up `~/.kb/backups` instead.
- **Point-in-time recovery.** If the knowledge base becomes hard to recreate, add pgBackRest or WAL-G
  with WAL archiving to an external disk. The ParadeDB image already ships `barman-cloud-*`.
- **In-container alternative (no restic).** Run `docker compose --profile backup up -d`. Use either
  this or the launchd job, not both.

## 5. Sizing

The Docker VM gets its memory from macOS: OrbStack allocates it dynamically, and Colima uses
`--memory`. Set it to about 2–3× `PG_SHARED_BUFFERS` plus 2 GB. The embedder and reranker need about
1–1.5 GB each at Q8_0.

| Mac RAM | `KB_LLM_MODEL` / `KB_LLM_ROUTER_MODEL` | `OLLAMA_CONTEXT_LENGTH` | Postgres (`PG_SHARED_BUFFERS` / cache) | Other |
|---|---|---|---|---|
| 16 GB | `qwen3.5:4b` / `qwen3.5:4b` | 16384 | 1GB / 3GB | `KB_WORKER_REPLICAS=1`; keep `KB_CONTEXTUAL_CHUNKS=false`; close other apps. You can use `engine=cloud` (Claude) for hard questions. |
| 24 GB | `qwen3.5:9b` / `qwen3.5:4b` | 16384–32768 | 1.5GB / 4GB | defaults otherwise |
| 32 GB | `qwen3.5:9b` / `qwen3.5:4b` (default) | 32768 | 2GB / 6GB (default) | `KB_CONTEXTUAL_CHUNKS=true` is affordable for small corpora |
| 64 GB | a larger Qwen3.5, such as the 27B dense or the 35B-A3B MoE (check `ollama list`/`ollama search`) / `qwen3.5:9b` | 65536 | 4GB / 12GB | Optionally Qwen3-Embedding-4B and Qwen3-Reranker-4B GGUFs. `KB_EMBEDDING_DIM` is fixed at database creation (halfvec, ≤ 4000), so a new dimension means a fresh database plus re-ingest. |

- **Applying changes.** After changing `OLLAMA_CONTEXT_LENGTH`, re-run `scripts/macos-setup.sh
  --skip-pmset`. After changing `PG_*` or `KB_*`, run `just up`.
- **GPU memory cap.** macOS caps GPU-wired memory at about 65–75% of RAM. You only need
  `sudo sysctl iogpu.wired_limit_mb=<MB>` for very large models.

## 6. Remote access with Tailscale

Use the standalone daemon (it runs before login):

```sh
brew install tailscale && sudo brew services start tailscale && sudo tailscale up
tailscale serve --bg --https=443 http://127.0.0.1:8080
tailscale serve status           # → https://<mac>.<tailnet>.ts.net (real Let's Encrypt cert)
tailscale serve reset            # undo
```

- **How it works.** Caddy's `127.0.0.1:8080` listener is plain HTTP that only the Mac can reach, and
  Tailscale terminates TLS in front of it.
- **Tailscale only, no LAN.** If you do not want LAN access, remove the `80`/`443` port mappings from
  the `caddy` service in `compose.yaml`.
- **Funnel.** Do not use Funnel (public internet) for a personal knowledge base.
- **Token.** Keep `KB_API_TOKEN` set either way.

## 7. Troubleshooting

**Power loss with FileVault on.** The Mac boots to the FileVault unlock screen, and nothing starts
(no login, no LaunchAgents, no OrbStack) until someone types the password. `autorestart 1` only gets
you as far as that screen. Choose one of these:
- turn FileVault off on this dedicated box and rely on restic's encryption for the backups that leave
  it;
- accept manual unlock (`sudo fdesetup authrestart` keeps planned reboots unattended);
- add a UPS, which helps either way.

**The reranker returns ~0 scores, or every score is the same.** The GGUF lost the Qwen3 reranker's
classification head. llama-server still answers HTTP 200, so ranking silently degrades to noise.
`just models-check` catches this.
- Fix: `rm ~/.kb/models/rerank.gguf`, then run `KB_RERANK_GGUF_REPO=ggml-org/Qwen3-Reranker-0.6B-Q8_0-GGUF
  scripts/macos-setup.sh --skip-pmset`.
- Also run `brew upgrade llama.cpp` (rank pooling needs a recent build).
- Confirm that the plist has `--reranking --pooling rank`.

**"Can Docker use the GPU?"** No. Every macOS container runtime is a Linux VM without Metal. Krunkit
and Vulkan reach only about 50–57% of native speed. Keep inference native (launchd).
- If the API logs `embedder down` while `just models-check` passes on the host, the containers cannot
  reach `host.docker.internal`.
- OrbStack and Docker Desktop map it to the host's loopback.
- On Colima, use `--network-address` or bind the servers to `0.0.0.0` (`--host 0.0.0.0`,
  `OLLAMA_HOST=0.0.0.0:11434`) and keep the macOS firewall on.

**A model server keeps restarting.** Check `just logs models`. `ThrottleInterval` (10 s) prevents tight
crash loops. The usual cause is a missing `~/.kb/models/*.gguf` symlink or a port already in use: Ollama.app
and `com.kb.ollama` must not run together.

**Nothing comes back after a reboot.** Check these in order:
- auto-login is on;
- OrbStack is set to *Start at login* (or Colima runs as a brew service);
- the agents are loaded: `launchctl print gui/$(id -u)/com.kb.llama-embed`;
- the containers are up: `docker compose ps`.

**Ingestion is stuck in `pending`.** Look at `just logs worker`. A crashed worker's jobs are
re-leased after `KB_JOB_LEASE_SECONDS`. A failure that repeats more than `KB_JOB_MAX_ATTEMPTS` times
marks the document `failed`, with the error stored on the document.

**Slow HNSW or BM25 index builds after a restore or bulk import.** Raise `PG_MAINTENANCE_WORK_MEM`
(the graph should fit in it), and give the Docker VM more memory.

## Fully-native alternative (no Docker)

Use this when you want no VM at all. You give up parity with CI's container image.

1. **Install Postgres.app (PostgreSQL 18).** Then install ParadeDB's `pg_search` package for
   PG 18 / arm64 (`pg_search@18--0.26.2.arm64_<macos>.pkg`, from ParadeDB's releases). Postgres.app
   already bundles pgvector.
2. **Configure Postgres.** In `postgresql.conf`, set `shared_preload_libraries = 'pg_search'` plus the
   `PG_*` values from `compose.yaml`, then restart Postgres. Next, create the role and database:
   `createuser -s kb -P` (use the `POSTGRES_PASSWORD` from `.env`) and `createdb -O kb kb`.
3. **Run the setup script.** Run `scripts/macos-setup.sh --native`. Besides the model agents, it
   installs `com.kb.api` and `com.kb.worker`, which run `scripts/run-native.sh kb-api` and
   `kb-worker` (`uv run` in `backend/`). That script reads `.env` and rewrites
   `host.docker.internal` to `127.0.0.1`. `KB_NATIVE_DATABASE_URL` overrides the database URL.
4. **Build the UI.** Run `pnpm -C frontend install && pnpm -C frontend build`. The API serves
   `frontend/dist`.
5. **TLS (optional).** Run `brew install caddy`. Use the same Caddyfile with `api:8787` changed to
   `127.0.0.1:8787`, then start it with `brew services start caddy`.
6. **Manage the services.** Run `scripts/models.sh status com.kb.api com.kb.worker`. Backups and
   restores detect native mode on their own, or you can set `KB_DEPLOY=native`. Use Postgres.app's
   `pg_dump` 18.
