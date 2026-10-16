---
name: knowledge-base
description: Search, read and add to the user's self-hosted knowledge base (notes, docs, design decisions, runbooks, research, snippets) with the `kb` CLI. Use when the user asks "what do we know about X", refers to past notes, decisions or documents, wants background before implementing or debugging something, or asks to save, remember, document or update a finding in their notes or knowledge base.
compatibility: Requires the `kb` CLI on PATH (uv tool install ./backend) and network access to the KB server (KB_URL, default http://127.0.0.1:8787; KB_TOKEN for writes when the server sets one).
metadata:
  version: "1.0"
---

# Knowledge base (`kb`)

Pass `--json` when you parse output (stable shapes; see references/cli.md). Text output is compact and fine for reading.

## 1. Search first
```bash
kb search "postgres autovacuum tuning" -n 5
kb search "ECONNRESET upstream" --mode lexical      # exact identifiers / error strings
kb search "deploy steps" -c runbooks -t prod --json | jq -r '.hits[].document_id'
```
- Each hit shows title › heading path, score, `doc=<id>`, and a snippet.
- If nothing relevant comes back, reword the query (synonyms, fewer words) or drop filters. Do not raise `-n` above about 20.
- `kb ask "question"` runs the server's research agent and returns an answer with [n] citations. It is slower, so use it only for broad questions you would otherwise answer with many searches.

## 2. Read, then cite
```bash
kb get <id>            # metadata header + full Markdown
kb get <id> --json     # {id,title,collection,tags,source_uri,status,body,...}
```
When you answer from the KB, cite the document title, and its id or source_uri. Say plainly when the KB has nothing on the topic.

## 3. Save knowledge (only when asked, or for a durable finding the user wants kept)
```bash
kb add - --title "Autovacuum tuning for big tables" -c notes -t postgres -t performance \
  --source-uri note:postgres-autovacuum-tuning --wait <<'EOF'
Summary: one line stating the finding.

Details in Markdown...
EOF
kb add ./docs/design.pdf -c docs -t architecture   # files upload (PDF/Office/HTML/MD)
```
- **Always give a `--source-uri`** for notes, such as `note:<kebab-slug>`, the source URL, or `repo:<name>/<path>`. Re-adding with the same `source_uri` updates that document instead of creating a duplicate. Identical content returns `duplicate: true`.
- Start the body with a one-line summary, then Markdown with headings. One topic per document.
- Change an existing document with `kb update <id> --title ... -t tag ... --content-file file.md`. Note that `--content-file` replaces the whole body, so `kb get` it first.
- Delete only when the user explicitly asks: `kb rm <id> --yes`.

## Conventions
- Collections are broad areas. Find them with `kb collections`, for example `notes`, `docs`, `runbooks`, `research`. Create one only if the user agrees: `kb collections create SLUG "Name"`.
- Tags are lowercase kebab-case topics (`postgres`, `incident`, `adr`), usually 1–4 per document. Reuse existing tags (`kb ls -t <tag>`) rather than inventing near-duplicates.

## Errors (exit codes)
0 ok · 1 error · 2 invalid usage/input · 3 not found (check the id with `kb search` / `kb ls`) · 4 server unreachable (tell the user, do not retry in a loop). With a 401 error, KB_TOKEN is missing or wrong.

Full command reference and JSON shapes: [references/cli.md](references/cli.md).
