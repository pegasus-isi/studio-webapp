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
(cd studio-api && pytest -q)                          # ~1000 tests; no Pegasus/Condor/keys needed

# Frontend (needs `npm ci` first so node_modules/.bin/tsc exists)
(cd studio-web && npm ci && node_modules/.bin/tsc --noEmit)   # strict mode
(cd studio-web && npm test)                                   # vitest, lib/ only
```

- `.github/workflows/deploy.yml` runs all three gates (pytest, tsc, vitest) as a
  `test` job that gates build/push, and builds without pushing on
  `pull_request` (added in PR #57). Run them locally anyway before pushing to
  avoid a red PR.
- On the frontend `npm run lint` is still not a gate (eslint isn't installed).
  `npm test` is: vitest over `lib/**/*.test.ts`, node environment, no DOM —
  added in #367 for `lib/opencode/transcript.ts`, the chat transcript state
  machine, whose rules come from measured server behaviour and which three
  review rounds found ordering and reconciliation bugs in. Components are still
  covered only by `tsc`. Single backend test: `pytest tests/test_workflows.py::test_x -q`
  (run from `studio-api/`).

## Architecture

nginx on **:8888** is the only exposed port, routing `/` → studio-web (Next.js
standalone, **:3000**), `/api/*` and `/ws/*` → studio-api (FastAPI, **:8080**),
`/jupyter/*` → JupyterLab (**:8889**). `opencode serve` (**:4096**) is
loopback-only and reached solely through studio-api's proxy, authenticated with
a per-user password the entrypoint writes to `~/.studio/opencode-password`.
Supervised by supervisord (`docker/supervisor/`);
`docker/entrypoint.sh` runs `init_db.py` (first launch only) then `exec supervisord`.

**Those port numbers are defaults, not constants.** All five come from the
environment (`STUDIO_PORT`, `STUDIO_API_PORT`, `STUDIO_WEB_PORT`,
`STUDIO_JUPYTER_PORT`, `STUDIO_OPENCODE_PORT`), with `STUDIO_ALLOC_PORTS=1` to
pick free ones per session — the image also runs on shared hosts (Open OnDemand,
where Apptainer gives it the *host* network namespace by default), and there a
fixed port is two users colliding. The nginx configs are therefore templates
under `docker/nginx/*.tmpl`, rendered by the entrypoint into
`$STUDIO_RUNTIME_DIR` (which also holds the pid files, supervisor socket and
nginx temp dirs, uid-suffixed) and loaded with `nginx -c`; `/etc` is not
writable by the injected uid, so **never bake a rendered config into the image**.
`entrypoint.sh --render-only` renders and exits — the image build uses it to run
`nginx -t`. If you add a `@STUDIO_*@` placeholder to a template, add it to
`render_template()` too; `tests/test_port_rendering.py` fails if the two drift.

- **studio-api/** — FastAPI + async SQLite (`~/.studio/studio.db`, hand-rolled
  migrations in `db.py`), structlog JSON logging. 20 routers in `routers/`, 35 services
  in `services/` (workflow scan/submit/monitor, condor pool, image builder via Apptainer,
  notifications, terminal/PTY sessions, telemetry consent). `mcp.py` serves the
  Pegasus tools to the OpenCode engine. Workflow runs are
  discovered by globbing `**/braindump.yml` under `$HOME` (`STUDIO_SCAN_DIR`),
  plus user-added scan roots/excludes from Settings → Pegasus Options
  (`/api/settings/workflow-scan`, merged in `workflow_scanner`'s async
  wrappers); monitoring tails `workflow-events.jsonl` written by an external
  `workflow-monitor --serve` process.
- **studio-web/** — Next.js 15 App Router, React 19, TS strict, Tailwind 3, Zustand,
  TanStack Query, xterm.js, `@xyflow/react` DAG viewer. Routes under `app/(studio)/`;
  `app/chat/page.tsx` is a standalone chrome-less page iframed into JupyterLab by the
  `jupyter-pegasusai-chat/` extension (postMessage handshake, origin-checked).
- **The API refuses cross-origin writes** (`security.py`,
  `CrossOriginWriteMiddleware`). Studio has no auth of its own — the gateway's
  cookie rides along on any request a page makes — and CORS governs only whether
  a page may *read* a reply, so unsafe methods get their own `Origin` /
  `Sec-Fetch-Site` check. Requests with no `Origin` are allowed on purpose:
  curl, the test client and the local MCP client cannot be driven cross-site by
  a victim's browser. Running the frontend separately (`npm run dev`) therefore
  needs `STUDIO_ALLOWED_ORIGINS=http://localhost:3000` on the backend.
