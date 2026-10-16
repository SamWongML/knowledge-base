# 05 — Frontend stack and design direction (as of 2026-10-16)

Scope: the web UI for a self-hosted knowledge base. It is served from a Mac mini to one user or a small team, and it talks to a Python JSON API that streams over SSE. The user asked for a UI that is "modern, elegant and niche" and built on the newest stack. I took every version below from the npm registry (`registry.npmjs.org/<pkg>`) on 2026-10-16. The date after each version is when it was published.

## 1. Framework

**Recommendation: a static SPA built with Vite 8 and React 19.3, routed by TanStack Router (file-based, with code splitting) and fed by TanStack Query.** The Python backend serves `dist/` as static files and handles `/api/*`. No Node runtime runs in production.

Why this one:
- **No SSR or SEO need.** The app is a private tool on a LAN, so server rendering buys nothing. A Node server process would only add another thing to deploy on the Mac mini.
- **Vite 8 (8.3.4, 2026-10-08)** shipped on 2026-03-12. It replaces the esbuild+Rollup pair with Rolldown (Rust) and Oxc, and claims builds 10–30x faster. It needs Node 20.19+ or 22.12+. `@vitejs/plugin-react` v6 (6.1.2) uses Oxc for React Refresh and no longer depends on Babel. https://vite.dev/blog/announcing-vite8 , https://infoq.com/news/2026/05/vite-v8-rust/
- **React 19.3.1 (2026-10-14).** It brings `use()`, Actions, `useOptimistic`, `<Activity>` and View Transitions. The last two pair well with route transitions in a reader-style UI.
- **React Compiler.** `babel-plugin-react-compiler` has been stable at 1.0.0 since 2025-10. In Vite 8 the faster route is the Oxc-native compiler, which is still marked **experimental**. You enable it with `react({ compiler: true })` and the `oxc-transform-react` peer package (0.154.0), and it needs plugin-react 6.1.0 or later. One user reports builds 17x faster than with Babel. Enable it from day one; the Babel route via `@rolldown/plugin-babel` and `reactCompilerPreset` is the fallback. With the compiler on, most `useMemo`/`useCallback` calls can go. https://oxc.rs/blog/2026-08-18-react-compiler-support , https://blog.logrocket.com/rust-react-compiler-vs-babel/ , https://voidzero.dev/posts/whats-new-aug-2026
- **TanStack Router (1.170.44)** gives type-safe search params, which matter for search URLs like `?q=…&tags=…&mode=hybrid`. It also offers route loaders that call `queryClient.ensureQueryData`, and preload on hover/intent. **TanStack Query (5.104.3)** handles caching, ingestion polling and optimistic tag edits. https://tanstack.com/router , https://tanstack.com/query

Alternatives considered:
| Option | Version | Verdict |
|---|---|---|
| TanStack Start | 1.169.2 | Same router, plus SSR and server functions. Its SPA mode works, but server functions overlap with the Python API. Only pick it if you later want a Node BFF. |
| Next.js 16 | 16.4.2 | Overkill. RSC and the Node server are pointless here, and `output: 'export'` turns off most of what makes Next attractive. |
| SvelteKit 3 / Svelte 5 | kit 3.1.0 (3.0.0 on 2026-10-01), svelte 5.57.2 | A real contender: runes, small bundles and elegant code, and `adapter-static` builds an SPA. But 3.0 is two weeks old (`#lib` alias, config moved into `vite.config.ts`, remote functions still experimental), and the shadcn/Base UI/Streamdown/cmdk ecosystem is React-first. https://infoq.com/news/2026/09/sveltekit-3-vite , https://svelte.dev/blog/sveltekit-3-release-candidate |
| SolidStart | 2.0.6 | Great performance, but the component ecosystem is thin. It is niche for the wrong reason. |

## 2. Styling and UI primitives

