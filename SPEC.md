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
- SQLite state under `.studio/studio.db`.
- Tool configuration files such as `AGENTS.md`, `CLAUDE.md`, `.mcp.json`, or
  `.opencode/`.
- JupyterLab's working directory.

In Kubernetes, mount the user's PVC at `$HOME` or set `STUDIO_WORKSPACE_DIR` to
the mounted workspace path.

## Frontend

`studio-web/` is a Next.js 15 App Router application using React 19, TailwindCSS,
Zustand, TanStack Query, xterm.js, and lucide-react.

Primary routes:

| Route | Purpose |
|-------|---------|
| `/` | Dashboard: workflow summary, recent runs, resources, quick actions |
| `/workflows` | Workflow projects, runs, actions, and details |
| `/provisioning` | Pool inventory, workflow demand, pressure findings (read-only) |
| `/workbench` | AI tool terminal/workbench surface |
| `/notebooks` | Opens JupyterLab at `/jupyter/lab` |
| `/settings` | LLM provider and application settings |

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
| `chat` | PegasusAI SSE chat; pluggable engine — shared `opencode serve` proxy (default) or builtin loop (fallback) |
| `jupyter` | Status plus back-compat start/stop no-ops |
| `dashboard` | Aggregate landing-page data (projects, runs, resources, versions) |
| `provisioning` | HTCondor pool inventory + queue demand + pressure (read-only) |

JupyterLab is not started or stopped by the API. `/api/jupyter/start` and
`/api/jupyter/stop` remain as compatibility no-ops and return current status.

## Workflow Model

Studio discovers workflow projects and Pegasus runs under the workspace.

Project actions:

- **Generate** runs a project's `workflow_generator.py`.
- **Plan** runs `pegasus-plan -s <site> -o <output-site>`.
- **Submit** runs `pegasus-plan --submit`.

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

Canonical knowledge is stored in `knowledge/` and copied into the runtime image
at `/opt/pegasus-ai/knowledge/`.

| Directory | Contents |
|-----------|----------|
| `skills/` | Tool-agnostic skill prompts |
| `agents/` | Persona prompts |
| `templates/` | Workflow generator, wrapper, Dockerfile, README templates |
| `examples/` | Curated Pegasus workflow examples |
| `references/` | Shared Pegasus and PegasusAI context |
| `mcp/` | MCP server definitions |

Adapters translate the shared knowledge into each tool's native format:

| Adapter | Output |
|---------|--------|
| Claude Code | `CLAUDE.md`, `.mcp.json`, Claude plugin/skill files |
| OpenCode | `AGENTS.md`, `opencode.json`, `.opencode/skills`, `.opencode/agents` |
| Codex CLI | `AGENTS.md` |
| Antigravity | `.antigravity/context.md` |
| Web Chat | Server-side in-memory context |

## LLM Configuration

Supported providers are `anthropic`, `openai`, `fabric`, `nrp`, `custom`, and
`ollama`. Initial configuration can come from environment variables, and runtime
configuration is stored in SQLite.

Important variables:

| Variable | Purpose |
|----------|---------|
| `LLM_PROVIDER` / `LLM_MODEL` | Default provider and optional model |
| `ANTHROPIC_API_KEY` | Anthropic provider and Claude Code |
| `OPENAI_API_KEY` | OpenAI provider and Codex CLI |
| `FABRIC_AI_API_KEY` | FABRIC AI gateway |
| `NRP_API_KEY` | NRP Nautilus eLLM |
| `CUSTOM_BASE_URL` / `CUSTOM_API_KEY` | Custom OpenAI-compatible endpoint |
| `OLLAMA_HOST` | Ollama endpoint |

The built-in chat supports both OpenAI Chat Completions and the OpenAI Responses
API and falls back when an endpoint requires Responses.

## Database

SQLite lives under the workspace, normally `$HOME/work/.studio/studio.db`.

Main tables:

| Table | Purpose |
|-------|---------|
| `tool_installations` | Installed/running tool state |
| `llm_config` | Active LLM provider, model, key, base URL |
| `workflow_runs` | Discovered workflow run metadata |
| `chat_messages` | Built-in chat history |

This plaintext storage model is acceptable for V1 because each container is a
single-user environment protected by upstream authentication.

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
| `GET` | `/api/workflows` | List workflow runs |
| `GET` | `/api/workflows/projects` | List workflow projects |
| `GET` | `/api/workflows/projects/{id}/params` | Introspect generator params |
| `POST` | `/api/workflows/projects/{id}/generate` | Generate workflow files |
| `POST` | `/api/workflows/projects/{id}/plan` | Plan a workflow |
| `POST` | `/api/workflows/projects/{id}/submit` | Submit a workflow |
| `GET` | `/api/workflows/{run_id}/jobs` | Job status |
| `GET` | `/api/workflows/{run_id}/events` | Workflow event stream |
| `POST` | `/api/workflows/{run_id}/analyze` | Run diagnostics |
| `GET` | `/api/workflows/{run_id}/estimate` | Remaining-time estimate (historical mean) |
| `GET` | `/api/dashboard` | Aggregate landing-page snapshot |
| `GET` | `/api/provisioning/status` | Pool inventory, demand, pressure findings |
| `POST` | `/api/chat/stream` | Built-in SSE chat (engine-dispatched) |
| `GET/PUT` | `/api/chat/engine` | Chat engine selection (builtin / opencode) + attach info |
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
