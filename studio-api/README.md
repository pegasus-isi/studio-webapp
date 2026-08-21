# PegasusAI Studio API

FastAPI backend for PegasusAI Studio — a web IDE for building, running, and
monitoring [Pegasus WMS](https://pegasus.isi.edu/) workflows.

## Architecture

```
studio-api/
├── main.py                 # FastAPI app, lifespan, same-origin gate + CORS, router registration
├── db.py                   # Async SQLite (aiosqlite) — hand-rolled migrations
├── models.py               # Pydantic v2 schemas + enums
├── security.py             # same-origin gate for mutating HTTP (403) + WebSocket cross-origin guard (4403) + CORS allowlist
├── logging_config.py       # structlog JSON processor chain
├── routers/                # 16 routers (see API Endpoints below)
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
│                           # builder, opencode engine, notifications, terminals,
│                           # chat_permissions, procutil, …
├── init_db.py              # CLI: initialize / migrate the SQLite schema
├── propagate_llm.py        # CLI: propagate the saved LLM config to installed tools
└── tests/                  # ~240 tests; no Pegasus/Condor/keys needed
```

The SQLite DB (`~/.studio/studio.db`; an existing one is moved out of `~/work/.studio` on first start) has **8 tables**: `app_settings`,
`llm_config`, `provider_configs`, `tool_installations`, `workflow_runs`,
`chat_messages`, `notifications`, `notification_log`.

### Knowledge checkout trust model

The knowledge tree (`~/.local/share/pegasus-ai/marketplace`, seeded from
`/opt/pegasus-ai/marketplace`) tracks `main` of
`pegasus-isi/claude-plugin-marketplace` and is fast-forwarded by
`knowledge/checkout.update()` at every API start and on
`POST /api/knowledge/marketplace/update`. What it can do is limited to:

- **Prose.** Skills, agents, templates and references are read as text by
  Claude Code, opencode and Codex.
- **MCP servers.** `knowledge/claude_code.py:filter_mcp_servers` accepts only
  `{"type": "http"|"sse"|"streamable-http", "url": "https://…"}`; entries with
  `command`/`args`/`env`, non-https URLs, `${VAR}` expansions, or extra keys
  such as `headers` are dropped and logged (`mcp_server_dropped`). The filtered
  set is what lands in `~/.mcp.json` and `~/.claude.json`. opencode and Codex
  MCP servers are hardcoded in their adapters and never read from the tree.
- **git.** Every invocation runs with `GIT_TERMINAL_PROMPT=0`,
  `core.hooksPath=/dev/null`, `core.fsmonitor=false`, an empty
  `credential.helper`, `GIT_ALLOW_PROTOCOL=http:https:file`, and `update()`
  refuses when `origin` is not an http(s) URL or an absolute local path
  (`reason: "origin is not an http(s) remote"`).

Not covered here: Claude Code loads the enabled plugin's own `.mcp.json` from
its plugin cache directly. The control for that and for the prose is review on
the marketplace repository (branch protection, required reviews, signed
commits). Build against a fixed commit with `--build-arg KNOWLEDGE_REF=<sha>`
when you need one.

## Development Setup

### Prerequisites

- Python 3.11+ (the container uses 3.12)
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
uvicorn main:app --reload --port 8080
```

Swagger UI at `http://localhost:8080/docs`.

### 5. Tests (the pre-PR gate — CI runs none)

```bash
pytest -q                                    # whole suite
pytest tests/test_workflows.py::test_x -q    # single test
```

All tests use temporary SQLite DBs and need no Pegasus, HTCondor, or LLM keys.

## API Endpoints

16 routers are registered under `/api` (WebSockets under `/ws`): `health`,
`settings`, `llm`, `knowledge`, `tools`, `files`, `jupyter`, `terminal`,
`ai_terminal`, `workflows`, `chat`, `notifications`, `dashboard`, `provisioning`,
`pegasus_bot`, `marketplace`. Highlights (see `/docs` for the full list):

### Health, Settings & LLM

| Method | Path | Notes |
|--------|------|-------|
| GET | `/api/health`, `/api/health/detailed` | liveness + DB/Pegasus/HTCondor |
| GET/PUT | `/api/settings` | LLM-config accessor (shares the validated `llm` save path) |
| GET/PUT | `/api/llm/config` | active provider/model; **keys are write-only** — GETs return `has_api_key`, never the value |
| GET/PUT | `/api/llm/provider-configs` | saved providers (also write-only keys) |
| GET | `/api/llm/provider-configs/{id}/models` | model discovery using the **stored** key (server-side) |
| POST | `/api/llm/validate` | test key connectivity |

### Workflows

| Method | Path | Notes |
|--------|------|-------|
| GET | `/api/workflows` | discover + list runs |
| GET | `/api/workflows/{id}` `/jobs` `/events` `/estimate` `/graph.json` | detail, jobs, SSE events, ETA, DAG |
| POST | `/api/workflows/{id}/analyze` | pegasus-analyzer |
| DELETE | `/api/workflows/{id}` | **destructive** — cancels if running, then removes the run's submit directory from disk |
| GET/POST | `/api/workflows/projects…` | project list, params, generate, plan, run, build image (SSE) |

### Chat, Tools, Files, Terminals

| Method | Path | Notes |
|--------|------|-------|
| POST | `/api/chat/stream` | SSE chat (engine: `opencode` default, or `builtin`) |
| POST | `/api/chat/permission` | approve/deny a pending shell command (per-turn gate) |
| GET/PUT | `/api/chat/engine`, `/api/chat/command-approval` | engine + approval posture |
| * | `/api/tools/{id}/install|uninstall|start|stop` | AI-tool lifecycle |
| * | `/api/files…` | workspace CRUD (path-confined) |
| WS | `/ws/terminal`, `/ws/terminal/{tool_id}` | bash / AI-tool PTY |

### Jupyter

JupyterLab runs **always-on** under supervisord (served at `/jupyter/`);
`GET /api/jupyter/status` reports whether it's up. The studio does not start or
stop it.

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `HOME` | set by KubeSpawner | user home; workspace defaults to `$HOME/work` |
| `STUDIO_WORKSPACE_DIR` | `$HOME/work` | workspace root (DB, tool configs, projects) |
| `STUDIO_SCAN_DIR` | `$HOME` | root scanned for `braindump.yml` runs |
| `KNOWLEDGE_ROOT` | set by the image | knowledge tree path. Unset falls back to the marketplace checkout, then the legacy `/opt/pegasus-ai/knowledge`. Set it for local dev, where neither exists. |
| `KNOWLEDGE_MARKETPLACE` | (unset) | pin the marketplace checkout. Unset prefers the user's `~/.local/share/pegasus-ai/marketplace`, then the image-baked `/opt/pegasus-ai/marketplace` |
| `STUDIO_KNOWLEDGE_GIT_TIMEOUT` | `20` | Seconds before a knowledge-base `git` call (status / fast-forward) is abandoned |
| `KNOWLEDGE_OVERLAY` | `/opt/pegasus-ai/overlay` | Studio-specific context composed on top of the portable Pegasus guide |
| `STUDIO_CHAT_ENGINE_DEFAULT` | `opencode` | default chat engine |
| `STUDIO_ALLOWED_ORIGINS` | (empty) | extra origins admitted by CORS and by the same-origin gate on mutating requests (same-origin always allowed; also the escape hatch if a proxy chain presents a `Host` the browser didn't send) |
| `STUDIO_TRUST_AUTH_HEADER` | `0` | trust `X-Auth-User` for display identity (opt-in) — the bundled nginx strips `X-Auth-User` from every request, so this only applies when uvicorn is fronted by something else |
| `STUDIO_LLM_GATEWAY_URL` | internal | base URL for the zero-config `gateway` provider |
| `STUDIO_SECRET_KEY` / `STUDIO_SECRET_KEY_FILE` | (unset) | master secret used to encrypt stored API keys / registry password at rest (S7). Unset → stored plaintext with a warning. Must come from a sealed secret, **not** the PVC. |

Provider API keys are stored in the DB via the app (not env). See `main.py` /
`llm/providers.py` for the full set of `STUDIO_*` toggles.

## Container Deployment

In the container, nginx (**port 8888**, the only exposed port) fronts everything:

```
nginx :8888
├── /          → Next.js frontend (:3000)
├── /api/, /ws/ → studio-api (:8080)
└── /jupyter/  → JupyterLab (:8889, always-on)
```

Supervised by supervisord. `docker/entrypoint.sh`:

1. On **first launch only** (marker on the PVC), runs `timeout 60 init_db.py`
   and seeds `AGENTS.md`.
2. Writes `~/.pegasus/pegasus.conf` (agent URL) if absent.
3. Backgrounds `propagate_llm.py` (hard-timeout'd) to sync tool configs.
4. `exec supervisord` — which starts nginx, studio-api (uvicorn), JupyterLab,
   and `opencode serve`.

## Supported LLM Providers

`gateway` (the zero-config default, points at the internal LLM gateway),
`anthropic`, `openai`, `google`, `fabric`, `nrp`, `ollama`, and `custom`
(user-specified base URL). Keyed presets are validated at save time; the
gateway/ollama/custom endpoints may be keyless.