- **Tailwind CSS 4.3.3** (`@tailwindcss/vite` 4.3.3). It uses CSS-first config (`@import "tailwindcss"; @theme {…}`), OKLCH colours by default, and container queries. v4.3 adds scrollbar utilities and new colours. Use `@custom-variant dark (&:where(.dark, .dark *))` for class-based dark mode. https://releases.sh/tailwind-css/releases
- **shadcn/ui CLI 4.21.5.** Since **July 2026, Base UI is shadcn's default primitive layer.** Radix remains supported (`shadcn init -b radix`), and the mechanical change is `asChild` → `render`. https://ui.shadcn.com/docs/changelog/2026-07-base-ui-default . Use the shadcn create flow (https://ui.shadcn.com/docs/changelog/2025-12-shadcn-create) to pick a style, font, radius and base colour, then hand-edit the tokens. Components are copied into your repo, so they can be fully restyled, which matters for a "niche" look.
- **Base UI vs Radix.** Pick **`@base-ui/react` 1.9.0** (note the new package name; `@base-ui-components/react` is stuck at an old RC). Base UI is the actively developed successor from the Radix/MUI/Floating UI people. It ships Combobox/Autocomplete, which Radix lacks, it is the shadcn default, and its `render` prop is cleaner. Keep `radix-ui` 1.8.1 only if a specific shadcn block still needs it.
- **Motion 14.3.0** (`motion`, the successor to framer-motion; import from `motion/react`). v14 (2026-10-02) only removed internal compat APIs, and v13 made `@emotion/is-prop-valid` opt-in. https://motion.dev/changelog?type=major . Use it sparingly for layout animation of the answer panel, step-trace expand/collapse and toasts. Use CSS View Transitions for route changes.
- **Command palette.** `cmdk` 1.1.1 is stable but has not changed since 2025-03; shadcn's `<Command>` still wraps it. The alternative is building the palette on Base UI `Combobox`/`Autocomplete` inside a `Dialog`. Recommendation: use shadcn Command (cmdk) and set `shouldFilter={false}`, because the server does the ranking.
- **Icons:** `lucide-react` 1.58.0. For a more editorial feel, use `strokeWidth={1.5}` at 16px.
- Other small parts: `sonner` 2.0.8 (toasts), `react-resizable-panels` 4.14.5 (split reader/answer layout), `tw-animate-css` 1.4.0 (shadcn animations).

**Fonts (all free and self-hostable via @fontsource 5.3.x, so no Google CDN call from a LAN app):**
- Display/serif: **Instrument Serif** (`@fontsource/instrument-serif`). Its high contrast reads as "editorial". Alternatives: **Newsreader** (`@fontsource-variable/newsreader`, has an optical-size axis, suits long-form reading) and **Fraunces** (variable, with "soft" and "wonk" axes).
- UI sans: **Geist** (`geist` 1.7.2 or `@fontsource-variable/geist`), **Inter** (`@fontsource-variable/inter` 5.3.0, use the `opsz` axis for display sizes), or **Instrument Sans** to pair with Instrument Serif.
- Mono, closest free match to Berkeley Mono: **Commit Mono** (`@fontsource/commit-mono`), **Geist Mono**, **JetBrains Mono**, **IBM Plex Mono**.

## 3. Features that make it feel modern

1. **Cmd-K everywhere.** One palette for search, navigation, actions ("ingest URL…", "toggle theme") and recent queries. Use `@tanstack/react-hotkeys` 0.14.3 (pre-1.0) or the mature `react-hotkeys-hook` 5.3.3. Add `?` for a shortcuts sheet, `j/k` to move through results, `/` to focus search, `Esc` to go back, and `g d` for chords. Wire search input through `@tanstack/react-pacer` 0.24.1 to debounce it.
2. **Streaming agentic answer.** Consume the Python SSE stream with `fetch()` plus `eventsource-parser` 4.1.2. Avoid native `EventSource`, which cannot POST a body or set auth headers, and avoid `@microsoft/fetch-event-source`, which has been dead since 2021. Define typed events such as `step.start | step.delta | step.end | retrieval | token | citation | done | error` and reduce them into a single `useReducer` state. Render:
   - **A live step trace:** a vertical timeline (planning → search "x" → read doc 3 → synthesize) with spinner, duration and collapsible tool I/O, animated with Motion `layout`.
   - **The answer:** use **Streamdown 2.7.0** (Vercel) in place of react-markdown. It is a drop-in replacement built for incomplete streaming markdown: it closes unterminated bold, code fences and links via `remend` 1.4.0, renders safely, and has a streaming caret. Add plugins as needed: `@streamdown/code` 2.0.0 (Shiki), `@streamdown/math` 1.0.3, `@streamdown/mermaid` 1.0.3. https://skills.sh/vercel/streamdown/streamdown , https://context7.com/vercel/streamdown
   - **Citations:** inline `[1]` markers become hover cards (Base UI `PreviewCard`) showing the snippet and source. Clicking one opens the reader scrolled to that chunk with the passage highlighted.
   - Optional alternative: if the backend emits the AI SDK "UI message stream" protocol, `ai` 7.0.142 / `@ai-sdk/react` 4.0.146 `useChat` gives typed message parts for free. It is heavier and ties you to Vercel's protocol, so I prefer the custom reducer.
