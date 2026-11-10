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
(default `http://localhost:8080`) via `next.config` rewrites. The rewrites exist
only under `next dev`: a production build returns none, because in the image
nginx routes `/api` and `/ws` straight to studio-api and Next.js should not be a
second forwarder (#229).

**Start the backend with the dev origin allowlisted**, or every write 403s:

```bash
STUDIO_ALLOWED_ORIGINS=http://localhost:3000 uvicorn main:app --reload --port 8080
```

The rewrite forwards the browser's `Origin: http://localhost:3000` while the
request arrives with `Host: localhost:8080`, which is exactly the cross-origin
write shape the API refuses (PAS-SEC-01, `studio-api/security.py`). In the
container both sides are one origin behind nginx, so nothing is needed there.

### Frontend gate (run before every PR)

CI runs both of these, so run them locally first:

```bash
node_modules/.bin/tsc --noEmit    # strict mode
npm test                          # vitest, unit tests under lib/ only
```

Do **not** use `npm run lint`: eslint isn't installed, so it fails.

## Routes

All app routes live under `app/(studio)/` and share the studio chrome (left nav
+ panels). The left-nav labels differ from the route paths in a couple of places
(noted below).

| Route | Rail label | Description |
|-------|-----------|-------------|
| `/` → `/home` | Home | Welcome, four ways in, an opener gallery that hands a prompt to the assistant, an attention strip for failed/running runs, and the guided tour |
| `/workflows` | Analyses | Project list (resizable/collapsible), a workflow picker in the header, the collapsible "Set up your run" form with its "Choose where to run the workflow" choice (`run-flow-panel.tsx`: "Studio's Compute Resources" or "Bring Your Own Compute", saved to the workflow's `pegasus.properties`; a warning when a GPU step has no free GPU), an always-visible graph pane with a run picker (full screen is resizable), Results + Steps tabs, "Where it ran", a slow-step warning, failure cards, Rename/Delete. `?project=` / `?run=` select on arrival |
| `/data` | Data | Drag-and-drop upload into the workspace, with copy-path and delete |
| `/marketplace` | Examples | Browse & "Add" (git-clone) workflows sourced live from GitHub (repos tagged `pegasus-studio-example`, enriched from the PegasusHub registry), with search, subject chips, source filter and sort; cards already in the workspace show "In your workspace · Open" |
| `/notebooks` | Notebooks | JupyterLab framed in Studio (served at `/jupyter/`), with "Open in new tab" and Reload; `?path=` opens a folder |
| `/provisioning` | Resources | A section menu (`#overview`, `#ospool`, `#compute`) that differs by deployment. Overview is the capacity verdict and pool numbers. Hosted adds OSPool and **Bring Your Own Compute** (`components/resources/source-tabs.tsx`: the user's HTCondor annex on their own cluster, with create, download and shut down; "Cloud & testbeds" is marked Soon). Open OnDemand adds **Where workflows run** (`compute-site-card.tsx`: site catalog + allocation fields derived from the catalog, Slurm account dropdown). A main rail item since #468 |
| `/settings` | Settings | Titled "Application Settings": Notifications, Pegasus Options (Simplified Run, guided tour, where planning runs, workflow secrets, container registry, scan roots), Chat, Feedback & privacy; LLM Providers and Tools under Advanced |

`/welcome` only redirects to Home: its introduction moved to the JupyterHub
login page. The foot of the rail carries **Website** (the PegasusAI site, in a
new tab) and **Feedback** above Settings.

The Advanced grouping follows the **Simplified Run** preference
(`GET/PUT /api/settings/ui`). Turning it off flattens the rail and opens the
Advanced disclosure — the individual Pegasus steps — by default. It no longer
*replaces* the run form: "Set up your run" owns the parameters in both modes,
because rendering them in both places gave one value two editors (#241).

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
  does not navigate. `dashboard-view.tsx` opens it on the first visit, then once
  per browser session (a session cookie) while the `tour_on_login` preference
  is on; per-login tours add the consent and profile steps only while they are unanswered. Every
  card carries "Don't show me this tour again"; **Take a tour** in the account
  menu always runs the full tour.
- **Workflow graph and files**: `components/workflows/dag-viewer.tsx` (React
  Flow + dagre) groups repeated steps, and with **Show files** draws file nodes
  grouped the same way. A click opens `job-files-panel.tsx` (a step's inputs and
  outputs, or a file node). Previews go through `file-preview.tsx`, a renderer
  registry shared with the Results tab: image, PDF, JSON tree, CSV/TSV table
  (parsing in `lib/preview/csv.ts`, unit-tested), text, with partial previews
  of large text.
- **Styling**: Tailwind 3 with the `pegasus-*` scale in `tailwind.config.ts`
  (Tailwind's sky ramp, matching JupyterHub and JupyterLab; it replaced the
  #314 cornflower) and semantic tokens in `app/globals.css`; light/dark theme-aware. Filled actions live at
  `pegasus-600`+ and decoration at `pegasus-500`-, because 600 is the first
  shade that carries white text at AA.
- **Terminal**: xterm.js over a WebSocket to a backend PTY (`/ws/terminal…`).
- **Chat**: OpenCode (`components/chat/opencode-chat.tsx`, through
  `/api/opencode/*`), with permission cards for what the engine asks about,
  including writes outside `~/workflows` and `~/data`. Each conversation is its
  own OpenCode session, so several can run at once.
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

The container copies the standalone build and runs it behind nginx on
`STUDIO_WEB_PORT` (default 3000), bound to `STUDIO_BIND_ADDR` (default
`127.0.0.1`) — supervisord passes both as `PORT` and `HOSTNAME`, which is what
the standalone server reads. It also passes `API_URL` (and `STUDIO_INTERNAL_SECRET`), so that a
server-side caller of studio-api reaches the allocated API port rather than a
literal `localhost:8080`. No server component calls the API today (the one that
did, `lib/server/ui-prefs.ts` for the old `/welcome` redirect, is gone); the
setting is kept for the next one. See `../studio-api/README.md` and the repo `Dockerfile`.
