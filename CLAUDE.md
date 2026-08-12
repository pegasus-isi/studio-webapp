# CLAUDE.md

Guidance for Claude Code (claude.ai/code) working in the **studio-webapp** repo
(github.com/pegasus-isi/studio-webapp).

## What this is

A web IDE for building, running, and monitoring Pegasus WMS workflows. One
`linux/amd64` container bundles the Next.js UI + FastAPI backend + JupyterLab +
Pegasus WMS + HTCondor + Apptainer + AI coding tools. **There is no built-in auth** —
an upstream spawner/ingress (JupyterHub/KubeSpawner) authenticates and the container
runs per-user. The image is consumed by `studio-system` (separate repo): a merge to
`main` publishes `ghcr.io/pegasus-isi/studio-webapp:main`, which production pulls with
`imagePullPolicy: Always`, so **a merge here redeploys to live user pods**.

## Commands

```sh
# Container (must be amd64 — on Apple Silicon the --platform flag is required, and
# emulated Jupyter kernels fail the kernel_info handshake; full runs need amd64 hardware)
docker build --platform linux/amd64 -t pegasus/studio-webapp:latest .
./local-test-instance.sh          # http://localhost:8888 (env: IMAGE=, PORT=)

# Backend dev (studio-api/)
cd studio-api
python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt pytest httpx
python init_db.py                 # creates ~/work/.studio/studio.db
uvicorn main:app --reload --port 8080   # Swagger at /docs

# Frontend dev (studio-web/)
cd studio-web && npm ci && npm run dev   # port 3000; /api + /ws proxied to $API_URL
npm run build
```

### Gates — run BEFORE every PR (CI runs neither)

```sh
# Backend (from repo root; assumes the studio-api venv is active with deps installed)
(cd studio-api && pytest -q)                          # ~185 tests; no Pegasus/Condor/keys needed

# Frontend (needs `npm ci` first so node_modules/.bin/tsc exists)
(cd studio-web && npm ci && node_modules/.bin/tsc --noEmit)   # strict mode
```

- `.github/workflows/deploy.yml` only builds/pushes the image on merge to `main` — **it
  runs no tests and no lint**. The local gates above are the only safety net.