- **The session token gate is off unless `STUDIO_AUTH_TOKEN` is set**, and when
  it is, four things turn on together (#218): the proxy's cookie gate, loopback
  binds, a real JupyterLab token, and the `X-Studio-Internal` secret
  `security.InternalSecretMiddleware` demands. Do not add one without the
  others — on a shared host a loopback bind is not a boundary between local
  users, so the cookie gate alone protects nothing. Every location proxying to
  studio-api must `include` the stamp fragment; a location that forgets it is a
  hole straight past the middleware, which `tests/test_session_gate.py`
  enforces. The gate fragments render to bare comments when the gate is off, so
  `nginx -t` in the image build runs **twice** — once each way — or their
  directives are never parsed at all.
- **Prefix handling is a recurring subtlety.** Under KubeSpawner the app lives at
  `/user/<name>/…`. `middleware.ts` strips the prefix, `app/layout.tsx` sets `<base
  href>` from `x-forwarded-prefix`, `lib/urls.ts` builds every URL relative to
  `document.baseURI`, nginx `sub_filter`s asset paths (`assetPrefix: "."`). **Never
  hardcode absolute app paths** (`fetch("/api…")`, `href="/…"`, absolute `new
  WebSocket`, `router.push("/…")`) — always go through `lib/urls.ts`. Left-nav links are
  full-page reloads, so panel/tab/chat state persists via Zustand `persist`
  (`lib/stores/`). Open OnDemand assigns its own, differently-shaped prefix
  (`STUDIO_URL_PREFIX`, e.g. `/node/compute-1/20153`, not `/user/<name>`) —
  `docker/nginx/prefix-proxy.conf.tmpl` routes on it generically, rendered
  inert when unset. `middleware.ts`/`lib/server/base-path.ts` needed no
  change: nginx strips the prefix before the request reaches studio-web
  either way, and `base-path.ts` reads `x-forwarded-prefix` generically.
- **`ood-app/`** builds a second, Rocky9-based image for Open OnDemand instead
  of K8s/KubeSpawner — same `docker/`, `studio-api/`, `studio-web/`, rebuilt on
  a different base because the intent is for HTCondor to submit onward into
  the cluster's own Slurm queue, which needs the cluster's bind-mounted Slurm
  tools to be library-ABI compatible (not built yet — see `ood-app/README.md`
  for current status). Read `ood-app/README.md` and `SPEC.md`'s "Open OnDemand
  / Apptainer Packaging" section before changing anything under `docker/` that
  the OOD image also relies on. The batch-connect app (`pegasus-isi/bc_pegasus_ai_studio`, a separate repo) runs the image
  with `apptainer --home "$STUDIO_HOME"` (a Studio directory, `~/pegasus-studio`
  by default) so Studio never writes into the user's real home (#482). That is
  why nothing in studio-api needs to know about OOD: keep it that way, and
  resolve paths from `$HOME` rather than from the passwd entry or a hardcoded
  `/home/<user>`. The passwd entry there is the user's *real* home (the app
  binds the host `/etc/passwd`), so anything that reads it goes wrong in a way
  that is invisible on K8s, where the two agree: the JVM's `user.home` (hence
  `JAVA_TOOL_OPTIONS=-Duser.home`, #489) and HTCondor, which resets `HOME` in
  local-universe jobs (the remote wrapper exports it, #508). Site settings come
  from the admin's `site.env` in the app: `STUDIO_SITE_CATALOG_FILE` replaces
  the default `pegasus-studio.yml` (#496), and an empty
  `STUDIO_LLM_GATEWAY_URL` (the EL9 image's default) means no gateway: no
  default provider, saved providers offered in chat, never a shared key (#498,
  #505).
- **The workspace root is `$HOME`** (`studio-api/workspace.py`, #401). Projects
  live in `$HOME/workflows/<name>/`, uploads in `$HOME/data/`. It used to be
  `$HOME/work`, a level copied from JupyterHub images where only that subdir is
  on the persistent volume — Studio mounts the whole home, so it marked nothing
  while disagreeing with JupyterLab's root, `.studio`, the generated
  `CLAUDE.md`/`AGENTS.md` and the run scanner, all of which had already moved up.
  Nothing was migrated: `$HOME/work/workflows` is still listed (`project_roots()`),
  never written to. Because the root is now `$HOME`, "inside the workspace" is no
  longer sufficient for the file tools — `workspace.is_protected()` keeps them out
  of `.studio`, `.ssh`, `.condor` (the IDTOKEN) and friends. Resolve the root
  through `workspace.py`; never rebuild it from `$HOME` inline.
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
- **jupyter-pegasus-ai-client/** — vendored copy of TauferLab's private client (see its
  `VENDORED.md`); local mods are limited to three files (`src/configPlugin.tsx`,
  `src/api.ts`, `src/toolbarPlugin.ts`). Disable with `--build-arg
  ENABLE_PEGASUS_BOT_CLIENT=0`. Respect the vendoring boundary.
- **Planning on the submit host** (`services/remote_exec.py`) is an HTCondor
  local-universe job, and the schedd starts those (and DAGMan) from its
  `SCHEDD_INTERVAL` timer, 300 s by default. Every submit path calls
  `services/schedd.nudge()` (`condor_reschedule` now and after the 5 s
  `SCHEDD_MIN_INTERVAL`), or runs sit idle for minutes with nothing in the UI
  saying why (#488, #494). It is fire-and-forget; `tests/conftest.py` disables
  it under test.
- **Work that outlives a request gets a broker.** `services/run_flow.py` (the
  one-button Run) and `services/chat_turn.py` (an assistant turn) both run as a
  background task with a buffered event log; subscribers replay from the start
  and then follow live. That is what makes both survive a navigation, a closed
  tab or a dropped connection — Studio's left nav used to be full page loads,
  and anything owned by the request died with it. Reach for this shape rather
  than sequencing calls in the browser.
- **Chat has two engines, chosen per user** by the `chat_engine` UI preference
  (#367). `builtin` is the in-process agent loop in `routers/chat.py` (direct
  provider calls with tool use, Anthropic + OpenAI-compatible + Responses API)
  and is still the default. `opencode` makes the panel a thin client of the
  supervised `opencode serve` process in the same pod: `routers/opencode.py`
  proxies `/api/opencode/*` to `127.0.0.1:4096` **verbatim**, including the SSE
  stream, and `studio-web/lib/opencode/` + `components/chat/opencode-parts.tsx`
  render OpenCode's own parts through a registry with a fallback. The intended
  end state is one engine — the builtin loop goes once OpenCode is proven in a
  real pod — so **do not add features to the builtin loop**; see #367 and
  `docs/OPENCODE-CHAT-SPIKE.md`, which records what the spike established
  (config changes need a process restart, text streams as `message.part.delta`
  and not as whole parts, the plugin hook does receive the session id).
  Nothing on the Python side parses an OpenCode payload, and it must stay that
  way: that is what lets a version bump be a version bump. The Pegasus tools
  reach that engine over MCP — `routers/mcp.py` serves them at `/api/mcp`
  (stateless Streamable HTTP) and the generated `opencode.json` registers it as
  a loopback remote server beside `pegasus-docs`/`kiso-docs`. Its tool list is
  read from `services/chat_tools.py`, so **add a Pegasus tool in one place** and
  both engines get it; the generic file/shell tools are deliberately not
  exposed, since OpenCode has its own behind its own permission gate. `@opencode-ai/sdk`
  and the `opencode-ai` pin in the Dockerfile must match, which
  `tests/test_opencode_version_pin.py` enforces. Anything reading the knowledge tree goes through
  `knowledge/layout.py`; nothing should resolve it independently — `chat.py`
  keeping its own `KNOWLEDGE_ROOT` after the knowledge base moved is how the
  chat lost its context, personas and skills at once (#179).
  The Marketplace is sourced live from GitHub (PegasusHub registry +
  curated Pegasus-ISI list), nothing baked in. "Add" git-clones into `~/workflows`.
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
- `docs/UX-NON-SPECIALIST-DESIGN.md` — the redesign that made Studio usable
  without Pegasus literacy (tracker issue #241). **All seven phases shipped**;
  Appendix B is the status table and the Rollout section records one deviation:
  the simplified interface was flipped on by default *ahead of* the terminology
  check that was meant to gate it. `docs/ux-terminology-test.md` is that
  protocol — written, runnable, and **still unrun**, so the vocabulary now in
  front of every user has not been validated with anyone outside the team.
- `docs/TODO-PLAN.md`, `docs/CODE_REVIEW_2026-07.md` — historical snapshots
  (banners point to the Aug review).
- `docs/CODE_REVIEW_2026-08.md` — the August full-repo review. **Fully
  remediated**: every finding carries a `**Status:** ✅ PR #NN` line. The whole
  set shipped across PRs #57/#60/#62/#64/#66/#68/#126/#128/#130/#132/#134/#136,
  including the previously-open security items — `run_command` now has a
  per-turn approval gate (SEC-4, #68), LLM keys are write-only over the API
  (SEC-3, #64) and encrypted at rest when `STUDIO_SECRET_KEY` is set (S7, #136),
  and the Dockerfile installs are checksummed / from signed repos (INF-4, #66).
  Check the doc's Status lines before assuming anything there is still open.

## Session usage logging

Append a usage entry to `../cc-usage-log.md` (in the parent `studio/` dir) at the end of
every session, per the parent CLAUDE.md rules.