3. **Result snippets.** Have the backend return highlight offsets or `<mark>` fragments, rendered as `font-serif` excerpts with a subtle highlight colour. Show score, source type and date as small mono metadata.
4. **Document reader.** Use Streamdown or react-markdown 10.1.0 + remark-gfm 4.0.1 for static docs (react-markdown is fine when nothing streams). Add a sticky table of contents, reading width of about 68ch, scroll-spy, and highlight-on-arrival from a citation. For code, use `shiki` 4.5.0.
5. **Drag-and-drop upload with ingestion progress.** Use `react-dropzone` 20.1.3 with a full-window drop overlay. Upload with XHR or `fetch` for progress, then subscribe to `/api/ingest/{job}/events` over SSE so each file shows stages (parsing → chunking → embedding → indexed). Fall back to TanStack Query polling.
6. **Virtualization.** `@tanstack/react-virtual` 3.14.20 for the library list, long result lists and very long documents.
7. **Polish.** Optimistic updates, skeletons that match the final layout, `prefers-reduced-motion`, focus rings in the accent colour, and `vite-plugin-pwa` 2.0.0 so it installs as a desktop app on the team's Macs.

## 4. Tooling

- **TypeScript 7.0.2.** Released 2026-07/08, it is the native Go compiler ("tsgo"), 8–12x faster. `tsc` is now the native binary, and `strict` and `esnext` are the defaults. **Caveat:** there is no stable programmatic API until 7.1, so typescript-eslint and some framework tooling cannot use it yet. That argues for a non-typescript-eslint linter. https://infoq.com/news/2026/08/typescript-7-released/ , https://visualstudiomagazine.com/articles/2026/06/22/typescript-7-0-rc-moves-microsofts-go-rewrite-into-the-mainline-compiler.aspx . Vite strips types without checking them, so type-checking runs as a separate `tsc --noEmit` step.
- **Lint/format: Biome 2.5.15.** One Rust binary for lint and format, with type-aware rules that do not depend on the TS API, so it works alongside TS 7. ESLint 10.12 + Prettier 3.9 is mature but slower, and typed linting is blocked on TS 7.1. Oxlint 1.88 is another option and now has React-Compiler-powered rules; if Vite+ (`vp`) matures, the Oxlint+Oxfmt route converges with it. For one dev, Biome is simplest. Add `knip` 6.41.0 for dead code.
- **Tests:** **Vitest 5.0.5** (latest; v5 left RC in October 2026, and 4.1.12 is the conservative pin). Use browser mode or jsdom, `@testing-library/react` 16.3.3, and **MSW 3.0.3** to mock the API and SSE streams. Use **Playwright 1.64.1** for end-to-end tests: ingest a file, ask a question, see citations.
- **Package manager: pnpm 12.11.2.** It has strict node_modules, a supply-chain `minimumReleaseAge` setting, and is the most widely supported. Bun 1.4.3 is faster but adds runtime-compat risk for no gain in a Vite SPA. Use whichever Node the Mac mini runs (22 or 24 LTS).
- **OpenAPI types.** FastAPI (and similar) emit `openapi.json`, from which you generate client types:
  - **Recommended:** `openapi-typescript` 7.13.0 + `openapi-fetch` 0.17.0 + `openapi-react-query` 0.5.4. It generates types only, has a roughly 6 kB runtime, and is typed by path (`client.GET("/api/docs/{id}")`). It is low-churn.
  - `orval` 8.43.0 generates TanStack Query hooks plus MSW mocks. Good if you want generated hooks and fixtures.
  - `@hey-api/openapi-ts` 0.99.0 is powerful with a plugin system, but still pre-1.0.
  - In every case SSE endpoints sit outside OpenAPI, so hand-write a typed `streamAsk()` with a discriminated-union event type. Mirror the Python Pydantic event models in a shared JSON Schema if you want them generated.