- On the frontend use `tsc`, **not** `npm run lint` (eslint isn't installed) or `npm test`
  (zero test files exist). Single backend test: `pytest tests/test_workflows.py::test_x -q`
  (run from `studio-api/`).

## Architecture

nginx on **:8888** is the only exposed port, routing `/` → studio-web (Next.js
standalone, **:3000**), `/api/*` and `/ws/*` → studio-api (FastAPI, **:8080**),
`/jupyter/*` → JupyterLab (**:8889**). Supervised by supervisord (`docker/supervisor/`);
`docker/entrypoint.sh` runs `init_db.py` (first launch only) then `exec supervisord`.
`opencode serve` runs on localhost:**4747** for the chat engine.

- **studio-api/** — FastAPI + async SQLite (`~/work/.studio/studio.db`, hand-rolled
  migrations in `db.py`), structlog JSON logging. 16 routers in `routers/`, 15 services
  in `services/` (workflow scan/submit/monitor, condor pool, image builder via podman,
  opencode chat engine, notifications, terminal/PTY sessions). Workflow runs are
  discovered by globbing `**/braindump.yml` under `$HOME`; monitoring tails
  `workflow-events.jsonl` written by an external `workflow-monitor --serve` process.
- **studio-web/** — Next.js 15 App Router, React 19, TS strict, Tailwind 3, Zustand,
  TanStack Query, xterm.js, `@xyflow/react` DAG viewer. Routes under `app/(studio)/`;
  `app/chat/page.tsx` is a standalone chrome-less page iframed into JupyterLab by the
  `jupyter-pegasusai-chat/` extension (postMessage handshake, origin-checked).
- **Prefix handling is a recurring subtlety.** Under KubeSpawner the app lives at
  `/user/<name>/…`. `middleware.ts` strips the prefix, `app/layout.tsx` sets `<base
  href>` from `x-forwarded-prefix`, `lib/urls.ts` builds every URL relative to
  `document.baseURI`, nginx `sub_filter`s asset paths (`assetPrefix: "."`). **Never
  hardcode absolute app paths** (`fetch("/api…")`, `href="/…"`, absolute `new
  WebSocket`, `router.push("/…")`) — always go through `lib/urls.ts`. Left-nav links are
  full-page reloads, so panel/tab/chat state persists via Zustand `persist`
  (`lib/stores/`).
- **knowledge/** — canonical tool-agnostic skill/agent knowledge base, installed to
  `/opt/pegasus-ai/knowledge/`. Per-tool adapters in `studio-api/knowledge/` *generate*
  `CLAUDE.md`/`AGENTS.md`/`.mcp.json`/`.opencode/` into the user's runtime workspace —
  those generated files are product output, not repo config.
- **jupyter-pegasus-ai-client/** — vendored copy of TauferLab's private client (see its
  `VENDORED.md`); local mods are limited to three files (`src/configPlugin.tsx`,
  `src/api.ts`, `src/toolbarPlugin.ts`). Disable with `--build-arg
  ENABLE_PEGASUS_BOT_CLIENT=0`. Respect the vendoring boundary.
- Chat is engine-dispatched (`routers/chat.py`): `opencode` (default) or `builtin`
  fallback. Marketplace/Examples is sourced live from GitHub (PegasusHub registry +
  curated Pegasus-ISI list), nothing baked in. "Add" git-clones into `~/work/workflows`.
- Deliberate exclusions: JupyterLab ships **without** `jupyter-ai` and without
  RTC/collaboration extensions (they break behind the proxy) — don't re-add. The ep/
  spawner running `privileged`/Apptainer nesting is intentional.

## Conventions

- **Conventional commits with a scope**, lowercase imperative subject:
  `fix(chat): name the active model explicitly on every opencode prompt`. Common scopes:
  `chat, workflows, marketplace, panels, dashboard, notifications, resources, jupyter,
  build, llm`.
- **All commits GPG-signed** — use the `gpg-commit` skill.
- **Branch + PR always**: `feat/<slug>` or `fix/<slug>`, push, `gh pr create`. **Do not
  merge PRs unless explicitly asked** — a merge publishes the production image.
- **Every PR needs an associated GitHub issue.** Before opening a PR, create one
  (`gh issue create`) describing the problem/feature it addresses, and link it from
  the PR body with a closing keyword (`Closes #NN`) so the merge closes it. If an
  issue already exists for the work, reference that instead of creating a duplicate.
- No enforced formatter/linter. Python: `from __future__ import annotations`, PEP 604
  unions, type hints, structlog `log.info("event_name", key=value)`. TS: strict, `@/*`
  alias. Comments in both codebases explain failure modes and "why" — match that tone.

## Status & review docs

- `SPEC.md` — design spec + V2 direction (note: some routes/endpoints in it have
  drifted, e.g. `/examples` is now `/marketplace`).
- `docs/TODO-PLAN.md` — phased roadmap (stale in places; predates ~PR #55).
- `docs/CODE_REVIEW_2026-07.md` and `docs/CODE_REVIEW_2026-08.md` — full-repo reviews;
  the Aug 2026 one is the current open-items list (event-loop blocking, silent-UI-fail
  in chat/DAG, subprocess-orphan-on-timeout, deploy/supply-chain safety, doc drift).
  Open security items across both: unrestricted `run_command` shell, plaintext LLM keys
  (also echoed by GET endpoints), Dockerfile `[trusted=yes]`.
- `studio-web/README.md` is stale (documents removed routes like `/workbench`,
  `/dashboard`); prefer this file and the code over it.

## Session usage logging

Append a usage entry to `../cc-usage-log.md` (in the parent `studio/` dir) at the end of
every session, per the parent CLAUDE.md rules.
