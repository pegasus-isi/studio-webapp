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
- Keep authentication outside the container; the spawner or ingress authenticates
  the user before the container is reachable.

## Non-Goals For V1

- Multi-user state inside one Studio process.
- Built-in login, sessions, or authorization.
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

## Container Stack

- Base image: `debian:trixie-slim` — the same distro as the HTCondor access
  point, so the Pegasus venv path baked into submit files matches on both
  sides (#334). Override with `--build-arg DEBIAN_SUITE=`.
- Supported platform: `linux/amd64`.
- Python: single system Python 3.13 (whatever the suite ships). No
  conda/Miniforge layer.
- Node.js: 20.x for the Next.js server and npm-installed AI tools.
- Pegasus WMS and HTCondor: installed from native DEB packages.
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
  token gate (#218, `STUDIO_AUTH_TOKEN`) is for.
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
`.sif`. See `ood-app/README.md`.

## Workspace

The workspace defaults to `$HOME/work`; application code also supports
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
| `/` | Home | Dashboard: workflow summary, recent runs, resources, quick actions |
| `/workflows` | Workflows | Workflow projects, runs, actions, and details |
| `/marketplace` | Marketplace | Browse workflows sourced live from GitHub (PegasusHub registry + curated Pegasus-ISI); "Add" git-clones one into the workspace |
| `/provisioning` | Resources | Pool inventory, workflow demand, pressure findings, OSPool capacity (read-only) |
| `/notebooks` | Notebooks | Opens JupyterLab at `/jupyter/lab` |
| `/settings` | Settings | LLM providers, chat engine + shell-command approval, Tools (install + launch AI tools / bash terminal), container registry |

A chrome-less `/chat` page renders the same chat for embedding into JupyterLab
(iframe, origin-checked postMessage handshake).

The frontend consumes REST APIs, SSE chat streams, and WebSocket PTY sessions.

## Backend

`studio-api/` is a FastAPI app with async SQLite, structlog JSON logging, and
service classes for tool installation, process management, workflow discovery,
workflow submission, and event reading.

Router groups:

| Router | Responsibility |
|--------|----------------|
| `health` | Basic and detailed container health |
| `settings` | User settings summary/update |
| `llm` | Provider config, validation, provider activation |
| `knowledge` | Skills, agents, templates, examples |
| `tools` | Tool install/uninstall/start/stop/status |
| `files` | Workspace file operations |
| `terminal` | Browser bash PTY |
| `ai_terminal` | AI tool PTYs |
| `workflows` | Project discovery, params, generate, plan, submit, monitor, analyze, estimate |
| `chat` | PegasusAI SSE chat — the builtin agent loop (direct provider calls with tool use; Anthropic, OpenAI-compatible and Responses API). Per-turn shell-command approval. OpenCode was removed as a chat engine in #299 and remains only as a launchable terminal tool |
| `jupyter` | Status only (JupyterLab is always-on; the `/notebooks` page frames it) |
| `dashboard` | Aggregate landing-page data (projects, runs, resources, versions) |
| `provisioning` | HTCondor pool inventory + queue demand + pressure + OSPool capacity (read-only) |
| `notifications` | Notification feed + prefs; SSE stream for live toasts |
| `marketplace` | GitHub-sourced workflow catalog; "Add" git-clones into the workspace |
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

Run monitoring is JSONL-driven:

- On submit, Studio starts `workflow-monitor --serve`.
- `workflow-monitor` writes `workflow-events.jsonl`.
- Studio tails/parses that JSONL for run status, job tables, progress, and SSE
  updates.
- Diagnostics use Pegasus CLI tools such as `pegasus-analyzer`.

Jobs run in Apptainer/Singularity containers on the configured HTCondor site.

## AI Tool Marketplace

Tools are installed on demand, usually with `npm install -g` into
`/opt/npm-global`.

| Tool | Type | Notes |
|------|------|-------|
| PegasusAI Chat | Built-in web chat | Always available |
| Claude Code | Terminal | Anthropic CLI with Pegasus knowledge |
| OpenCode | Terminal/web | Open-source coding assistant |
| Codex CLI | Terminal | OpenAI terminal coding agent |
| Antigravity | Terminal | Lightweight terminal assistant |

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
| Codex CLI | `AGENTS.md` |
| Antigravity | `.antigravity/context.md` |
| Web Chat | Server-side in-memory context |

## LLM Configuration

Supported providers are `gateway` (the zero-config default — an internal
OpenAI-compatible gateway, so chat/tools work with no keys), `anthropic`,
`openai`, `google`, `fabric`, `nrp`, `custom`, and `ollama`.

Configuration is done at runtime on the Settings page and stored in SQLite.
Keys are write-only over the API — responses report only whether a key is set —
and are encrypted at rest when `STUDIO_SECRET_KEY` is provided (see Database).

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
| `notifications` | Notification feed rows |
| `notification_log` | Durable dedup-key ledger (survives clear, so fire-once guards hold) |
| `app_settings` | Key/value app settings (e.g. container registry, chat engine) |

The DB is a single-user store protected by upstream authentication. Stored
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
| `GET/PUT` | `/api/settings/ui` | Per-user interface preferences (Simplified Run, disclosure state, welcome) |
| `GET` | `/api/workflows/{run_id}/jobs` | Job status |
| `GET` | `/api/workflows/{run_id}/events` | Workflow event stream |
| `POST` | `/api/workflows/{run_id}/analyze` | Run diagnostics |
| `GET` | `/api/workflows/{run_id}/estimate` | Remaining-time estimate (historical mean; shown only when confidence allows) |
| `GET` | `/api/workflows/{run_id}/outputs` | Files a run produced (paginated) |
| `GET` | `/api/workflows/{run_id}/outputs/file` | Preview or download one output file |
| `GET` | `/api/workflows/{run_id}/diagnosis` | Plain-language failure cards (cause, action, technical detail) |
| `DELETE` | `/api/workflows/{run_id}` | **Destructive**: cancel if running, then remove the run's submit directory from disk |
| `GET` | `/api/dashboard` | Aggregate landing-page snapshot |
| `GET` | `/api/dashboard/onboarding` | Onboarding funnel (first seen → first workflow → first run → first success) |
| `GET` | `/api/provisioning/status` | Pool inventory, demand, pressure findings, OSPool capacity |
| `GET` | `/api/marketplace` | Workflow catalog sourced live from GitHub |
| `POST` | `/api/marketplace/{id}/add` | git-clone a catalog workflow into the workspace |
| `POST` | `/api/chat/stream` | Built-in SSE chat (engine-dispatched, brokered so a turn outlives the request) |
| `GET` | `/api/chat/turn` | Whether a turn is in flight, so a reloaded panel can reattach |
| `GET` | `/api/chat/turn/events` | SSE: replay the in-flight turn from its start, then follow live |
| `POST` | `/api/chat/permission` | Approve/deny a pending shell command (per-turn gate) |
| `GET/PUT` | `/api/chat/engine`, `/api/chat/command-approval` | Chat engine selection + approval posture |
| `GET` | `/api/notifications` | Notification feed (+ `/prefs`, `/stream` SSE) |
| `GET` | `/api/jupyter/status` | JupyterLab status |
| `WS` | `/ws/terminal` | Bash PTY |
| `WS` | `/ws/terminal/{tool_id}` | AI tool PTY |

## Deployment

V1 deployment is one container per user.

Requirements:

- Native `amd64` node or VM.
- Persistent workspace volume.
- Upstream authentication and routing.
- Runtime privileges or cluster configuration sufficient for Apptainer user
  namespaces when workflows execute `docker://` containers.

KubeSpawner should set the runtime UID and `$HOME`. The image does not create or
depend on a named application user.

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
| Upstream-only auth | CILogon/OIDC in Studio |
| Per-user container | Multi-service Kubernetes deployment |
| Tools inside the user container | Tool sidecars or separate workloads |
| Local `workflow-monitor` process | Shared workflow-monitor service |