## 5. Aesthetic directions

**A. "Linear/Raycast precision."** Near-black, layered surfaces, 1px hairline borders, Inter/Geist at 13px, one saturated accent, glassy palette. Feels very slick but is the most common look of 2024–26, so it is not niche.

**B. "Editorial reading room" (Are.na × Craig Mod × a well-set journal).** Warm paper background, ink text, a serif display face for headings and answers, sans for chrome, mono for metadata. One restrained accent (vermilion or oxblood) used for citations and highlights. Generous whitespace, rules instead of boxes, small caps for labels, numbered marginalia citations. Feels like a private library, which suits a knowledge base.

**C. "Terminal brutalist."** Mono everywhere, a hard 1-bit-ish palette with phosphor green or amber, visible grid, ASCII step traces. Charming, but tiring for long reading and harder to make elegant.

**Recommendation: B (Editorial), with A's interaction grammar.** That means Cmd-K, keyboard-first navigation and crisp motion, while the visual identity stays editorial. It is distinctive without hurting readability. Fonts: **Instrument Serif** (display, answer headings) + **Newsreader** (long-form body in the reader and answers) + **Inter Variable** or **Geist** (UI) + **Commit Mono** (metadata, step trace, scores).

Token palette (OKLCH, written as shadcn-compatible CSS variables in `@theme inline`):

```css
:root {
  --background: oklch(0.975 0.008 85);   /* warm paper */
  --surface:    oklch(0.955 0.010 85);   /* cards, sidebar */
  --foreground: oklch(0.22 0.012 60);    /* ink */
  --muted-foreground: oklch(0.50 0.012 60);
  --border:     oklch(0.88 0.010 80);    /* hairline rules */
  --accent:     oklch(0.58 0.17 35);     /* vermilion: citations, focus */
  --accent-foreground: oklch(0.98 0.01 85);
  --highlight:  oklch(0.92 0.09 95);     /* marker-yellow snippet <mark> */
  --success:    oklch(0.60 0.11 150);
  --destructive: oklch(0.55 0.19 25);
  --radius: 0.375rem;
  --font-display: "Instrument Serif", serif;
  --font-serif: "Newsreader Variable", Georgia, serif;
  --font-sans: "Inter Variable", system-ui, sans-serif;
  --font-mono: "Commit Mono", ui-monospace, monospace;
}
.dark {
  --background: oklch(0.17 0.008 60);    /* lamp-lit ink, not pure black */
  --surface:    oklch(0.21 0.009 60);
  --foreground: oklch(0.92 0.012 85);
  --muted-foreground: oklch(0.66 0.012 80);
  --border:     oklch(0.30 0.008 60);
  --accent:     oklch(0.70 0.15 40);
  --highlight:  oklch(0.40 0.07 90);
}
```

Type scale: display 48/40/32 in Instrument Serif with tight tracking (-0.01em). Reading body is Newsreader at 18/1.6 with 68ch measure. UI is 14px Inter. Metadata is Commit Mono at 12px, uppercase, +0.04em tracking.

## RECOMMENDED STACK

