# 0005. Agent access: CLI + Agent Skill as the primary path, MCP server too, one shared core

- Status: accepted
- Date: 2026-10-16
- Related: [research §4](../research/README.md#4-agent-access), [raw/04](../research/raw/04-agent-access.md), [api.md](../api.md), `kb.cli`, `kb.mcp`

## Context

Coding agents need to search, read and add to the kb. Claude Code, Codex CLI, Cursor and Gemini CLI all have a shell and all read Agent Skills (`SKILL.md`), which are loaded by progressive disclosure at about 100 idle tokens. MCP tool schemas sit in context from the start of a session (one measurement: seven servers took 67k tokens). On the other hand, MCP is the only option for agents without a shell (hosted connectors, IDE chat modes), and it gives typed I/O. MCP spec **2026-07-28** is stateless and Streamable-HTTP-only. The Python SDK `mcp` 2.3 implements it and still serves 2025-11-25 clients.

## Decision

- **Shared core.** `kb.ingest.service`, `kb.search.pipeline` and `kb.agent.loop`, using one set of Pydantic models (`kb/schemas.py`). REST, MCP and the CLI are thin adapters over it.
- **Primary path: the `kb` CLI** (Typer + httpx, thin HTTP client, installed with `uv tool install`):
  - Configured by `KB_URL` and `KB_TOKEN`.
  - Concise text on a TTY and `--json` otherwise. Data goes to stdout and diagnostics to stderr.
  - Stable exit codes: 0 ok, 2 usage, 3 not found, 4 auth/config, 5 conflict, 6 unreachable.
  - Writes support `--dry-run` and are idempotent through content-hash dedup.
  - It ships a **`knowledge-base` Agent Skill**, installed in `~/.agents/skills/` and symlinked into `~/.claude/skills/`.
- **Also: an MCP server** mounted at `/mcp` in the same ASGI app (Streamable HTTP, behind the same bearer token).
  - Eight namespaced tools that mirror the CLI: `kb_search`, `kb_ask`, `kb_read`, `kb_list`, `kb_add`, `kb_update`, `kb_delete`, `kb_collections`.
  - The tools are hand-written, with concise output, pagination or truncation hints, and actionable errors.

## Alternatives considered

| Option | Why not |
|---|---|
| MCP only | Higher idle context cost. Harder for humans to debug. Does not compose with `jq` or scripts. |
| CLI only | Excludes agents with no shell, and clients that prefer typed tools. |
| `FastMCP.from_fastapi` auto-generated tools | Endpoint wrappers go against Anthropic's tool-design guidance (few, workflow-level tools). |
| Separate MCP process | Duplicates configuration, pools and auth. Mounting it in the API process is free. |
| OAuth 2.1 for MCP | Unneeded on localhost or a tailnet. Deferred until hosted agents need access. |

## Consequences

- One behaviour across three surfaces. A fix in the core fixes REST, the CLI and MCP together.
- The CLI's `--json` shapes and the MCP `outputSchema` must stay in sync with `schemas.py`. Treat them as a public contract.
- `kb_delete` over MCP is a destructive capability. Keep it behind the token, and consider removing it if agents misuse it.
- Evaluate both paths on 10–20 realistic tasks, tracking tokens and calls per task.
