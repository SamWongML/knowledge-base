# knowledge-base agent skill

This folder is an [Agent Skill](https://agentskills.io): `SKILL.md` plus `references/`. It teaches coding agents to use the `kb` CLI to search, read and save to the knowledge base. Claude Code, Codex CLI, Cursor and Gemini CLI all load it from their skills directories. The skill costs about 100 tokens of context until it is used.

## 1. Install the CLI

The CLI is a thin HTTP client, so install it on every machine that runs agents, not only on the server.

```bash
uv tool install ./backend                       # from a checkout of this repo
# or, without a checkout:
uv tool install --from "git+https://github.com/<you>/knowledge-base#subdirectory=backend" kb
kb --help
```

Point it at the server and set the token if the server has one:

```bash
export KB_URL=http://mac-mini.tailnet:8787   # default: http://127.0.0.1:8787
export KB_TOKEN=...                          # = KB_API_TOKEN on the server
kb status
```

## 2. Install the skill

Symlink the folder, so `git pull` keeps it current:

```bash
mkdir -p ~/.agents/skills ~/.claude/skills
ln -s "$PWD/skills/knowledge-base" ~/.agents/skills/knowledge-base   # Codex, Cursor, Gemini CLI
ln -s "$PWD/skills/knowledge-base" ~/.claude/skills/knowledge-base   # Claude Code
```

For a single project, link it into `<project>/.agents/skills/` and `<project>/.claude/skills/` instead.

## 3. Alternative: MCP

Agents without a shell, or setups that prefer typed tools, can use the server's MCP endpoint at `http://<host>:8787/mcp`. It uses Streamable HTTP and needs a bearer token when `KB_API_TOKEN` is set.

```bash
claude mcp add --transport http kb http://127.0.0.1:8787/mcp --header "Authorization: Bearer $KB_TOKEN"
kb mcp-config            # prints snippets for Claude Code, Codex (~/.codex/config.toml) and Cursor
```

The MCP server exposes eight tools: `kb_search`, `kb_ask`, `kb_read`, `kb_list`, `kb_add`, `kb_update`, `kb_delete` and `kb_collections`. Documents are also available as resources at `kb://documents/{id}`.

You can use the skill and MCP together. The skill's workflow guidance (search, then read, then cite, plus the `source_uri` conventions) applies to the MCP tools as well.