Runtime deps:
```
react@19.3.1  react-dom@19.3.1
@tanstack/react-router@1.170.44  @tanstack/react-query@5.104.3
@tanstack/react-virtual@3.14.20  @tanstack/react-pacer@0.24.1
@base-ui/react@1.9.0  (shadcn CLI shadcn@4.21.5, base = Base UI)
cmdk@1.1.1  lucide-react@1.58.0  motion@14.3.0  sonner@2.0.8
react-resizable-panels@4.14.5  react-dropzone@20.1.3
react-hotkeys-hook@5.3.3   (or @tanstack/react-hotkeys@0.14.3)
streamdown@2.7.0  @streamdown/code@2.0.0  @streamdown/math@1.0.3
eventsource-parser@4.1.2
openapi-fetch@0.17.0  openapi-react-query@0.5.4
@fontsource/instrument-serif@5.3.0  @fontsource-variable/newsreader@5.3.0
@fontsource-variable/inter@5.3.0  @fontsource/commit-mono@5.3.0
```
Dev deps:
```
vite@8.3.4  @vitejs/plugin-react@6.1.2  oxc-transform-react@0.154.0 (React Compiler, experimental)
@tanstack/router-plugin@1.168.46  @tanstack/devtools-vite@0.8.5
tailwindcss@4.3.3  @tailwindcss/vite@4.3.3  tw-animate-css@1.4.0
typescript@7.0.2  @types/react@19.3.0
@biomejs/biome@2.5.15  knip@6.41.0
vitest@5.0.5  @testing-library/react@16.3.3  msw@3.0.3  @playwright/test@1.64.1
openapi-typescript@7.13.0  vite-plugin-pwa@2.0.0
pnpm@12.11.2 (packageManager field), Node 22/24 LTS
```

Proposed file structure:
```
web/
  package.json  pnpm-lock.yaml  biome.json  tsconfig.json  vite.config.ts
  components.json            # shadcn config (base: base-ui, style, aliases)
  index.html
  public/                    # favicon, PWA icons
  src/
    main.tsx                 # QueryClient + RouterProvider + ThemeProvider
    routeTree.gen.ts         # generated by router-plugin
    styles/
      globals.css            # @import tailwindcss; @theme tokens; fonts; .dark
      prose.css              # reader/answer typography
    routes/                  # TanStack file routes
      __root.tsx             # shell: sidebar, Cmd-K, toaster, hotkeys
      index.tsx              # home: ask/search box + recent
      search.tsx             # ?q=&mode=&tags= validated search params
      ask.$threadId.tsx      # streaming answer + step trace + citations
      library/index.tsx      # virtualized doc list, filters, upload dropzone
      library/$docId.tsx     # document reader (?chunk= highlight)
      ingest.tsx             # ingestion jobs + progress
      settings.tsx
    api/
      schema.d.ts            # generated: openapi-typescript http://localhost:8000/openapi.json
      client.ts              # openapi-fetch client + openapi-react-query $api
      queries.ts             # queryOptions factories (docs, search, jobs)
      sse.ts                 # fetch + eventsource-parser async iterator
      events.ts              # typed AskEvent / IngestEvent unions
    features/
      ask/        useAskStream.ts  AnswerView.tsx  StepTrace.tsx  CitationCard.tsx
      search/     ResultList.tsx  Snippet.tsx
      reader/     DocReader.tsx  Toc.tsx  useChunkHighlight.ts
      ingest/     DropOverlay.tsx  JobProgress.tsx  useIngestEvents.ts
      command/    CommandPalette.tsx  commands.ts
    components/
      ui/                    # shadcn-generated (Base UI) components, restyled
      layout/                # AppShell, Sidebar, Kbd, ThemeToggle
      markdown/              # Streamdown wrapper + component overrides
    hooks/       useHotkeys.ts  useTheme.ts
    lib/         utils.ts (cn)  format.ts
  tests/
    unit/  (vitest + msw handlers incl. SSE)
    e2e/   (playwright: ingest -> ask -> open citation)
```
Build output `web/dist/` is served by the Python app (e.g. FastAPI `StaticFiles` with SPA fallback to `index.html`). In development, Vite's `server.proxy` forwards `/api` to the Python backend at `:8000` (set `Cache-Control: no-cache` and no buffering for SSE).

Caveats: the Oxc React Compiler is experimental, so fall back to the Babel preset if it misbehaves. TS 7 has no API until 7.1, so if a tool breaks, pin `@typescript/typescript6`. Vitest 5 and SvelteKit 3 are only weeks old. cmdk and openapi-typescript are mature but slow-moving.
