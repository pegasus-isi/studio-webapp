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
nginx :8888
  |-- /          -> studio-web  :3000
  |-- /api/*     -> studio-api  :8080
  |-- /ws/*      -> studio-api  WebSocket PTYs
  `-- /jupyter/* -> JupyterLab  :8889
```

The container entrypoint initializes the workspace and then execs
`supervisord`. Process definitions live in
`docker/supervisor/supervisord.conf`.

| Program | Command | Purpose |
|---------|---------|---------|
| `studio-api` | `uvicorn main:app --host 0.0.0.0 --port 8080` | FastAPI backend |
| `studio-web` | `node server.js` | Next.js standalone server |
| `jupyter` | `/etc/supervisor/scripts/jupyter.sh` | Always-on JupyterLab |
| `nginx` | `nginx -g "daemon off;"` | Public reverse proxy |

`supervisord` restarts crashed services and keeps logs on stdout/stderr for the
container runtime.

## Container Stack

- Base image: `ubuntu:24.04`.
- Supported platform: `linux/amd64`.
- Python: single system Python 3.12. No conda/Miniforge layer.
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

## Future V2 Direction

V2 can move the same API and frontend concepts onto shared infrastructure:

| V1 | V2 |
|----|----|
| SQLite in user workspace | PostgreSQL |
| Upstream-only auth | CILogon/OIDC in Studio |
| Per-user container | Multi-service Kubernetes deployment |
| Tools inside the user container | Tool sidecars or separate workloads |
| Local `workflow-monitor` process | Shared workflow-monitor service |
