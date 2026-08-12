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

| Route | Nav label | Description |
|-------|-----------|-------------|
| `/` | Home | Dashboard — capability cards, recent runs, resource snapshot, version badge |
| `/workflows` | Workflows | Project list, per-project Generate/Plan/Run/Build, run list + detail, live DAG viewer |
| `/marketplace` | Marketplace | Browse & "Add" (git-clone) workflows sourced live from GitHub (PegasusHub + curated Pegasus-ISI) |
| `/notebooks` | Notebooks | Opens JupyterLab (served at `/jupyter/`) |
| `/provisioning` | Resources | HTCondor pool + OSPool (OSG) capacity/pressure panels |
| `/settings` | Settings | LLM providers, chat engine + shell-command approval, AI tools, container registry |

`app/chat/page.tsx` is a standalone, chrome-less chat page (not under the studio
layout). It is iframed into JupyterLab by the `jupyter-pegasusai-chat` extension
via an origin-checked `postMessage` handshake; the same chat also renders in the
studio's slide-in panel.

## Architecture

- **State**: Zustand for client/panel/chat state (with `persist` to
  localStorage), TanStack Query for server state.
- **Styling**: Tailwind 3 with a custom Pegasus palette; light/dark theme-aware.
- **Terminal**: xterm.js over a WebSocket to a backend PTY (`/ws/terminal…`).
- **Chat**: SSE streaming with tool-call/result display and a per-turn
  shell-command approval prompt.
- **DAG viewer**: `@xyflow/react` + dagre, live-polling while a run is active.
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
