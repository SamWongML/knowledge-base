# 04 — Agent Access: MCP vs CLI + Agent Skills (state as of Oct 2026)

Scope: how coding agents (Claude Code, Codex CLI, Cursor, Gemini CLI, and others) should search, read and write a self-hosted knowledge base (KB) running on a Mac mini.

## 1. MCP in late 2026

**Spec revision.** The current revision is **2026-07-28**. The release candidate came out on 2026-05-21, and the final version shipped on 2026-07-28 ([MCP blog](https://blog.modelcontextprotocol.io/tags/protocol/), [The Register](https://www.theregister.com/a/5276722)). It is the largest change since auth was added, and it is **not backward compatible** at the wire level. The previous revisions were 2025-11-25 and 2025-06-18. Main changes ([changelog](https://modelcontextprotocol.net/specification/2026-07-28/changelog), [summary](https://www.developersdigest.tech/blog/mcp-2026-07-28-breaking-changes)):
- **Stateless core.** The `initialize` handshake and `Mcp-Session-Id` are gone. Each request carries its protocol version and capabilities in `_meta`. A new `server/discover` RPC replaces the handshake. Any state that must survive between calls becomes a server-minted handle passed as an ordinary tool argument.
- **Streamable HTTP** is the only HTTP transport going forward, and HTTP+SSE is deprecated. POST requests carry `Mcp-Method` and `Mcp-Name` headers so gateways can route without reading the body. SSE resumability was removed. Optional push notifications go over a single `subscriptions/listen` stream.
- **Tools.** `inputSchema` and `outputSchema` accept full JSON Schema 2020-12, and `structuredContent` can be any JSON value. `tools/list` must return tools in a deterministic order, which helps prompt-cache hits, and carries `ttlMs` and `cacheScope` hints.
- **Auth** is aligned more closely with OAuth 2.1 and OIDC: RFC 9207 `iss` validation, credentials bound to the issuer, and Client ID Metadata Documents in place of Dynamic Client Registration, which is now deprecated.
- **Deprecated, with a 12-month window:** Roots, Sampling and Logging. Tasks moved to an extension (`io.modelcontextprotocol/tasks`), and MCP Apps (server-rendered UI) is an official extension.

**Primitives.** *Tools* are actions the model chooses to call (search, read, insert). *Resources* are URI-addressed data the client application attaches, for example `kb://doc/{id}`. Client support for resources is uneven, and most coding agents mainly surface tools. *Prompts* are templates the user invokes, which some clients expose as slash commands. In practice, almost everything in a KB server should be a tool. Documents can also be offered as resources for clients that support them.

**SDKs (checked against PyPI on 2026-10-16).** Official `mcp` **2.3.0** (2026-10-02) implements 2026-07-28 and still serves 2025-11-25 clients on the same endpoint. It renamed `FastMCP` to `MCPServer` (`from mcp.server import MCPServer`). Transport options moved to `run()`, and serving under a prefix is done by mounting the ASGI app ([what's new](https://py.sdk.modelcontextprotocol.io/whats-new/)). The standalone **FastMCP is now 4.1.0** (2026-10-08) and is built on SDK 2. FastMCP 3.x pins `mcp<2` and is still receiving 3.4.x maintenance releases ([migration notes](https://jacar.es/?p=7486)). There is no current "FastMCP 2.x" to target. Mounting into FastAPI:

```python
mcp_app = mcp.http_app(path="/")          # FastMCP
app = FastAPI(lifespan=mcp_app.lifespan)   # lifespan is mandatory
app.mount("/mcp", mcp_app)
```
([FastMCP FastAPI docs](https://gofastmcp.com/v3/integrations/fastapi); use `combine_lifespans` if the app already has a lifespan.) `FastMCP.from_fastapi(app)` can auto-generate tools from OpenAPI routes, but auto-generated endpoint wrappers break Anthropic's tool-design advice, so hand-write the tools.

**Auth for a local server.** On a LAN or tailnet, a static bearer token is pragmatic. FastMCP offers `StaticTokenVerifier`, documented as dev-only because tokens are stored in plaintext; `DebugTokenVerifier`, which takes a custom validate callable and can compare against a hashed token held in the Keychain or an env var; and `JWTVerifier` with JWKS ([docs](https://gofastmcp.com/servers/auth/token-verification)). All the major clients accept a header. Claude Code: `claude mcp add --transport http kb https://mini.tailnet/mcp --header "Authorization: Bearer $KB_TOKEN"`. Codex: `[mcp_servers.kb] url=... bearer_token_env_var="KB_TOKEN"` ([examples](https://developers.bookingsync.com/guides/mcp/connecting-clients)). Full OAuth 2.1 is only worth it if the KB is exposed to remote or hosted agents such as claude.ai connectors or cloud Codex.

**Context-token cost.** Every MCP tool schema sits in context from the start of the session. Measured examples: 7 servers took 67k tokens, 33.7% of a 200k window, and the GitHub MCP server alone took about 18k ([claude-code #11364](https://github.com/anthropics/claude-code/issues/11364), [mcp.directory](https://mcp.directory/blog/mcp-context-bloat-fix-2026-tool-search-code-mode-progressive-disclosure)). Mitigations:
- **Tool Search / `defer_loading`.** Anthropic reports 85% fewer tokens and better accuracy, for example Opus 4.5 going from 79.5% to 88.1% on MCP evals ([advanced tool use](https://www.anthropic.com/engineering/advanced-tool-use)). Claude Code switches this on automatically when MCP tool definitions exceed 10% of context.
- **Code mode.** The agent writes code against a typed API in a sandbox. Anthropic's "code execution with MCP" cut one workflow from 150k to 2k tokens ([post](https://www.anthropic.com/engineering/code-execution-with-mcp)). Cloudflare's Code Mode exposes only `search()` and `execute()` ([post](https://blog.cloudflare.com/code-mode-mcp/)).

The lesson for a KB is to keep the server to **about 5–7 tools** with tight descriptions. At that size, bloat does not matter: roughly 1.5–3k tokens.

**Anthropic's tool-writing guidance** ([Writing effective tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents)):
- Build a few workflow-level tools rather than endpoint wrappers, and namespace them (`kb_search`, `kb_read`).
- Return high-signal fields, with readable titles and paths instead of opaque IDs.
- Offer a `response_format: "concise" | "detailed"` option.
- Paginate, filter and truncate with sensible defaults. Claude Code caps tool output at 25k tokens.
- When output is truncated, tell the agent how to narrow the query.
- Make errors actionable, use unambiguous parameter names, and evaluate the tools with realistic multi-step tasks.

## 2. CLI + Agent Skills

**Format.** A skill is a directory containing `SKILL.md`: YAML frontmatter plus Markdown instructions, with optional `scripts/`, `references/` and `assets/` folders. Frontmatter fields ([agentskills spec summary](https://codex.danielvaughan.com/2026/05/05/agent-skills-open-standard-portable-skills-codex-cli-cross-agent/)):
- `name`: required, up to 64 characters, lowercase letters, digits and hyphens, matching the directory name.
- `description`: required, up to 1,024 characters, saying what the skill does and when to use it.
- Optional: `license`, `compatibility`, `metadata`, and `allowed-tools` (experimental).

Skills load by **progressive disclosure**:
1. About 100 tokens of metadata per skill at startup.
2. The body (keep it under about 5k tokens or 500 lines) when the skill activates.
3. Referenced files and scripts only when needed.

**Adoption.** Anthropic published Agent Skills as an open standard in December 2025, and governance has since moved to the Linux Foundation's Agentic AI Foundation (AAIF). Reports in 2026 count more than 30 adopting tools ([noqta](https://noqta.tn/en/news/skill-md-open-standard-30-ai-coding-tools-adoption-2026)). Discovery paths by tool:
- **Codex CLI:** `.agents/skills/`, `~/.agents/skills/`, `/etc/codex/skills/`.
- **Gemini CLI:** `~/.gemini/skills`, `.gemini/skills`, and the aliases `~/.agents/skills` and `.agents/skills`. It asks the user before activating a skill ([docs](https://geminicli.com/docs/cli/creating-skills/)).
- **Cursor:** `.agents/skills`, `.cursor/skills`, and the user-level equivalents. It also reads `.claude/skills` and `.codex/skills`, and adds the `paths` and `disable-model-invocation` fields ([docs](https://cursor.com/docs/skills)).
- **Claude Code:** `~/.claude/skills`, `.claude/skills`, and plugins.

**One skill folder installed in `~/.agents/skills/kb/` plus a symlink at `~/.claude/skills/kb` covers all four.**

**The case for CLI + skills** ([manveerc](https://manveerc.substack.com/p/mcp-vs-cli-ai-agents), [heyuan110](https://www.heyuan110.com/posts/ai/2026-07-04-cli-skills-vs-mcp/)):
- **Near-zero idle cost.** About 100 tokens of skill metadata, compared with thousands for MCP schemas. A cited example puts a Jira skill at about 300 tokens against about 55k for the full MCP toolset.
- **On-demand discovery.** `--help` is fetched only when needed.
- **Composability.** Output can be piped through `| jq`, `xargs` or loops. In one cited example, 150 lookups took one 500-token script instead of 150 MCP round-trips.
- **Familiarity.** Models are heavily trained on shell usage.
- **Easy debugging.** A human can run exactly the same command.
- **No session or transport fragility.**

**Counterarguments:**
- **No shell.** Sandboxed, remote or hosted agents cannot run your binary: claude.ai and ChatGPT connectors, cloud Codex and Claude Code on the web without network egress to the Mac mini, and IDE chat modes that have no terminal.
- **Credentials.** A CLI puts credentials into the agent's execution environment. MCP keeps them server-side, with per-user OAuth and audit ([Stacklok](https://stacklok.com/blog/mcp-vs-cli-tools-why-security-changes-the-answer/); note that Stacklok sells MCP governance products).
- **Discovery.** Without a skill, an agent does not know the CLI exists. MCP is discoverable by configuration.
- **Typed I/O.** Structured schemas and output validation come with MCP, whereas a CLI has to be disciplined about its JSON.
- **Bloat is fixable.** MCP context bloat is largely an implementation problem, and tool search and deferred loading have narrowed the gap.

The consensus is "both, with skills as the unifying layer": choose the transport per integration.

## 3. Best practices for an agent-facing KB CLI

Sources: [agent-friendly CLIs](https://codex.danielvaughan.com/2026/04/28/building-agent-friendly-clis-codex-cli-composable-tool-design/), [clig.dev](https://clig.dev/), and the Anthropic tool guide.

- **Output.** Concise, human-readable text by default for TTYs. `--json`, plus `KB_OUTPUT=json` when stdout is not a TTY, gives a stable, versioned shape. Use `--jsonl` for streams. Data goes to stdout, and diagnostics and progress go to stderr.
- **Concise by default.** Search returns a title, path or ID, a score, and a snippet of about 200 characters. `kb read` returns the whole document only up to a cap and offers `--range`, `--section` and `--max-tokens`. When output is truncated, print a hint such as `"truncated: use --offset 4000 or --section 'Setup'"`.
- **Pagination.** Use `--limit` (default 10) and `--cursor`. JSON output includes `next_cursor` and `total`.
- **Exit codes.** 0 for success, 1 for a generic error, 2 for usage error, 3 for not found, 4 for auth or config, 5 for conflict (duplicate or version mismatch), 6 for server unreachable. Errors in JSON mode are `{"error":{"code":"not_found","message":"…","hint":"…"}}`.
- **Non-interactive.** Never prompt. Writes take `--dry-run` and are idempotent through `--if-absent` or a content hash. Body input comes from stdin (`-`) or `--file`.
- **`--help`.** Every subcommand gets examples, its exit codes, and its output schema. `kb help --agent` (or `kb schema`) prints a compact machine-readable command catalog.
- **Config.** Use `KB_URL`, `KB_TOKEN` (or `KB_TOKEN_FILE`), and `KB_COLLECTION`, with optional `~/.config/kb/config.toml`. Fail loudly if something is missing. Avoid interactive login.
- **Distribution.** For Python, `uv tool install kb-cli` (or `pipx`) gives an isolated, PATH-installed command. The CLI should be a **thin HTTP client** to the server on the Mac mini so it starts quickly and works from any machine. For a zero-runtime binary, use a Go or Bun-compiled client. Its startup time is better, but it means a second language. A Python CLI that only depends on `httpx` and `typer` starts in roughly 150–300 ms, which is acceptable.

## 4. Verdict and concrete design

The work lives in a **shared core**: a Python service layer for hybrid search, documents and ingest, exposed through FastAPI REST. Both front-ends are thin:
- An **MCP server**: FastMCP 4 or `mcp` 2.x, mounted at `/mcp` in the same ASGI app, using Streamable HTTP and a bearer token.
- A **CLI**: `kb`, which calls the REST API.

Tool names and semantics match one-to-one, and both front-ends share the same Pydantic models, so `outputSchema` equals the `--json` shape.

**Tool / command list:**

| MCP tool | CLI | Params | Output (concise) |
|---|---|---|---|
| `kb_search` | `kb search "q"` | `query`, `limit=10`, `cursor?`, `collection?`, `tags?[]`, `mode=hybrid\|keyword\|semantic`, `response_format=concise\|detailed` | `{results:[{id,title,path,snippet,score,tags,updated_at}], next_cursor, total}` |
| `kb_read` | `kb read <id\|path>` | `id`, `section?`, `offset=0`, `max_chars=20000` | `{id,title,path,content,format,truncated,next_offset,sections:[…],metadata}` |
| `kb_list` | `kb ls` | `collection?`, `tag?`, `since?`, `limit`, `cursor` | `{items:[{id,title,path,updated_at}], next_cursor}` |
| `kb_add` | `kb add -` / `--file` / `--url` | `title`, `content` or `url`, `collection`, `tags[]`, `source?`, `if_absent=true`, `dry_run=false` | `{id,path,status:created\|duplicate\|updated,chunks}` |
| `kb_update` | `kb edit <id>` | `id`, `content` or `append`, `tags?`, `expected_version` | `{id,version,status}` |
| `kb_related` (optional) | `kb related <id>` | `id`, `limit=5` | same shape as search results |

Leave delete out of MCP, or put it behind a confirmation step, and keep it CLI-only for humans. Also offer `kb://doc/{id}` as an MCP resource template for clients that support resources.

**Example `SKILL.md`** (`~/.agents/skills/knowledge-base/SKILL.md`, symlinked into `~/.claude/skills/`):

```markdown
---
name: knowledge-base
description: Search, read, and add to the personal knowledge base (notes, docs, design decisions, snippets) hosted on the Mac mini. Use when the user asks "what do we know about X", references past notes/decisions, wants context before implementing something, or asks to save/remember/document a finding.
compatibility: Requires the `kb` CLI on PATH (uv tool install kb-cli) and KB_URL/KB_TOKEN env vars; network access to the KB host.
metadata:
  version: "1.0"
---
# Knowledge base

Use the `kb` CLI. Always pass `--json` and parse with jq when chaining.

## Search first
kb search "postgres vacuum tuning" --limit 5 --json
- Results are concise (title, path, snippet). Refine the query or add `--tag`/`--collection` rather than raising --limit above 20.
- `--mode keyword` for exact identifiers/error strings; default `hybrid` otherwise.

## Read
kb read <id> --json            # capped at 20k chars
kb read <id> --section "Setup" # prefer sections for long docs
If `truncated` is true, continue with `--offset <next_offset>`.

## Add knowledge (only when the user asks to save, or a durable finding emerges)
echo "$BODY" | kb add - --title "..." --collection notes --tag topic --dry-run --json
Check the dry run, then rerun without --dry-run. Write Markdown with a one-line summary first. `status: duplicate` means it already exists; use `kb edit` to append instead.

## Errors
Exit 3 = not found, 4 = auth/config (check KB_TOKEN), 6 = server unreachable (tell the user; do not retry in a loop).
See references/schema.md for full JSON shapes.
```

## RECOMMENDATION

1. **Build the shared core first.** It is a Python service layer for search, read and ingest behind FastAPI on the Mac mini. Both interfaces are thin adapters over it, using one set of Pydantic models.
2. **Primary interface: the `kb` CLI plus the `knowledge-base` Agent Skill.**
   - All the target agents (Claude Code, Codex CLI, Cursor, Gemini CLI) have a shell and read `.agents/skills` or `.claude/skills`.
   - It costs about 100 idle tokens, it composes with jq and scripts, and it is trivially debuggable.
   - Ship it with `uv tool install` as a thin HTTP client using `KB_URL` and `KB_TOKEN`, JSON output, cursor pagination, documented exit codes and `--dry-run` writes.
3. **Also ship an MCP server from day one.** It is cheap because it shares the core: mount FastMCP 4.x (or `mcp` 2.x `MCPServer`) at `/mcp` with Streamable HTTP on the 2026-07-28 spec, which still serves 2025-11-25 clients. Expose the same 5–6 `kb_*` tools with `outputSchema`, `response_format`, pagination and actionable errors. Use bearer-token auth (a custom verifier against a hashed token) over Tailscale or the LAN. It covers agents and modes with no shell, as well as clients that prefer typed tools. Keep the tool count small so tool-search or bloat concerns never arise.
4. **Defer** full OAuth 2.1 (Client ID Metadata Documents) until the KB must be reached by hosted or remote agents such as claude.ai connectors or cloud Codex. When that happens, put it behind Tailscale Funnel or Cloudflare Tunnel with a proper authorization server rather than a static token.
5. **Evaluate both paths** with 10–20 realistic tasks (find a past decision, save a finding, avoid duplicates), and track the tokens and tool calls used per task, as Anthropic's tool-writing guide recommends.
