# PegasusAI Studio — Frontend

Next.js 15 (App Router, React 19, TypeScript strict) web UI for PegasusAI Studio.

## Development

### Prerequisites

- Node.js 20+
- The backend API running on port 8080 (see `../studio-api/README.md`)

### Setup

```bash
npm ci
npm run dev
```

Open `http://localhost:3000`. `/api/*` and `/ws/*` are proxied to `$API_URL`
(default `http://localhost:8080`) via `next.config` rewrites.

### Type-check gate (run before every PR)

CI runs no frontend tests or lint, so `tsc` is the gate:

```bash
node_modules/.bin/tsc --noEmit    # strict mode
```

Do **not** use `npm run lint` (eslint isn't installed) or `npm test` (there are
no test files) — both fail.

## Routes

All app routes live under `app/(studio)/` and share the studio chrome (left nav
+ panels). The left-nav labels differ from the route paths in a couple of places
(noted below).

| Route | Rail label | Description |
|-------|-----------|-------------|
| `/` | Home | Welcome, four ways in, an opener gallery that hands a prompt to the assistant, an attention strip for failed/running runs, and the guided tour |
| `/workflows` | Analyses | Project list (resizable/collapsible), a workflow picker in the header, the collapsible "Set up your run" form, an always-visible graph pane with a run picker, Results + Steps tabs, failure cards |
| `/data` | Data | Drag-and-drop upload into the workspace, with copy-path and delete |
| `/marketplace` | Examples | Browse & "Add" (git-clone) workflows sourced live from GitHub (PegasusHub + curated Pegasus-ISI), with search, subject chips, source filter and sort |
| `/notebooks` | Notebooks | JupyterLab framed in Studio (served at `/jupyter/`), with "Open in new tab" and Reload; `?path=` opens a folder |
| `/provisioning` | Resources | A plain-language capacity verdict, with HTCondor pool + OSPool (OSG) panels under Details |
| `/settings` | Settings | Notifications, Pegasus options (incl. Simplified Run), with LLM providers and AI tools under Advanced |

The Advanced grouping follows the **Simplified Run** preference
(`GET/PUT /api/settings/ui`). Turning it off flattens the rail and opens the
Advanced disclosure — the individual Pegasus steps — by default. It no longer
*replaces* the run form: "Set up your run" owns the parameters in both modes,
because rendering them in both places gave one value two editors. See
`docs/UX-NON-SPECIALIST-DESIGN.md` and `docs/UI-REDESIGN-2026-09.md`.

**In-app links do not reload the page.** `components/layout/app-link.tsx`
pushes through the client router so the chat panel, terminals and scroll
positions survive a nav click. It stays an `<a>` with a real `href`, which is
what keeps middle-click and open-in-new-tab working. Next's `basePath` is
build-time only, so the per-user prefix is recovered at runtime from
`<base href>` by `appPushPath()` in `lib/urls.ts` — never hardcode an absolute
app path.

`app/chat/page.tsx` is a standalone, chrome-less chat page (not under the studio
layout). It is iframed into JupyterLab by the `jupyter-pegasusai-chat` extension
via an origin-checked `postMessage` handshake; the same chat also renders in the
studio's slide-in panel.

## Architecture

- **State**: Zustand for client/panel/chat state (with `persist` to
  localStorage), TanStack Query for server state.
- **Shell**: `components/layout/top-bar.tsx` (wordmark, ⌘K palette, account
  menu) above `sidebar.tsx`, a 72px icon rail with a short label under each
  glyph. `command-palette.tsx` searches the user's analyses, the catalog and
  the pages; its queries are `enabled` only while it is open, so the search
  box costs nothing on other pages.
- **Resizable panes**: `components/layout/resizable-pane.tsx` handles both the
  workflow list (left-anchored, collapsible) and the chat panel
  (right-anchored, not collapsible — it has a close button). Width persists per
  browser in localStorage, not in the settings DB: a laptop and an external
  monitor want different numbers, so following the user across devices would be
  wrong here.
- **Onboarding**: `components/onboarding/guided-tour.tsx` spotlights real
  elements by `data-tour` anchor, cutting the scrim into four panes so the
  highlighted control stays clickable. Steps whose anchor is not on screen are
  skipped, which is what lets one step list work across interface states. It
  does not navigate. Restartable from **Take a tour** in the account menu.
- **Styling**: Tailwind 3 with a custom cornflower Pegasus palette
  (`app/globals.css` semantic tokens + the `pegasus-*` scale in
  `tailwind.config.ts`); light/dark theme-aware. Filled actions live at
  `pegasus-600`+ and decoration at `pegasus-500`-, because 600 is the first
  shade that carries white text at AA.
- **Terminal**: xterm.js over a WebSocket to a backend PTY (`/ws/terminal…`).
- **Chat**: SSE streaming with tool-call/result display and a per-turn
  shell-command approval prompt.
- **DAG viewer**: `@xyflow/react` + dagre, live-polling while a run is active.
  Renders inline (a pane on the analyses page) or as a modal. Siblings that
  share a transformation and the same parent groups collapse into one node
  carrying the status breakdown — a 167-way scatter is one rank ~36,700px wide
  otherwise, which `fitView` cannot fit. "Show every task" turns it off.
- **Per-user prefix handling** (important): under KubeSpawner the app is served
  at `/user/<name>/…`. `middleware.ts` strips the prefix, `app/layout.tsx` sets
  `<base href>` from `x-forwarded-prefix`, and **all** URLs are built relative to
  `document.baseURI` via `lib/urls.ts`. Never hardcode absolute app paths
  (`fetch("/api…")`, `href="/…"`, absolute `new WebSocket`, `router.push("/…")`) —
  always go through `lib/urls.ts`.

## Build for production

```bash
npm run build    # standalone output in .next/standalone/ (~15 MB)
```

The container copies the standalone build and runs it on port 3000 behind nginx;
see `../studio-api/README.md` and the repo `Dockerfile`.
