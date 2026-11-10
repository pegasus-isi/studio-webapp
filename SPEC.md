# PegasusAI Studio - Design Specification

This document describes the current V1 implementation: a single-user Pegasus
workflow studio packaged as one container and normally spawned by JupyterHub,
KubeSpawner, or Open OnDemand.

## Goals

- Replace the JupyterLab-only Pegasus workbench with a workflow-focused web UI.
- Keep deployment simple: one container per user, one persistent workspace.
- Provide workflow generation, planning, submission, monitoring, and diagnostics.
- Let users install AI coding tools on demand while sharing the same Pegasus
  knowledge base across tools.
- Keep identity outside the container: the spawner (JupyterHub, Open OnDemand)
  says who the user is. The container has no login or accounts of its own, but
  it does check every request against that identity (see Authentication).

## Non-Goals For V1

- Multi-user state inside one Studio process.
- Built-in login, user accounts, or authorization rules. (Studio does keep a
  session cookie under JupyterHub, issued only after the hub's OAuth says who
  the user is.)
- PostgreSQL/Redis/Helm-based platform services.
- JupyterLab RTC collaboration or `jupyter-ai` inside the notebook server.

## Runtime Architecture

```
Browser
  |
JupyterHub / Open OnDemand / ingress
  |
nginx :8888                            STUDIO_PORT
  |-- /          -> studio-web  :3000  STUDIO_WEB_PORT
  |-- /api/*     -> studio-api  :8080  STUDIO_API_PORT
  |-- /ws/*      -> studio-api  WebSocket PTYs
  `-- /jupyter/* -> JupyterLab  :8889  STUDIO_JUPYTER_PORT
                    opencode    :4096  STUDIO_OPENCODE_PORT
```

Those numbers are defaults; every listener is configurable, and
`STUDIO_ALLOC_PORTS=1` picks free ones per session. That exists because the
image is not only run one-per-pod: under Open OnDemand it runs on a shared
host, and Apptainer gives it the host network namespace unless it can create a
new one, so fixed ports are two users colliding. Only nginx answers on all
interfaces — the rest bind `STUDIO_BIND_ADDR`, `127.0.0.1` by default.

The container entrypoint allocates the ports, renders the nginx configs from
`docker/nginx/*.tmpl` into `STUDIO_RUNTIME_DIR` (`/etc` is not writable by the
injected uid), initializes the workspace and then execs `supervisord`. Process
definitions live in `docker/supervisor/supervisord.conf`, which takes the ports
through supervisord's own `%(ENV_VAR)s` expansion.

| Program | Command | Purpose |
|---------|---------|---------|
| `studio-api` | `uvicorn main:app --host $STUDIO_BIND_ADDR --port $STUDIO_API_PORT` | FastAPI backend |
| `studio-web` | `node server.js` (`PORT`, `HOSTNAME`, `API_URL` from the environment) | Next.js standalone server |
| `jupyter` | `/etc/supervisor/scripts/jupyter.sh` | Always-on JupyterLab |
| `opencode` | `/etc/supervisor/scripts/opencode.sh` | Chat engine, loopback + per-user password |
| `nginx` | `nginx -c $STUDIO_RUNTIME_DIR/nginx.conf -g "daemon off;"` | Public reverse proxy |

`supervisord` restarts crashed services and keeps logs on stdout/stderr for the
container runtime.

## Authentication

Studio has no login page or user accounts. Who the user is comes from the
spawner, and nginx checks it on every request, in one of two modes the
entrypoint picks at start:

| Deployment | Mode | How a request is checked |
|---|---|---|
| JupyterHub / KubeSpawner | `STUDIO_HUB_AUTH=1` (#592), on when the hub's OAuth variables (`JUPYTERHUB_API_TOKEN`, `JUPYTERHUB_CLIENT_ID`, `JUPYTERHUB_OAUTH_CALLBACK_URL`, `JUPYTERHUB_USER`) are set and the token gate is not | A server-level nginx `auth_request` (`docker/nginx/hub-auth-server.conf.tmpl`) asks studio-api's `/_hub_auth/check` about every request. Without a valid session cookie, a page load is sent through the hub's OAuth flow and anything else (API, WebSocket, non-GET) gets 401. The callback accepts only `JUPYTERHUB_USER` or a user holding one of `JUPYTERHUB_OAUTH_ACCESS_SCOPES` (an admin), then sets an HMAC-signed, `HttpOnly`, `SameSite=Lax` cookie scoped to the server's prefix, valid 8 hours, keyed from the pod's hub token |
| Open OnDemand | Session token gate, `STUDIO_AUTH_TOKEN` (#218) | The proxy's cookie gate, loopback binds, a JupyterLab token, and an `X-Studio-Internal` secret studio-api demands (see Open OnDemand below) |
| Local (`local-test-instance.sh`, dev) | Neither | Unauthenticated; for a single person's machine |

This is what `jupyterhub-singleuser` does with HubOAuth, done with plain HTTP
because the image has no `jupyterhub` package; the entrypoint runs
supervisord instead of that server, so before #592 nothing checked. In every
mode studio-api also refuses cross-origin writes (`CrossOriginWriteMiddleware`
in `security.py`), since a cookie rides along on any request a page makes.

All users of one hub share an origin (`/user/<name>/`), so one user's served
page is same-origin with another's. That is a JupyterHub property fixed only
by per-user subdomains, which this deployment does not use. `SECURITY.md`
describes the security model per deployment.

## Container Stack

- Base image: `debian:trixie-slim` — the same distro as the HTCondor access
  point, so the Pegasus venv path baked into submit files matches on both
  sides (#334). Override with `--build-arg DEBIAN_SUITE=`.
- Supported platform: `linux/amd64`.
- Python: single system Python 3.13 (whatever the suite ships). No
  conda/Miniforge layer. pip is bootstrapped from a released wheel pinned by
  version and sha256 (`PIP_VERSION`, `PIP_WHEEL_URL`, `PIP_WHEEL_SHA256`, #568),
  not from `get-pip.py`, whose checksum changes with every pip release.
- Node.js: 20.x for the Next.js server and npm-installed AI tools. The frontend
  build runs with `NEXT_TELEMETRY_DISABLED=1`.
- Pegasus WMS and HTCondor: installed from native DEB packages. Pegasus comes
  from the 6.0 development channel, which republishes every nightly under one
  version, so bumping `PEGASUS_REFRESH` is what pulls the current build.
- Apptainer: installed from the upstream `.deb`, with `singularity` compatibility.
- JupyterLab: installed via pip without `jupyter-ai` or collaboration extensions.
- OpenCode: bundled in the image (pinned npm version) and registered as an
  installed tool at first boot, so the OpenCode chat engine and
  `opencode attach` work out of the box. Other AI tools install on demand.
- Process supervision: `supervisor` Python package.
- npm global prefix: `/opt/npm-global`.

The image intentionally avoids a fixed user. KubeSpawner or the local container
runtime supplies the UID and `$HOME`.

## Open OnDemand / Apptainer Packaging

[`ood-app/`](ood-app/) builds a second image, `ood-app/studio-el9.def`, for
[Open OnDemand](https://openondemand.org/) instead of K8s/KubeSpawner. It is
the same product — studio-api, studio-web, JupyterLab, HTCondor, Pegasus WMS,
nginx, supervisord, the session token gate — reusing `docker/`, `studio-api/`,
and `studio-web/` as-is, on a different base:

| | K8s/Docker | Open OnDemand/Apptainer |
|---|---|---|
| Base image | `debian:trixie-slim` (matches the HTCondor AP's distro, #334) | `rockylinux:9` (matches HPC clusters, which skew RHEL-family) |
| Packaged as | multi-stage `Dockerfile` | single-stage `ood-app/studio-el9.def` |
| Spawned by | KubeSpawner | the batch-connect app, [pegasus-isi/bc_pegasus_ai_studio](https://github.com/pegasus-isi/bc_pegasus_ai_studio) |
| `$HOME` is | the user's PVC, which exists only for Studio | a Studio directory (`~/pegasus-studio` by default), never the user's real home |
| Front port published by | the K8s Service | the OOD portal's own allocator |

Three things are true of this deployment that are not (yet) true of K8s:

- **The container does not get its own network namespace.** Apptainer gives it
  the *host* network namespace by default, and Open OnDemand runs several
  users' sessions on one shared compute node — so every listener has to be
  configurable (`STUDIO_ALLOC_PORTS=1`, see Runtime Architecture above) and
  the front proxy port needs its own isolation, which is what the session
  token gate (#218, `STUDIO_AUTH_TOKEN`) is for. Behind the proxy, studio-api
  refuses any request without the proxy-stamped `X-Studio-Internal` secret,
  and JupyterLab requires its token. The one upstream left reachable by other
  local users on loopback is studio-web (Next.js), deliberately (#229). It
  serves the UI shell (HTML, JS, CSS) and nothing else: every byte of user
  data comes from `/api`, which 403s without the secret, and the `/api` and
  `/ws` rewrites in `next.config.ts` exist only in dev, so Next never
  forwards a request onward. studio-web is still given the secret
  (`STUDIO_INTERNAL_SECRET` in `supervisord.conf`) for server components that
  call studio-api on loopback, but none does today: the one that did,
  `lib/server/ui-prefs.ts` deciding whether the app root showed the welcome
  page, went when Studio started opening on Home, so a local user learns
  nothing through it. Gating Next as well would widen `middleware.ts` to every
  route on the K8s path, more risk than nothing is worth. If a server
  component ever starts calling studio-api or returning user data, gate it.
- **The URL isn't served at the app root.** Open OnDemand's own reverse proxy
  assigns each session a path like `/node/compute-1/20153` (`STUDIO_URL_PREFIX`),
  a different shape from KubeSpawner's `/user/<name>` convention that the nginx
  templates originally only knew how to route. `docker/nginx/prefix-proxy.conf.tmpl`
  generalizes this: locations keyed on the literal, per-session prefix value,
  rendered inert (a comment) when unset — so the K8s path is unaffected.
- **The home directory is not Studio's.** Everything Studio writes is
  `$HOME`-relative: its database, the marketplace checkout, `~/.pegasusrc`,
  the generated `CLAUDE.md`/`AGENTS.md`, and the configs the knowledge
  adapters write for codex, opencode and claude. On a cluster `$HOME` is the
  user's real home, where their own setup of those tools already lives (#482).
  Rather than re-root ~30 `Path.home()` call sites and every subprocess, the
  batch-connect app runs the image with `apptainer run --home "$STUDIO_HOME"`,
  which moves both `$HOME` and the passwd entry, so the app code is unchanged
  and the K8s model ("Studio owns its home") holds. The app refuses the real
  home or any ancestor of it, and links in `.condor/tokens.d` and `.gitconfig`
  so submission and git identity keep working. The real home stays
  bind-mounted for terminals.
  `docker/entrypoint.sh` exports `HOME=$STUDIO_HOME` itself if a launch set
  only the variable. The directory must be on storage the HTCondor submit host
  also mounts, since remote planning passes `HOME` through.

**Why a rebuild rather than `apptainer run docker://...` against the existing
image**: the intent is for HTCondor to submit workflow jobs onward into the
target cluster's own Slurm queue (a local schedd, using HTCondor's Slurm/BLAHP
grid support) rather than only the shared remote AP (`ap.pegasus-ai.org`) that
K8s deployments use. That means bind-mounting the cluster's own Slurm client
tools and munge authentication into the container, which only works reliably
if the container's OS matches the host's closely enough to be library-ABI
compatible — hence Rocky9, not Debian, for this build. **This is not built
yet**: `docker/htcondor/90-studio.conf` is installed unmodified, so today an
OOD session is still a remote-AP HTCondor client like everything else; the
Rocky9 base is a prerequisite for local Slurm submission, not evidence it
exists.

**The batch-connect app** lives in its own repo, [pegasus-isi/bc_pegasus_ai_studio](https://github.com/pegasus-isi/bc_pegasus_ai_studio): form, Slurm
submit options, the job script that runs this image, and the Connect view. A
site clones it into `/var/www/ood/apps/sys/` and points it at a published
`.sif`. An administrator configures every session through the app's
`site.env`: the site catalog Studio uses for `compute`
(`STUDIO_SITE_CATALOG_FILE`, which replaces `pegasus-studio.yml`, #496), an
LLM gateway if the site runs one (otherwise none, and users bring their own
provider and key; the EL9 image defaults `STUDIO_LLM_GATEWAY_URL` to empty,
#498), and the HTCondor access point. Validated end to end on a FABRIC Slurm
cluster on 2026-10-13, with workflow jobs reaching Slurm through BLAHP on the
access point. See `ood-app/README.md`.

## Workspace

The workspace root is `$HOME` (#401; it used to be `$HOME/work`, still listed
read-only for old projects); application code also supports
`STUDIO_WORKSPACE_DIR` for service-level workspace resolution.

The workspace contains:

- Workflow projects and generated files.

Studio's own SQLite state lives at `$HOME/.studio/studio.db`, outside the
workspace: it holds settings, LLM configuration and history, which should not
be inside the directory users are told to work in. A database at the old
`~/work/.studio` path is moved on first start.

Generated tool configuration (`CLAUDE.md`, `AGENTS.md`, `.mcp.json`,
`.opencode/`, `.agents/`) is written to **`$HOME`**, not the workspace: these
tools search from the directory they start in and upwards, and terminals start
at `$HOME`, so a copy in the workspace existed without ever being found.

JupyterLab is rooted at `$HOME` for the same reason — a browser that could not
see above the workspace made those files look absent, and workflow runs are
discovered by scanning all of `$HOME` anyway.

In Kubernetes, mount the user's PVC at `$HOME` or set `STUDIO_WORKSPACE_DIR` to
the mounted workspace path.

## Frontend

`studio-web/` is a Next.js 15 App Router application using React 19, TailwindCSS,
Zustand, TanStack Query, xterm.js, and lucide-react.

Primary routes:

| Route | Nav label | Purpose |
|-------|-----------|---------|
| `/` → `/home` | Home | Where Studio opens: welcome, four ways in, an opener gallery, an attention strip for runs that need it, and the guided tour (every login until opted out). The former `/welcome` introduction moved to the JupyterHub login page; `/welcome` now redirects to Home |
| `/workflows` | Analyses | Workflow projects, runs, actions and details; the Run panel (Choose where to run the workflow, GPU notes); where each step ran; long-running-step warnings; the graph (click a step for its files; optional file nodes); Results with sandboxed previews; rename/delete |
| `/data` | Data | Upload input files into the workspace |
| `/marketplace` | Examples | Browse workflows sourced live from GitHub: repos in the configured orgs tagged `pegasus-studio-example`, enriched from the PegasusHub registry; "Add" git-clones one into the workspace |
| `/provisioning` | Resources | A section menu: Overview (pool inventory, workflow demand, pressure findings, every capacity number), OSPool (capacity and the user's OSPool project name), and per deployment either Bring Your Own Compute (hosted: create, download and shut down the user's HTCondor annex, cluster presets, GPU nodes) or Where workflows run (Open OnDemand: the cluster's catalog and the user's allocation) |
| `/notebooks` | Notebooks | Opens JupyterLab at `/jupyter/lab` |
| `/settings` | Settings (page title "Application Settings") | Notifications; Pegasus Options (Simplified Run, guided tour, where planning runs, workflow secrets, container registry, workflow discovery scan roots); Chat (engine, shell-command approval, chat-text sharing); Feedback & privacy; LLM Providers and Tools under Advanced |

A chrome-less `/chat` page renders the same chat for embedding into JupyterLab
(iframe, origin-checked postMessage handshake). The rail, the account menu and
the command palette also link out to the PegasusAI website (`pegasus-ai.org`).

The frontend consumes REST APIs, SSE chat streams, and WebSocket PTY sessions.

## Backend

`studio-api/` is a FastAPI app with async SQLite, structlog JSON logging, and
service classes for tool installation, process management, workflow discovery,
workflow submission, and event reading.

Router groups:

| Router | Responsibility |
|--------|----------------|
| `health` | Basic and detailed container health |
| `hub_auth` | JupyterHub OAuth gate (#592): `/_hub_auth/check`, `/login`, `/callback`, mounted outside `/api` and reached only through nginx (see Authentication) |
| `settings` | User settings summary/update; UI preferences, telemetry consent, profile; container registry, workflow-scan roots, where planning runs, workflow secrets, and the compute site catalog and allocation |
| `llm` | Provider config, validation, provider activation |
| `knowledge` | Skills, agents, templates, examples |
| `tools` | Tool install/uninstall/start/stop/status |
| `files` | Workspace file operations |
| `terminal` | Browser bash PTY |
| `ai_terminal` | AI tool PTYs |
| `workflows` | Project discovery, params, generate, plan, submit, monitor, analyze, estimate; per-workflow placement and GPU steps; where each step ran and slow steps; outputs and a job's files with previews; project rename |
| `chat` | The builtin agent loop (direct provider calls with tool use; Anthropic, OpenAI-compatible and Responses API), still selectable per user. Per-turn shell-command approval |
| `opencode` | Verbatim proxy of `/api/opencode/*`, SSE included, to the loopback `opencode serve` that backs the chat panel — the default engine since #403 (`chat_engine` UI preference) |
| `mcp` | The Pegasus tools served to the OpenCode engine at `/api/mcp` (stateless Streamable HTTP), read from `services/chat_tools.py` |
| `feedback` | 👍/👎 on chat replies (`message_feedback`; no message text), also sent to the OTLP collector when one is configured (`services/rating_telemetry.py`, #608) |
| `episodes` | "Did the AI help?" failure episodes: nudges, help cards, answers, give-ups |
| `jupyter` | Status only (JupyterLab is always-on; the `/notebooks` page frames it) |
| `dashboard` | Aggregate landing-page data (projects, runs, resources, versions) |
| `provisioning` | HTCondor pool inventory + queue demand + pressure + OSPool capacity, the deployment kind, and on hosted the user's annex: create the setup bundle, download it, close requests, shut it down |
| `notifications` | Notification feed + prefs; SSE stream for live toasts |
| `marketplace` | The Examples catalog: topic-tagged GitHub repos enriched from the PegasusHub registry; "Add" git-clones into the workspace |
| `pegasus_bot` | Same-origin proxy to the cluster-internal PegasusAI Bot server (search/feedback) |

JupyterLab runs always-on under supervisord; the API does not start or stop it,
only reports status (`GET /api/jupyter/status`).

## Workflow Model

Studio discovers workflow projects and Pegasus runs under the workspace.

Project actions:

- **Generate** runs a project's `workflow_generator.py`.
- **Plan** runs `pegasus-plan -s <site> -o <output-site>` (no submit) and
  captures the planned run directory.
- **Run** submits that planned dir via `pegasus-run`, falling back to a full
  `pegasus-plan --submit` when there's no reusable planned dir.
- **Build** runs `apptainer build` on the project's `Apptainer/<Name>.def`,
  writing the `.sif` beside it so the Transformation Catalog can reference it as
  `file:///abs/path/X.sif` with `image_site="local"`, and streaming the log over
  SSE. A registry is optional: when one is configured the `.sif` is also pushed
  over ORAS, but a workflow does not need it.

Before rendering action forms, Studio introspects each project's
`workflow_generator.py` `argparse` definition and builds a parameter form for
that project.

The one-button Run (`services/run_flow.py`) asks **Choose where to run the workflow**:

- The site comes from what planning can resolve (`services/execution_sites.py`:
  the project's `sites.yml`, a hosted catalog named in `~/.pegasusrc`, then the
  deployment's default). On hosted, "This machine" (`local`) is not offered
  unless a workflow already has it saved; under Open OnDemand it is labelled
  "This Studio session's node".
- On hosted, the default is **Studio's Compute Resources** (Studio's pool and,
  for steps with a project name, the OSPool), and the alternative is **Bring
  Your Own Compute** (only the user's annex nodes). The choice is per workflow,
  written into the project's own `pegasus.properties` as a site
  `condor.requirements` profile (`services/run_placement.py`), so a
  `pegasus-plan` from a terminal plans the same thing as Run. See
  [`docs/annex.md`](docs/annex.md).
- GPUs are the workflow's to request, per job or transformation (a Pegasus
  `gpus` or HTCondor `request_gpus` profile). The panel lists the steps that
  ask (`services/workflow_gpus.py`) and says when no machine has a GPU free.
- After generate and before plan, a run bound for an HTCondor pool is stopped
  if the workflow assumes a shared filesystem the pool lacks (`sharedfs`,
  bypassed input staging, a shared scratch dir, container mounts from the
  home), with a message naming both sides (`services/shared_fs.py`, #520).

Run monitoring reads the stampede DB:

- Studio reads the run's stampede DB (`services/stampede_reader.py`), which
  pegasus-monitord writes for every run, for run status, job tables, progress,
  and SSE updates. It does not start a monitor itself. That DB also says where
  each step ran (the kickstart host), shown as "Ran on" and grouped as Studio
  pool, access point or a cluster's domain.
- A legacy JSONL event log already in the run directory
  (`workflow-events.jsonl` from the standalone `workflow-monitor --serve`, or
  `wfevents.jsonl` from the monitord `wfevents` plugin) is read in preference,
  but nothing in the image produces one. Pegasus 6.0's native
  `pegasus-monitor` writes a `workflow-events.jsonl` in a different format;
  the reader recognises and skips it, or the run would read "planning" with
  zero jobs (`services/event_reader.py`, #656).
- A step running much longer than the same step usually takes, in this run or
  earlier runs of the workflow, is flagged (`services/slow_steps.py`, #586).
- Diagnostics use Pegasus CLI tools such as `pegasus-analyzer`.

Jobs run in Apptainer/Singularity containers on the configured HTCondor site.

## AI Tool Marketplace

Tools are installed on demand, usually with `npm install -g` into
`/opt/npm-global`.

| Tool | Type | Notes |
|------|------|-------|
| PegasusAI Chat | Built-in web chat | Always available; not an installable tool |
| OpenCode | Terminal/web | Open-source coding assistant; bundled, and the chat's default engine |
| Antigravity CLI | Terminal | Google's agentic coding CLI (installed by shell script, not npm) |
| Codex CLI | Terminal | OpenAI terminal coding agent |
| Crush | Terminal | Charmbracelet's multi-provider terminal agent |
| Claude Code | Terminal | Anthropic CLI with Pegasus knowledge |

Tools that run on the user's own LLM subscription or API tokens carry a "Your
subscription" badge (#515).

Tool lifecycle:

```
available -> installed -> running
                 |          |
                 `----------'
```

Installing a tool writes or updates its workspace config through a knowledge
adapter. Starting a terminal tool creates a WebSocket PTY in the workspace.

## Knowledge Layer

Knowledge is authored once, in
[pegasus-isi/claude-plugin-marketplace](https://github.com/pegasus-isi/claude-plugin-marketplace),
and checked out into the runtime image at `/opt/pegasus-ai/marketplace`, which
tracks `main` (`KNOWLEDGE_REF` overrides it for a fixed build). That copy is a
template: the entrypoint clones it into the user's own
`~/.local/share/pegasus-ai/marketplace`, which fast-forwards to `origin/main` at
each start and is what tools read. `.git` is kept, so a session's skills can be
updated or rolled back in a running pod without a rebuild, and the checkout's
SHA — reported in Settings → Tools — is the answer to which skills are in use.
Studio does not keep a copy; CI fails if one reappears under `knowledge/`.

| Directory | Contents |
|-----------|----------|
| `skills/<name>/SKILL.md` | Skill prompts, with name/description in frontmatter |
| `agents/*.md` | Specialist subagent prompts |
| `assets/templates/` | Workflow generator, wrapper, Apptainer definition, README templates |
| `assets/examples/` | Curated Pegasus workflow examples |
| `references/PEGASUS.md` | The portable Pegasus development guide |
| `.mcp.json` | MCP server definitions |

`knowledge/overlay/STUDIO_ENV.md` in this repo is the one piece that stays here:
Studio-specific environment context (what persists, where runs are discovered)
that does not belong in a plugin someone installs on a laptop. Generated context
is the overlay followed by the portable guide.

`studio-api/knowledge/layout.py` resolves the tree — `KNOWLEDGE_ROOT` env (set by
the image), then the checkout, then the legacy `/opt/pegasus-ai/knowledge` path —
and is the only module that knows the on-disk shape. `knowledge/seed.py` writes
the Claude Code plugin seed directory that points at it.

Adapters translate the shared knowledge into each tool's native format:

| Adapter | Output |
|---------|--------|
| Claude Code | `CLAUDE.md`, `.mcp.json`, Claude plugin/skill files |
| OpenCode | `AGENTS.md`, `opencode.json`, `.opencode/skills`, `.opencode/agents` |
| Codex CLI | `AGENTS.md`, `~/.codex/config.toml`, `.agents/skills/`, `.codex/agents/` |
| Crush | `AGENTS.md`, `~/.config/crush/crush.json` |
| Antigravity | `.antigravity/context.md`, `settings.json` |
| Web Chat | Server-side in-memory context |

## LLM Configuration

Provider presets are `gateway` (the zero-config default on the hosted
deployment: an internal OpenAI-compatible gateway, so chat/tools work with no
keys), `anthropic`, `openai`, `google`, `custom` (any OpenAI-compatible
endpoint; several may coexist), and `ollama` (`studio-api/llm/providers.py`).

Where there is no gateway (`STUDIO_LLM_GATEWAY_URL` set to empty, the Open
OnDemand image's default unless a site names one), there is no default: each
user adds a provider with their own key, every saved provider is offered in the
chat's model picker, and chat says so when none exists. A shared key is never
injected into sessions (#498, #505).

Configuration is done at runtime on the Settings page and stored in SQLite.
Keys are write-only over the API — responses report only whether a key is set —
and are encrypted at rest when `STUDIO_SECRET_KEY` is provided (see Database).
The tool configs that carry keys (`opencode.json`, Codex's `config.toml`,
`crush.json`) are written 0600 (#600).

The built-in chat supports both OpenAI Chat Completions and the OpenAI Responses
API and falls back when an endpoint requires Responses.

## Database

SQLite lives at `$HOME/.studio/studio.db`, outside the workspace.

Tables:

| Table | Purpose |
|-------|---------|
| `tool_installations` | Installed/running tool state |
| `llm_config` | Active LLM provider, model, key, base URL |
| `provider_configs` | Saved multi-provider configs (name, key, base URL, active flag) |
| `workflow_runs` | Discovered workflow run metadata |
| `chat_messages` | Built-in chat history + 👍/👎 feedback |
| `message_feedback` | 👍/👎 on OpenCode replies, keyed by OpenCode session/message id; no message text |
| `notifications` | Notification feed rows |
| `notification_log` | Durable dedup-key ledger (survives clear, so fire-once guards hold) |
| `app_settings` | Key/value app settings (e.g. container registry, chat engine) |
| `workflow_secrets` | Credentials a workflow's generator needs; encrypted, never returned over the API |
| `feedback_episodes`, `feedback_giveups` | "Did the AI help?" failure episodes and give-up tags |

The DB is a single-user store behind the request authentication above.
`~/.studio` is created 0700 and the DB 0600, and the entrypoint re-applies
those modes on every start, since homes are 0755 under a 022 umask (#600). Stored
secrets (LLM API keys, the registry password) are additionally encrypted at
rest when `STUDIO_SECRET_KEY` is set — the key is derived from that
operator-provided secret, which must live off the workspace PVC so a leak of
the DB file alone doesn't expose them. With no secret configured they fall back
to plaintext (with a warning).

## API Surface

Common endpoints:

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/api/health` | Basic health |
| `GET` | `/_hub_auth/check`, `/login`, `/callback` | JupyterHub OAuth gate, reached only through nginx (hub mode only) |
| `GET` | `/api/health/detailed` | DB, Pegasus, and HTCondor health |
| `GET/PUT` | `/api/llm/config` | Active LLM config |
| `GET` | `/api/llm/providers` | Supported providers |
| `POST` | `/api/llm/validate` | Validate provider/key |
| `GET` | `/api/tools` | Tool catalog and install state |
| `POST` | `/api/tools/{id}/install` | Install a tool |
| `POST` | `/api/tools/{id}/start` | Start a tool |
| `GET` | `/api/knowledge/skills` | List skills |
| `GET` | `/api/files` | List workspace files |
| `GET` | `/api/files/read` | Read a workspace file |
| `POST` | `/api/files/write` | Write a workspace file |
| `POST` | `/api/files/upload` | Upload a file into the workspace (streamed multipart) |
| `GET` | `/api/files/upload-limit` | Upload ceiling + free space, so the UI can refuse before sending |
| `GET` | `/api/workflows` | List workflow runs |
| `GET` | `/api/workflows/projects` | List workflow projects |
| `GET` | `/api/workflows/projects/{id}/params` | Introspect generator params |
| `POST` | `/api/workflows/projects/{id}/generate` | Generate workflow files |
| `POST` | `/api/workflows/projects/{id}/build` | Build & push the project's container image (SSE log) |
| `POST` | `/api/workflows/projects/{id}/plan` | Plan a workflow |
| `POST` | `/api/workflows/projects/{id}/submit` | Submit a workflow |
| `POST` | `/api/workflows/projects/{id}/run-flow` | Start the whole prepare-and-run sequence as one background flow |
| `GET` | `/api/workflows/projects/{id}/run-flow` | Current/most-recent flow, for reattaching after a reload |
| `GET` | `/api/workflows/projects/{id}/run-flow/events` | SSE: full replay of the flow, then live |
| `DELETE` | `/api/workflows/projects/{id}` | **Destructive**: delete a workflow, its runs (cancelled first) and its files |
| `GET/PUT` | `/api/settings/registry` | Container registry + credentials for Build |
| `GET/PUT` | `/api/settings/ui` | Per-user interface preferences (Simplified Run, disclosure state, `tour_on_login`, chat engine, Resources source) |
| `GET/PUT` | `/api/settings/planning` | Where planning runs (automatic, submit host, or this workspace) |
| `GET/PUT/DELETE` | `/api/settings/workflow-secrets` | Workflow credentials (names only are ever returned) |
| `GET/PUT` | `/api/settings/compute-site` | The hosted site catalog and the user's allocation (`RESOURCE_*`), kept in `~/.pegasusrc`; edited on Resources under Open OnDemand |
| `GET/PUT` | `/api/settings/telemetry` | Chat-text sharing consent, with a read-only `decided` |
| `POST` | `/api/workflows/projects/{id}/rename` | Move a project's folder (refused while a run is in progress) |
| `GET/PUT` | `/api/workflows/projects/{id}/placement` | Hosted: Studio's Compute Resources or Bring Your Own Compute for a site, read from and written to the project's `pegasus.properties` |
| `GET` | `/api/workflows/projects/{id}/gpu-steps` | Steps whose job or transformation profiles ask for a GPU |
| `GET` | `/api/workflows/{run_id}/slow-steps` | Steps running much longer than usual |
| `GET` | `/api/workflows/{run_id}/jobs` | Job status |
| `GET` | `/api/workflows/{run_id}/events` | Workflow event stream |
| `POST` | `/api/workflows/{run_id}/analyze` | Run diagnostics |
| `GET` | `/api/workflows/{run_id}/estimate` | Remaining-time estimate (historical mean; shown only when confidence allows) |
| `GET` | `/api/workflows/{run_id}/outputs` | Files a run produced (paginated) |
| `GET` | `/api/workflows/{run_id}/outputs/file` | Preview or download one output file; `?head=1` for the start of a large text/table |
| `GET` | `/api/workflows/{run_id}/graph.json` | DAG with live statuses; `?include_pegasus=true` (executable DAG) or `?files=true` (file nodes) |
| `GET` | `/api/workflows/{run_id}/jobs/{job}/files` | A job's inputs and outputs, located (result / scratch / project) |
| `GET` | `/api/workflows/{run_id}/files/info` `/files/content` | Describe files by name; serve one from its location |
| `GET` | `/api/workflows/{run_id}/diagnosis` | Plain-language failure cards (cause, action, technical detail) |
| `DELETE` | `/api/workflows/{run_id}` | **Destructive**: cancel if running, then remove the run's submit directory from disk |
| `GET` | `/api/dashboard` | Aggregate landing-page snapshot |
| `GET` | `/api/dashboard/onboarding` | Onboarding funnel (first seen → first workflow → first run → first success) |
| `GET` | `/api/provisioning/status` | Pool inventory, demand, pressure findings, OSPool capacity, deployment kind, and (hosted) annex status |
| `POST/GET` | `/api/provisioning/annex/bundle` | Hosted: create the annex setup bundle / download it (attachment only) |
| `POST` | `/api/provisioning/annex/close`, `/annex/shutdown` | Hosted: remove leftover annex requests / release the annex's nodes |
| `POST` | `/api/feedback/message` | Rate an OpenCode reply 👍/👎 |
| `GET` | `/api/marketplace` | Workflow catalog sourced live from GitHub |
| `POST` | `/api/marketplace/{id}/add` | git-clone a catalog workflow into the workspace |
| `POST` | `/api/chat/stream` | Built-in SSE chat (engine-dispatched, brokered so a turn outlives the request) |
| `GET` | `/api/chat/turn` | Whether a turn is in flight, so a reloaded panel can reattach |
| `GET` | `/api/chat/turn/events` | SSE: replay the in-flight turn from its start, then follow live |
| `POST` | `/api/chat/permission` | Approve/deny a pending shell command (per-turn gate) |
| `GET/PUT` | `/api/chat/command-approval` | Approval posture (the engine itself is the `chat_engine` field of `/api/settings/ui`) |
| `GET` | `/api/notifications` | Notification feed (+ `/prefs`, `/stream` SSE) |
| `GET` | `/api/jupyter/status` | JupyterLab status |
| `WS` | `/ws/terminal` | Bash PTY |
| `WS` | `/ws/terminal/{tool_id}` | AI tool PTY |

## Deployment

V1 deployment is one container per user.

Requirements:

- Native `amd64` node or VM.
- Persistent workspace volume.
- A spawner that supplies identity and routing (JupyterHub's OAuth variables,
  or Open OnDemand with the token gate); see Authentication.
- Runtime privileges or cluster configuration sufficient for Apptainer user
  namespaces when workflows execute `docker://` containers.

KubeSpawner should set the runtime UID and `$HOME`. The image does not create or
depend on a named application user.

Images are published by `.github/workflows/deploy.yml` to
`ghcr.io/pegasus-isi/studio-webapp`, tagged by branch: pushes to `develop`
publish `:develop` for testing, and a `develop` → `main` release PR publishes
`:main`, which the hosted deployment pulls with `imagePullPolicy: Always`.
Pull requests build without pushing.

Open OnDemand deployment uses the second image built from
[`ood-app/studio-el9.def`](ood-app/studio-el9.def) instead (see [Open OnDemand
/ Apptainer Packaging](#open-ondemand--apptainer-packaging)); the requirements
above still apply, plus a Slurm cluster whose distro/ABI the container's Rocky9
base is close enough to for bind-mounted host tools to run.

## Future V2 Direction

V2 can move the same API and frontend concepts onto shared infrastructure:

| V1 | V2 |
|----|----|
| SQLite in user workspace | PostgreSQL |
| Identity from the spawner (hub OAuth or OOD token gate), checked per request | CILogon/OIDC in Studio |
| Per-user container | Multi-service Kubernetes deployment |
| Tools inside the user container | Tool sidecars or separate workloads |
| Local `workflow-monitor` process | Shared workflow-monitor service |
