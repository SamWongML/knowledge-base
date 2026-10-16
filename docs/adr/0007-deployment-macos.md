# 0007. Deployment on macOS: compose for stateless services and Postgres, launchd for native models, Caddy + Tailscale

- Status: accepted
- Date: 2026-10-16
- Related: [research §6](../research/README.md#6-deployment-on-macos), [raw/06](../research/raw/06-deployment.md), [architecture §5](../architecture.md#5-process-topology-on-the-mac-mini), [ADR 0003](0003-models-and-local-serving.md)

## Context

One always-on Mac mini runs:
- the API, ingestion workers and Postgres with extensions;
- a static SPA;
- three model servers.

Containers on macOS cannot use Metal, but they make Postgres and its extensions reproducible and give the API and worker clean restarts. The box must come back by itself after reboots and power cuts, and must be reachable remotely without opening ports.

## Decision

- **Native, launchd** (LaunchDaemons with `KeepAlive`, `RunAtLoad`, `ProcessType=Interactive`, logs in `~/Library/Logs/kb/`):
  - `llama-server` embed (:8081) and rerank (:8082).
  - Ollama (:11434).
  - `tailscaled`.
  - A nightly backup job: `pg_dump -Fc` + restic, via `StartCalendarInterval`.
- **Compose** (OrbStack, or Colima for a fully open-source, headless setup):
  - `db`: ParadeDB `0.26.2-pg18`, named volume, `pg_isready` health check.
  - `api` (`kb-api`) and `worker` (`kb-worker`). They reach the models at `host.docker.internal`.
  - `caddy`.
  - Every service uses `restart: unless-stopped` and health-gated `depends_on`.
- **Exposure:**
  - Everything binds to `127.0.0.1`.
  - **Caddy** is the single front door: it serves the SPA and proxies `/api/*` and `/mcp` with SSE-friendly (unbuffered) settings.
  - **Tailscale Serve** publishes Caddy at `https://<host>.<tailnet>.ts.net`. Funnel is not used.
- **Host hardening:**
  - `pmset -a sleep 0 disksleep 0 autorestart 1 womp 1`.
  - Auto-login, so the container runtime starts.
  - A UPS if available.
  - Either FileVault off with encrypted backups, or accept manual unlock after a power cut.
- **Backups:**
  - Nightly `pg_dump -Fc`, keeping 7 daily and 4 weekly copies.
  - restic to an external disk and offsite.
  - A weekly scripted restore into a scratch database.
  - Time Machine excludes the VM image.
  - pgBackRest PITR is added when the corpus becomes hard to recreate.

## Alternatives considered

| Option | Why not |
|---|---|
| Everything native (Postgres.app + pg_search pkg + launchd for the API) | Viable and documented as a fallback. Compose gives reproducibility with CI and simpler upgrades. |
| Everything in containers, including models | Vulkan-in-VM reaches about 50% of Metal throughput. |
| Apple `container` 1.0 | No compose, and no GPU. |
| Docker Desktop | Heavier, with licensing friction. No advantage. |
| Port-forwarding / Funnel / Cloudflare Tunnel | Exposes the service publicly. Unneeded for personal or team use on a tailnet. |
| Time Machine for the database | Not an atomic snapshot of a running cluster. |

## Consequences

- There are two supervisors (launchd and compose). A `just up`/`status` wrapper hides that, and health checks cover both.
- The model servers' lifecycles are independent of the app's. A model restart triggers degraded modes, not failures ([architecture §6](../architecture.md#6-concurrency-and-availability)).
- OrbStack needs a paid licence for commercial use. Colima is the free swap.
- Restore drills must include the BM25 and HNSW rebuild time.
