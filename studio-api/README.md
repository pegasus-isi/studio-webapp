# PegasusAI Studio API

FastAPI backend for PegasusAI Studio — a web IDE for building, running, and
monitoring [Pegasus WMS](https://pegasus.isi.edu/) workflows.

## Architecture

```
studio-api/
├── main.py                 # FastAPI app, lifespan, CORS, router registration
├── db.py                   # Async SQLite (aiosqlite) — hand-rolled migrations
├── models.py               # Pydantic v2 schemas + enums
├── security.py             # CORS allowlist + WebSocket cross-origin guard
├── logging_config.py       # structlog JSON processor chain
├── routers/                # 16 routers (see API Endpoints below)
├── llm/
│   ├── providers.py        # Provider registry, key-requirement + model discovery
│   └── propagator.py       # Push the active LLM config to installed tools
├── knowledge/              # Per-tool adapters that GENERATE runtime config
│   ├── adapters.py         # KnowledgeAdapter ABC + registry
│   ├── claude_code.py / opencode.py / codex_cli.py / antigravity.py / web_chat.py
├── services/               # workflow scan/submit/monitor, condor pool, image
│                           # builder, opencode engine, notifications, terminals,
│                           # chat_permissions, procutil, …
├── init_db.py              # CLI: initialize / migrate the SQLite schema
├── propagate_llm.py        # CLI: propagate the saved LLM config to installed tools
└── tests/                  # ~230 tests; no Pegasus/Condor/keys needed
```

The SQLite DB (`~/work/.studio/studio.db`) has **8 tables**: `app_settings`,
`llm_config`, `provider_configs`, `tool_installations`, `workflow_runs`,
`chat_messages`, `notifications`, `notification_log`.

## Development Setup

### Prerequisites

- Python 3.11+ (the container uses 3.12)
- Pegasus WMS + HTCondor are optional for dev — health checks degrade gracefully

### 1–3. venv, deps, DB

```bash
cd studio-api
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt pytest httpx
python init_db.py          # creates ~/work/.studio/studio.db
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
| GET/PUT | `/api/settings` | legacy LLM-config accessor (shares the validated `llm` save path) |
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

### Jupyter (back-compat no-ops)

JupyterLab runs **always-on** under supervisord (served at `/jupyter/`), so
`POST /api/jupyter/start|stop` are no-ops kept for back-compat; only
`GET /api/jupyter/status` is meaningful.

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `HOME` | set by KubeSpawner | user home; workspace defaults to `$HOME/work` |
| `STUDIO_WORKSPACE_DIR` | `$HOME/work` | workspace root (DB, tool configs, projects) |
| `STUDIO_SCAN_DIR` | `$HOME` | root scanned for `braindump.yml` runs |
| `KNOWLEDGE_ROOT` | `/opt/pegasus-ai/knowledge` | shared knowledge store |
| `STUDIO_CHAT_ENGINE_DEFAULT` | `opencode` | default chat engine |
| `STUDIO_ALLOWED_ORIGINS` | (empty) | extra CORS origins (same-origin always allowed) |
| `STUDIO_TRUST_AUTH_HEADER` | `0` | trust `X-Auth-User` for display identity (opt-in) |
| `STUDIO_LLM_GATEWAY_URL` | internal | base URL for the zero-config `gateway` provider |

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

There is **no** `seed_config.py` step (it was removed); setting LLM env vars does
not seed provider config.

## Supported LLM Providers

`gateway` (the zero-config default, points at the internal LLM gateway),
`anthropic`, `openai`, `google`, `fabric`, `nrp`, `ollama`, and `custom`
(user-specified base URL). Keyed presets are validated at save time; the
gateway/ollama/custom endpoints may be keyless.
