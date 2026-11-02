# PegasusAI Studio API

FastAPI backend for PegasusAI Studio — a web IDE for building, running, and
monitoring [Pegasus WMS](https://pegasus.isi.edu/) workflows.

## Architecture

```
studio-api/
├── main.py                 # FastAPI app, lifespan, middleware, routers
├── db.py                   # Async SQLite (aiosqlite) — hand-rolled migrations
├── models.py               # Pydantic v2 schemas + enums
├── security.py             # CORS allowlist, cross-origin write gate,
│                          #   WebSocket origin guard, proxy-secret middleware
├── logging_config.py       # structlog JSON processor chain
├── workspace.py            # The workspace root ($HOME) and its workflows/ and data/
├── routers/                # 21 routers (see API Endpoints below)
├── llm/
│   ├── providers.py        # Provider registry, key-requirement + model discovery
│   └── propagator.py       # Push the active LLM config to installed tools
├── knowledge/              # Per-tool adapters that GENERATE runtime config
│   ├── layout.py           # Resolves the knowledge tree + reads its layout
│   ├── seed.py             # Writes the Claude Code plugin seed directory
│   ├── checkout.py         # Marketplace checkout status + fast-forward update
│   ├── adapters.py         # KnowledgeAdapter ABC + registry
│   ├── claude_code.py / opencode.py / codex_cli.py / antigravity.py / web_chat.py
├── services/               # workflow scan/submit/monitor, condor pool, image
│                           # builder, notifications, terminals,
│                           # chat_permissions, procutil, run_flow, run_outputs,
│                           # run_diagnosis, chat_turn, onboarding_metrics,
│                           # job_files, project_rename, telemetry,
│                           # remote_exec + schedd (planning on the
│                           # submit host, condor_reschedule), site_config,
│                           # execution_sites, run_placement (annex or not,
│                           # per workflow), shared_fs, workflow_gpus,
│                           # slow_steps, annex, rating_telemetry, …
├── init_db.py              # CLI: initialize / migrate the SQLite schema
├── propagate_llm.py        # CLI: propagate the saved LLM config to installed tools
└── tests/                  # ~1000 tests; no Pegasus/Condor/keys needed
```

The SQLite DB (`~/.studio/studio.db`; an existing one is moved out of `~/work/.studio` on first start) has **12 tables**: `app_settings`,
`llm_config`, `provider_configs`, `tool_installations`, `workflow_runs`,
`chat_messages`, `message_feedback`, `notifications`, `notification_log`,
`workflow_secrets`, `feedback_episodes`, `feedback_giveups`.

## Development Setup

### Prerequisites

- Python 3.11+ (the container uses 3.13; the Open OnDemand EL9 image, 3.12)
- Pegasus WMS + HTCondor are optional for dev — health checks degrade gracefully

### 1–3. venv, deps, DB

```bash
cd studio-api
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt pytest httpx
python init_db.py          # creates ~/.studio/studio.db
```

### 4. Run the dev server

```bash
# STUDIO_ALLOWED_ORIGINS is only needed when the frontend runs separately
# (npm run dev on :3000) — the API refuses cross-origin writes, and the dev
# proxy makes them look exactly like one.
STUDIO_ALLOWED_ORIGINS=http://localhost:3000 uvicorn main:app --reload --port 8080
```

Swagger UI at `http://localhost:8080/docs`.

### 5. Tests (the pre-PR gate; CI runs them too)

```bash
pytest -q                                    # whole suite
pytest tests/test_workflows.py::test_x -q    # single test
```

All tests use temporary SQLite DBs and need no Pegasus, HTCondor, or LLM keys.
`.github/workflows/deploy.yml` runs this suite, with the frontend's `tsc` and
`npm test`, as the job that gates the image build.

## API Endpoints

20 routers are registered under `/api` (WebSockets under `/ws`): `health`,
`settings`, `llm`, `knowledge`, `tools`, `files`, `jupyter`, `terminal`,
`ai_terminal`, `workflows`, `chat`, `feedback`, `opencode`, `mcp`,
`notifications`, `episodes`, `dashboard`, `provisioning`, `pegasus_bot`,
`marketplace`. A 21st, `hub_auth`, is mounted at `/_hub_auth` outside `/api`
and out of the schema (see Request gates). Highlights (see
`/docs` for the full list):

Two are not browser-facing. `opencode` proxies `/api/opencode/*` verbatim to the
loopback agent server that backs the chat panel. `mcp` serves the Pegasus
workflow tools at `/api/mcp` over stateless Streamable HTTP, read-only, for that
same engine — its tool list is read from `services/chat_tools.py`, so a Pegasus
tool added there reaches both chat engines.

### Request gates

**Authentication.** Studio has no accounts of its own; it checks every request
against whatever spawned it.

- **Under JupyterHub** the entrypoint turns on `STUDIO_HUB_AUTH` when the hub's
  OAuth variables are present (`JUPYTERHUB_API_TOKEN`, `JUPYTERHUB_CLIENT_ID`,
  `JUPYTERHUB_OAUTH_CALLBACK_URL`, `JUPYTERHUB_USER`) and the session token
  gate is not in use. nginx then sends every request through an
  `auth_request` to `/_hub_auth/check` (`docker/nginx/hub-auth-server.conf.tmpl`),
  so pages, API, WebSockets and JupyterLab are all covered.
  `routers/hub_auth.py` runs the HubOAuth flow over plain HTTP (the image has
  no `jupyterhub` package): `/login` sends a page load to the hub's authorize
  endpoint and answers API, WebSocket and non-GET requests with 401;
  `/callback` checks state, exchanges the code, and accepts only
  `JUPYTERHUB_USER` or a token carrying one of
  `JUPYTERHUB_OAUTH_ACCESS_SCOPES`, then sets an HMAC-signed, HttpOnly,
  prefix-scoped session cookie (8 hours, key derived from the pod's hub
  token). `STUDIO_HUB_AUTH=0` forces it off.
- **Under Open OnDemand** the session token gate (`STUDIO_AUTH_TOKEN`, below)
  does the same job.
- **Neither** (a local `docker run`, `uvicorn` in dev): unauthenticated.

Three more checks run before any handler, all of them no-ops in the deployment
that does not need them:

- **CORS** — allowlist empty by default (`STUDIO_ALLOWED_ORIGINS` adds to it).
  Same-origin needs no entry.
- **Cross-origin writes** — unsafe methods must carry a same-origin or
  allowlisted `Origin`, with `Sec-Fetch-Site` as a second signal. CORS governs
  whether a page may *read* a reply, never whether the request runs, so without
  this any page the user visited could drive every mutating route with the
  gateway's cookie. Requests with no `Origin` are allowed deliberately: curl,
  the test client and the in-pod MCP client send none and cannot be driven
  cross-site by a victim's browser. WebSockets are checked at the handshake
  instead, where a close code can be sent.
- **Proxy secret** — only when the session token gate is on; see below.

### Health, Settings & LLM

| Method | Path | Notes |
|--------|------|-------|
| GET | `/api/health`, `/api/health/detailed` | liveness + DB/Pegasus/HTCondor |
| GET | `/api/health/submit-host` | whether this workspace's Pegasus matches the submit host's |
| GET/PUT | `/api/settings` | LLM-config accessor (shares the validated `llm` save path) |
| GET/PUT | `/api/settings/planning` | where `pegasus-plan` runs (this pod or the submit host): the stored preference and the effective outcome |
| GET/PUT/DELETE | `/api/settings/workflow-secrets` | credentials a generator reads from its environment, overlaid on it when Studio generates (never on planning); encrypted at rest. GET says which are set and when, never the value |
| GET/PUT | `/api/settings/registry` | container registry for Build Image; the password is write-only and encrypted at rest (S7) |
| GET/PUT | `/api/settings/workflow-scan` | user-added run-scan roots/excludes, merged with the env defaults (#306) |
| GET/PUT | `/api/settings/ui` | interface preferences (Simplified Run, disclosures, `welcome_dismissed`, `skip_welcome`, `tour_on_login`, `chat_engine`). The PUT replaces the whole object; the client merges before sending |
| GET/PUT | `/api/settings/compute-site` | the user's hosted site catalog and allocation in `~/.pegasusrc` (`pegasus.catalog.site.repo.file`, `env.RESOURCE_*`), with the catalogs published for the running Pegasus series, which `RESOURCE_*` fields each references, and the user's Slurm accounts where `sacctmgr` exists. PUT keeps every other property in the file (#468). `overrides` rows `{site, namespace, key, value}` become `pegasus.catalog.site.sites.<site>.profiles.<ns>.<key>` properties; they are stored per catalog (`saved_overrides`) so switching cluster swaps the set, and GET returns `effective_site`, the execution site's YAML with them merged in (#478). `default_label` names the default catalog: "Studio's own pool", or the administrator's (`STUDIO_SITE_CATALOG_FILE`/`_LABEL`, #496) |
| GET/PUT | `/api/settings/telemetry` | whether this user's traces may carry chat text, plus a read-only `decided` (the choice was made either way: "off" is also the default, so the tour keeps asking until it is, #454). Its own row, not a `UIPreferences` field, because that PUT replaces the bag wholesale and its GET substitutes defaults for invalid values — both wrong for a consent flag. Saving propagates, since the engine reads plugin options once at startup |
| GET/PUT | `/api/settings/profile` | the optional four-question profile (research field, role, Pegasus/HTCondor experience, coding comfort 1–5) for the feedback program, stored in the `user_profile` setting with a server-stamped `answered_at`. Its own row for the same reasons as telemetry consent; the free-text field is never logged |
| GET/PUT | `/api/llm/config` | active provider/model; **keys are write-only** — GETs return `has_api_key`, never the value. `provider: ""` means nothing is configured (no gateway and no active provider, #498) |
| GET/PUT | `/api/llm/provider-configs` | saved providers (also write-only keys) |
| GET | `/api/llm/provider-configs/{id}/models` | model discovery using the **stored** key (server-side) |
| POST | `/api/llm/validate` | test key connectivity |

### Workflows

| Method | Path | Notes |
|--------|------|-------|
| GET | `/api/workflows` | discover + list runs |
| GET | `/api/workflows/{id}` `/jobs` `/events` `/estimate` | detail, jobs, SSE events, ETA |
| GET | `/api/workflows/{id}/graph.json` | DAG; `?include_pegasus=true` for the executable DAG, `?files=true` adds file nodes (`file:<lfn>`, grouping family, `result` flag) to the abstract one (#458) |
| GET | `/api/workflows/{id}/outputs` `/outputs/file` | files a run produced (paginated); preview or download one. `?head=1` serves the first 2 MB of a text/table file too large to preview whole (#460) |
| GET | `/api/workflows/{id}/jobs/{job}/files` | a job's inputs and outputs from `workflow.yml`, each located as `result` / `scratch` / `project` (or none) with size and previewability (#440) |
| GET | `/api/workflows/{id}/files/info?lfn=…` `/files/content?location=&path=` | describe files by name (a clicked file node); serve one, confined to its location's directory, with `download` / `head` |
| GET | `/api/workflows/{id}/diagnosis` | plain-language failure cards — cause, action, technical detail |
| GET | `/api/workflows/{id}/slow-steps` | running steps well past their usual duration in this and earlier runs (#586) |
| POST | `/api/workflows/{id}/analyze` | pegasus-analyzer |
| DELETE | `/api/workflows/{id}` | **destructive** — cancels if running, then removes the run's submit directory from disk |
| GET/POST | `/api/workflows/projects…` | project list, params, generate, plan, run, build image (SSE) |
| POST/GET | `/api/workflows/projects/{id}/run-flow` `/run-flow/events` | the one-button Run: start a background flow; snapshot it; SSE replay-then-live. Before planning it stops a workflow that assumes a shared filesystem an HTCondor pool site lacks (`services/shared_fs.py`, #520) |
| GET/PUT | `/api/workflows/projects/{id}/placement?site=` | used on hosted: whose machines the workflow's steps use, Studio's Compute Resources (`elsewhere`, the default) or Bring Your Own Compute (`annex`), stored as a `condor.requirements` site profile in the project's own `pegasus.properties` so a terminal `pegasus-plan` agrees with Run (`services/run_placement.py`). 409 when the workflow sets its own requirements line |
| GET | `/api/workflows/projects/{id}/gpu-steps` | which steps declare a GPU (`gpus` / `request_gpus` profiles), so the Run panel can warn when none is free (#576) |
| DELETE | `/api/workflows/projects/{id}` | **destructive** — deletes a workflow, its runs (cancelled first) and its files |
| POST | `/api/workflows/projects/{id}/rename` | moves the project's folder; refused while a run is planning/running; re-points run records and root Pegasus catalogs (#450) |
| GET | `/api/workflows/projects/{id}/jobs/{job}/files` `/files/info` `/files/content` | the job-files endpoints for a project before any run (only local inputs exist) |

### Chat, Tools, Files, Terminals

| Method | Path | Notes |
|--------|------|-------|
| POST | `/api/chat/stream` | SSE chat (builtin agent loop); brokered so a turn outlives the request |
| GET | `/api/chat/turn` `/turn/events` | reattach to an in-flight turn after a reload — snapshot, then SSE replay-then-live |
| POST | `/api/chat/permission` | approve/deny a pending shell command (per-turn gate) |
| GET/PUT | `/api/chat/command-approval` | approval posture |
| * | `/api/tools/{id}/install|uninstall|start|stop` | AI-tool lifecycle |
| * | `/api/files…` | workspace CRUD (path-confined) |
| POST/GET | `/api/files/upload`, `/api/files/upload-limit` | streamed multipart upload; ceiling + free space so the UI can refuse before sending |
| WS | `/ws/terminal`, `/ws/terminal/{tool_id}` | bash / AI-tool PTY |

### Resources (provisioning)

| Method | Path | Notes |
|--------|------|-------|
| GET | `/api/provisioning/status` | pool inventory, queue demand, OSPool, the user's annex status and the `deployment` (`hosted` or `ood`) |
| POST/GET | `/api/provisioning/annex/bundle` | create the Bring Your Own Compute setup bundle (`htcondor annex create`); download it. It leaves Studio by download only |
| POST | `/api/provisioning/annex/close` `/annex/shutdown` | clear leftover annex request jobs; release the nodes now |

### Jupyter

JupyterLab runs **always-on** under supervisord (served at `/jupyter/`);
`GET /api/jupyter/status` reports whether it's up. The studio does not start or
stop it.

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `HOME` | set by KubeSpawner (a Studio directory under Open OnDemand) | user home and workspace root; Studio's own state lives in `$HOME/.studio` |
| `STUDIO_WORKSPACE_DIR` | `$HOME` | workspace root: projects in `workflows/`, uploads in `data/` (`workspace.py`). `$HOME/work/workflows` from before #401 is still listed, never written |
| `STUDIO_SCAN_DIR` | `$HOME` | root scanned for `braindump.yml` runs; users extend it via `/api/settings/workflow-scan` |
| `KNOWLEDGE_ROOT` | set by the image | knowledge tree path. Unset falls back to the marketplace checkout, then the legacy `/opt/pegasus-ai/knowledge`. Set it for local dev, where neither exists. |
| `KNOWLEDGE_MARKETPLACE` | (unset) | pin the marketplace checkout. Unset prefers the user's `~/.local/share/pegasus-ai/marketplace`, then the image-baked `/opt/pegasus-ai/marketplace` |
| `STUDIO_KNOWLEDGE_GIT_TIMEOUT` | `20` | Seconds before a knowledge-base `git` call (status / fast-forward) is abandoned |
| `KNOWLEDGE_OVERLAY` | `/opt/pegasus-ai/overlay` | Studio-specific context composed on top of the portable Pegasus guide |
| `STUDIO_ALLOWED_ORIGINS` | (empty) | extra origins allowed to read responses **and to make writes** (same-origin always allowed) |
| `STUDIO_HUB_AUTH` | set by the entrypoint | `1` when the JupyterHub OAuth gate is on (see Request gates); `0` forces it off |
| `STUDIO_DEPLOYMENT` | derived | `hosted` or `ood`; derived from whether an administrator catalog (`STUDIO_SITE_CATALOG_FILE` / `STUDIO_SITE_CATALOG`) is set. Decides which Resources page and run choices are shown |
| `STUDIO_TRUST_AUTH_HEADER` | `0` | trust `X-Auth-User` for display identity (opt-in) |
| `STUDIO_LLM_GATEWAY_URL` | internal | base URL for the zero-config `gateway` provider; an empty value means no gateway: no default provider, the preset is not offered, and the chat engine's config declares every saved provider with no default model (#498, #505) |
| `STUDIO_SECRET_KEY` / `STUDIO_SECRET_KEY_FILE` | (unset) | master secret used to encrypt stored API keys / registry password at rest (S7). Unset → stored plaintext with a warning. Must come from a sealed secret, **not** the PVC. |

Provider API keys are stored in the DB via the app (not env). See `main.py` /
`llm/providers.py` for the full set of `STUDIO_*` toggles.

## Container Deployment

In the container, nginx (**port 8888** by default, the only exposed port)
fronts everything:

```
nginx :8888                       STUDIO_PORT
├── /          → Next.js frontend (:3000)   STUDIO_WEB_PORT
├── /api/, /ws/ → studio-api      (:8080)   STUDIO_API_PORT
└── /jupyter/  → JupyterLab       (:8889)   STUDIO_JUPYTER_PORT
                  opencode serve  (:4096)   STUDIO_OPENCODE_PORT
```

**Every listener is configurable**, because the image no longer only runs
one-per-pod. Under Open OnDemand it runs on a shared host, and with Apptainer it
does not get its own network namespace by default, so two users' sessions would
bind the same ports on the same machine. Set the variables above, or set
`STUDIO_ALLOC_PORTS=1` to have the entrypoint pick a free port for each of the
four internal listeners. The front port is never auto-allocated: whatever
publishes it has to know it in advance.

`STUDIO_BIND_ADDR` (default `127.0.0.1`) is the internal bind address for
uvicorn and Next.js (JupyterLab and opencode already bind loopback via their own
flags). nginx is unaffected — its `listen` carries no address, so it still
answers on all interfaces and the front port stays reachable. Set it to
`0.0.0.0` only if something must reach a backend without going through nginx. `STUDIO_RUNTIME_DIR` (default `/tmp/studio-runtime-<uid>`)
holds the rendered nginx configs, pid files, the supervisor socket and nginx's
temp dirs; the uid suffix is what keeps two sessions on one host apart.

### Session token gate (shared hosts)

Off by default, and off is exactly today's behaviour. Set `STUDIO_AUTH_TOKEN`
(Open OnDemand's `before.sh.erb` generates one the way it generates any
batch-connect password) and four things switch on together, because any one of
them alone is a gate with a door beside it:

1. the proxy 403s every request without the `studio_session` cookie, and only
   `GET /auth?token=…` sets it — that is the URL the Connect button points at;
2. the internal services bind loopback (already the default);
3. JupyterLab gets a real token, injected by the proxy as an `Authorization`
   header so the browser flow is unchanged — an empty token means any local
   user reaching its port gets terminals as the session owner;
4. studio-api requires a proxy-stamped `X-Studio-Internal` secret
   (`security.InternalSecretMiddleware`), so hitting uvicorn directly 403s.

Point 4 is the one that is easy to skip and cannot be. On a shared host a
loopback bind is **not** a boundary between local users — it separates hosts,
not accounts — so the cookie gate on the front port protects nothing on its own.

`STUDIO_URL_PREFIX` (optional, e.g. `/node/<host>/<port>`) places the gate's
`/auth` location and keys the prefix locations in
`docker/nginx/prefix-proxy.conf.tmpl`, which nginx strips before proxying;
unset, that file renders to a comment. The rendered gate fragments hold this session's secrets and are written
0600 inside the 0700 runtime dir.

`STUDIO_AUTH_TOKEN` is deliberately **not** exported to any child process. It
is the browser's session credential, it is needed only while rendering, and
every supervisord child — including the chat engine's tool shells and the
user's terminals — would otherwise carry it in its environment.

#### Known residual exposure (#229)

**The Next.js upstream is not gated.** Every location proxying to studio-api
carries the `X-Studio-Internal` stamp and every JupyterLab location carries the
token, but the web locations carry neither, so a second local user on a shared
host can still fetch pages from the Next.js port directly. This is recorded
rather than fixed, for the reason the measured probe found: what they get is the
UI shell — HTML, the client bundle, CSS. The app is client-rendered, so the data
arrives through `/api`, which refuses them.

Next's own `/api` and `/ws` rewrites are dev-only (`next.config.ts`), so the
Next port is not a forwarder to studio-api either. Two caveats worth keeping
honest about that reasoning:

- No server component calls studio-api today: the one that did,
  `lib/server/ui-prefs.ts` (the welcome redirect's SSR read of
  `/api/settings/ui`), went with that redirect. supervisord still hands
  studio-web `API_URL` and the session's `STUDIO_INTERNAL_SECRET` for the next
  server-side caller, and the moment one exists the Next port serves whatever
  it fetches to any local user.
- Closing this properly means rejecting unstamped requests in `middleware.ts`.
  Its matcher now covers `/user/:path*`, `/api`, `/ws` and `/jupyter`, but only
  to strip the hub prefix and inject the dev-only `HUB_COOKIE`; it rejects
  nothing. Making it reject touches every route on the hosted path, which is
  more risk to production than the finding carries on its own. Revisit if the
  web upstream ever starts serving real user data during SSR.

Because `/etc` is not writable by the injected uid, the nginx configs are
**templates** (`docker/nginx/*.tmpl`) rendered per session by the entrypoint;
nginx is started with `-c` against the result. `entrypoint.sh --render-only`
renders and exits, which is how the image build runs `nginx -t` over them.

Supervised by supervisord, whose config takes those ports through its own
`%(ENV_VAR)s` expansion. `docker/entrypoint.sh`:

1. Decides whether the JupyterHub OAuth gate is on (`STUDIO_HUB_AUTH`) and
   renders the nginx templates; creates `~/workflows` and `~/data`.
2. Clones the image's marketplace seed into
   `~/.local/share/pegasus-ai/marketplace` if the user has no checkout.
3. On **first launch only** (marker `~/.studio/.initialized`), runs
   `timeout 60 init_db.py`.
4. Writes `~/AGENTS.md` (the Studio overlay plus the portable Pegasus guide)
   whenever it is absent, and `pegasus.agent.url` into
   `~/.pegasus/pegasus.conf` if missing.
5. Points `~/.pegasusrc` at Studio's site catalog (site `compute`), or at the
   administrator's (`STUDIO_SITE_CATALOG_FILE` / `STUDIO_SITE_CATALOG`), if it
   names none; tightens permissions on `~/.studio`, tool config dirs and
   `~/.pegasusrc`; generates the OpenCode server password.
6. Backgrounds `propagate_llm.py` (hard-timeout'd) to sync tool configs.
7. `exec supervisord`, which starts nginx, studio-api (uvicorn), studio-web,
   JupyterLab and `opencode serve`.

## Collecting early-user feedback

`scripts/collect_feedback.py` gathers the feedback program's data from every
user's Studio database into CSVs. It is standard-library Python so it runs on
the HTCondor access point (which mounts the homes) with the system interpreter
and no venv:

```bash
sudo python3 scripts/collect_feedback.py --homes /home --out ./feedback-$(date +%F)
# optionally: --since 2026-10-01
```

It looks for `<home>/<user>/.studio/studio.db` (falling back to the legacy
`<home>/<user>/work/.studio/studio.db`), opens each **read-only**, and writes
`funnel.csv` (onboarding milestones + `preexisting_workspace`), `profiles.csv`,
`message_feedback.csv`, `episodes.csv` and `giveups.csv`. Tables or settings an
older image lacks yield no rows; a locked or unreadable database is reported on
stderr and skipped. `--since` filters each file on its own timestamp (the
funnel on `first_seen`, profiles on `answered_at`). The printed summary
includes the median time to first success, over new workspaces only:
pre-existing ones are excluded, as `services/onboarding_metrics.py` explains.

## Supported LLM Providers

`gateway` (the zero-config default, points at the deployment's LLM gateway),
`anthropic`, `openai`, `google`, `ollama`, and `custom` (user-specified base
URL; any number of saved custom endpoints). Keyed presets are validated at save time; the
gateway/ollama/custom endpoints may be keyless. With no gateway configured
(`STUDIO_LLM_GATEWAY_URL=""`, the Open OnDemand image's default) there is no
`gateway` preset and no default; users add their own provider and key.
