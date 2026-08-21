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
python init_db.py                 # creates ~/.studio/studio.db
uvicorn main:app --reload --port 8080   # Swagger at /docs

# Frontend dev (studio-web/)
cd studio-web && npm ci && npm run dev   # port 3000; /api + /ws proxied to $API_URL
npm run build
```

### Gates — run BEFORE every PR

```sh
# Backend (from repo root; assumes the studio-api venv is active with deps installed)
(cd studio-api && pytest -q)                          # ~242 tests; no Pegasus/Condor/keys needed

# Frontend (needs `npm ci` first so node_modules/.bin/tsc exists)
(cd studio-web && npm ci && node_modules/.bin/tsc --noEmit)   # strict mode
```

- `.github/workflows/deploy.yml` now runs both gates (pytest + tsc) as a `test`
  job that gates build/push, and builds without pushing on `pull_request` (added
  in PR #57). Run them locally anyway before pushing to avoid a red PR.
- On the frontend use `tsc`, **not** `npm run lint` (eslint isn't installed) or `npm test`
  (zero test files exist). Single backend test: `pytest tests/test_workflows.py::test_x -q`
  (run from `studio-api/`).

## Architecture

nginx on **:8888** is the only exposed port, routing `/` → studio-web (Next.js
standalone, **:3000**), `/api/*` and `/ws/*` → studio-api (FastAPI, **:8080**),
`/jupyter/*` → JupyterLab (**:8889**). Supervised by supervisord (`docker/supervisor/`);
`docker/entrypoint.sh` runs `init_db.py` (first launch only) then `exec supervisord`.
`opencode serve` runs on localhost:**4747** for the chat engine.

- **studio-api/** — FastAPI + async SQLite (`~/.studio/studio.db`, hand-rolled
  migrations in `db.py`), structlog JSON logging. 16 routers in `routers/`, 15 services
  in `services/` (workflow scan/submit/monitor, condor pool, image builder via Apptainer,
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
- **Knowledge base** — skills, agents, templates and examples are authored in
  [pegasus-isi/claude-plugin-marketplace](https://github.com/pegasus-isi/claude-plugin-marketplace)
  and checked out into the image at `/opt/pegasus-ai/marketplace`, tracking
  `main` (`KNOWLEDGE_REF` overrides it for a fixed build — the running version is
  the user checkout's SHA, shown in Settings → Tools, not a build-time pin).
  That copy is a **template**: the entrypoint clones it
  into `~/.local/share/pegasus-ai/marketplace` at first launch, which is what
  tools read — writable, so skills update with `git pull` and roll back with
  `git checkout`, no root and no rebuild, and it survives pod restarts. **Do not add a copy under `knowledge/`** — CI fails
  if `knowledge/{skills,agents,templates,examples,mcp,references}` reappears,
  because a second copy is how the two drifted into giving different container
  advice per tool. `knowledge/overlay/STUDIO_ENV.md` is Studio's own environment
  context and is composed on top of the shared guide.
  `studio-api/knowledge/layout.py` resolves the tree (`KNOWLEDGE_ROOT` env, set
  by the image → `marketplace_root()`: `KNOWLEDGE_MARKETPLACE`, then the
  user's own `~/.local/share/pegasus-ai/marketplace`, then the image-baked seed
  at `/opt/pegasus-ai/marketplace` → legacy `/opt/pegasus-ai/knowledge`);
  everything else goes through it. `knowledge/seed.py` writes the Claude Code plugin seed
  directory that points at the checkout. Per-tool adapters in `studio-api/knowledge/` *generate*
  `CLAUDE.md`/`AGENTS.md`/`.mcp.json`/`.opencode/` into the user's runtime workspace —
  those generated files are product output, not repo config.
  For local backend dev, point `KNOWLEDGE_ROOT` at a clone's
  `plugins/pegasus-ai`, otherwise the tree resolves empty.
  **Trust model.** Tracking `main` means a commit on the marketplace repo
  reaches every pod at its next start, so what the tree may do is bounded on
  this side: skills and agents are prompt text the assistants follow; the
  tree's `.mcp.json` is filtered by `knowledge/claude_code.py:filter_mcp_servers`
  to `https://` remotes with no `command`, `headers` or `${VAR}` expansion
  before it is written to `~/.mcp.json` or merged into `~/.claude.json`; `git`
  in `knowledge/checkout.py` and the entrypoint runs with hooks, fsmonitor,
  credential helpers and prompts disabled and refuses an origin that is not
  http(s) or the image seed. The plugin Claude Code enables from the checkout
  (`pegasus-ai@scitech`) still loads the plugin's own `.mcp.json` through
  Claude Code's plugin cache, unfiltered — the control for that, and for the
  prose itself, is branch protection with required reviews (and signed
  commits) on the marketplace repo, not code here. A tree update is logged at
  `warning` (`knowledge_checkout_updated`, before/after SHA, upstream).
  Pinning `KNOWLEDGE_REF` to a tag or verifying commit signatures before
  adapters run are the documented alternatives if that control proves
  insufficient.
- **jupyter-pegasus-ai-client/** — vendored copy of TauferLab's private client (see its
  `VENDORED.md`); local mods are limited to three files (`src/configPlugin.tsx`,
  `src/api.ts`, `src/toolbarPlugin.ts`). Disable with `--build-arg
  ENABLE_PEGASUS_BOT_CLIENT=0`. Respect the vendoring boundary.
- Chat is engine-dispatched (`routers/chat.py`): `opencode` (default) or `builtin`
  fallback. **`chat.py` is not legacy** — `_opencode_stream()` lives in it and
  serves every real session; only the builtin engine's own machinery
  (`TOOL_DEFINITIONS`, the direct provider calls, the Responses-API fallback) is
  the seldom-exercised part. Both share `_load_system_prompt()` and
  `_process_slash_command()`, so a change made "just for the builtin engine"
  reaches opencode too — which is how the chat lost its context, personas and
  skills at once when this file kept its own `KNOWLEDGE_ROOT` after the
  knowledge base moved (#179). Anything reading the knowledge tree goes through
  `knowledge/layout.py`; nothing should resolve it independently.
  The Marketplace is sourced live from GitHub (PegasusHub registry +
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
- **Every PR needs an associated GitHub issue — no exceptions.** Create it
  (`gh issue create`) **before** `gh pr create`, describing the problem or feature
  in its own terms, and link it from the PR body with a closing keyword
  (`Closes #NN`) so the merge closes it. If an issue already exists, reference that
  rather than creating a duplicate.
  - This holds for **every** PR, including one-line fixes, follow-ups that repair
    a defect in a PR merged minutes earlier, docs-only changes, and anything done
    under time pressure. Rapid follow-ups are exactly when this gets skipped, and
    they are the changes whose motivation is hardest to reconstruct later.
  - The issue is not paperwork for the PR: it records *why* — the symptom, the
    evidence, what was observed in a pod or a log. A PR says what changed; the
    issue is where someone finds out what was actually wrong.
  - If a PR was opened without one, create the issue afterwards and link it, then
    close it if the PR already merged. Do not leave it unlinked.
- No enforced formatter/linter. Python: `from __future__ import annotations`, PEP 604
  unions, type hints, structlog `log.info("event_name", key=value)`. TS: strict, `@/*`
  alias. Comments in both codebases explain failure modes and "why" — match that tone.

## Status & review docs

- `SPEC.md` — design spec + V2 direction (refreshed to current code in PR #138).
- `README.md`, `studio-web/README.md`, `studio-api/README.md` — current
  (rewritten in PRs #134 / #138); prefer them and the code.
- `docs/TODO-PLAN.md`, `docs/CODE_REVIEW_2026-07.md` — historical snapshots
  (banners point to the Aug review).
- `docs/CODE_REVIEW_2026-08.md` — the August full-repo review. **Fully
  remediated**: every finding carries a `**Status:** ✅ PR #NN` line. The whole
  set shipped across PRs #57/#60/#62/#64/#66/#68/#126/#128/#130/#132/#134/#136,
  including the previously-open security items — `run_command` now has a
  per-turn approval gate (SEC-4, #68), LLM keys are write-only over the API
  (SEC-3, #64) and encrypted at rest when `STUDIO_SECRET_KEY` is set (S7, #136).
  Every root-level install in the Dockerfile is either from a `signed-by=` apt
  repo or a sha256-pinned download: Pegasus 6.0 is a pinned development `.deb`
  (`PEGASUS_DEB_URL`/`PEGASUS_DEB_SHA256` — its nightly repo publishes no
  signature, so it is pinned, not tracked; bump procedure in the Dockerfile
  comment), Apptainer's `.deb` is hash-pinned, and `jupyter-pegasusai-chat`
  installs from a committed `yarn.lock` with `--immutable` (INF-4, #66; restored
  after `c50c700` re-enabled the unsigned repo).
  `studio-api/tests/test_dockerfile_guards.py` plus a grep step in CI fail if
  any of that comes back.
  Check the doc's Status lines before assuming anything there is still open.

## Session usage logging

Append a usage entry to `../cc-usage-log.md` (in the parent `studio/` dir) at the end of
every session, per the parent CLAUDE.md rules.
