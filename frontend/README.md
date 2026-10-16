# Commonplace — web UI

Single-page app for the knowledge base: search, agentic Q&A with citations, library management,
a document reader and a system page. Built as static files into `dist/`, which the FastAPI app
serves (assets under `/assets`, SPA fallback to `index.html`).

## Commands

```sh
pnpm install
pnpm dev         # http://localhost:5173, proxies /api and /mcp to http://127.0.0.1:8787
pnpm build       # typecheck + production build to dist/
pnpm preview     # serve dist/ with the same proxy
pnpm test --run  # Vitest + Testing Library + MSW
pnpm lint        # Biome (lint + format check); `pnpm format` fixes
pnpm typecheck   # tsc (TypeScript 7, native)
```

Set `KB_BACKEND_URL` to proxy somewhere other than `127.0.0.1:8787`.

## Stack

Vite 8 (Rolldown) · React 19.3 with the Oxc React Compiler · TanStack Router (code-based routes,
typed search params) + TanStack Query · Tailwind CSS 4 with OKLCH tokens · Base UI primitives ·
cmdk · Streamdown · Motion (lazy-loaded features) · react-dropzone · TanStack Virtual ·
eventsource-parser · sonner · lucide · self-hosted Instrument Serif / Newsreader / Inter / Commit Mono.

## Layout

```
src/
  lib/api/        types.ts (mirror of backend/src/kb/schemas.py), client.ts (fetch + bearer),
                  sse.ts (POST-capable SSE reader), endpoints.ts, queries.ts, live.ts (/api/events)
  lib/            settings (localStorage), snippet sanitiser, citation parser, formatting
  components/     ui primitives, layout (masthead, shortcuts), command palette, markdown
  features/       search · ask · library · reader · system
  styles/         globals.css (tokens, light/dark), prose.css (reading typography)
  test/           MSW server, fixtures, render helpers
```

## Notes

- `types.ts` is hand-written; keep it in sync with `schemas.py` when the contract changes.
- Search snippets are never injected as HTML: only `<mark>` toggles highlighting, everything else
  renders as text.
- Answer citations `[n]` become hover cards (Base UI PreviewCard) linking to
  `/documents/<id>#chunk-<chunk_id>`; the reader scrolls to and highlights that passage.
- The API token and preferences (engine, search mode, theme) live in `localStorage` (`kb.settings`).
- Keyboard: `⌘K` palette, `/` focus search, `j`/`k`/`↵` results, `i` inspector, `g s|a|l|y`
  navigation, `n` new note, `t` theme, `?` cheat sheet.
