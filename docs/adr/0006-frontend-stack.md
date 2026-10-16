# 0006. Frontend: Vite 8 + React 19 + TanStack + Tailwind 4 + Base UI + Streamdown, editorial design

- Status: accepted
- Date: 2026-10-16
- Related: [research §5](../research/README.md#5-frontend), [raw/05](../research/raw/05-frontend.md), `frontend/`, `KB_STATIC_DIR`

## Context

The UI is a private tool for one user or a small team. It needs no SSR and no SEO. It must stream agentic answers over POST SSE, show a live step trace and citations, and handle drag-and-drop ingestion with live status. The brief asked for something "modern, elegant and niche" on the newest stable stack. The Python API already serves JSON and SSE, and a Node server in production would be one more process to run.

## Decision

- **Build:** Vite 8 (Rolldown + Oxc), `@vitejs/plugin-react` 6, React 19, TypeScript, and pnpm. The output is a static SPA in `frontend/dist`, served by the API (`KB_STATIC_DIR`) or by Caddy, with an SPA fallback to `index.html`.
- **Routing and data:** TanStack Router (file routes, type-safe search params such as `?q=&mode=&tags=`) and TanStack Query.
- **UI:** Tailwind CSS 4 (CSS-first `@theme`, OKLCH tokens), Base UI primitives (`@base-ui/react`, the shadcn default since July 2026), cmdk for the Cmd-K palette, Motion used sparingly, and lucide icons.
- **Streaming:** `fetch` + `eventsource-parser`, reduced into typed state. Native `EventSource` cannot POST a body or set headers. **Streamdown** renders partial Markdown safely while it streams. Citations `[n]` become hover cards that open the reader at the cited chunk.
- **Design:** the **"editorial reading room"** direction.
  - Warm paper and ink surfaces, hairline rules instead of boxes.
  - Instrument Serif for display, Newsreader for reading, Inter for UI, and Commit Mono for metadata, all self-hosted.
  - A single vermilion accent for citations and focus.
  - Keyboard-first interaction (Cmd-K, `/`, `j/k`).
  - Dark mode is "lamp-lit ink".
- **Quality:** Biome for lint and format, Vitest + Testing Library + MSW (including SSE mocks), and Playwright for end-to-end tests.

## Alternatives considered

| Option | Why not |
|---|---|
| Next.js 16 | RSC and a Node server add nothing for a private SPA. Static export disables most of its value. |
| TanStack Start | The same router, but its server functions overlap with the Python API. |
| SvelteKit 3 / Svelte 5 | Elegant, but 3.0 was two weeks old, and Streamdown and Base UI are React-first. |
| SolidStart | Thin component ecosystem. |
| react-markdown for answers | Breaks on unterminated Markdown mid-stream. Still fine for static documents. |
| Linear-style dark UI / terminal brutalism | Linear-style is the generic 2024–26 look; brutalism tires the eye during long reading. |

## Consequences

- Production needs no Node runtime: one static folder.
- The SSE event types are not in OpenAPI, so they are hand-typed and must track `AgentStep` / `AskResponse`.
- Some pieces are young: the Oxc React Compiler is experimental, TypeScript 7 has no API until 7.1, and Vitest 5 is new. Pin versions and keep fallbacks (the Babel compiler preset, `@typescript/typescript6`).
- An editorial look takes typographic care: about 68ch measure, an optical-size axis, and contrast checks in both themes.
