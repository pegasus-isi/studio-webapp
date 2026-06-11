# PegasusAI Studio API

FastAPI backend for [PegasusAI Studio](../SPEC.md) — an AI-powered scientific workflow development platform for Pegasus WMS.

## Architecture

```
studio-api/
├── main.py                 # FastAPI app, lifespan, CORS
├── db.py                   # Async SQLite (aiosqlite) — 4 tables
├── models.py               # Pydantic v2 schemas + enums
├── logging_config.py       # structlog JSON processor chain
├── routers/
│   ├── health.py           # GET /api/health, /api/health/detailed
│   ├── settings.py         # GET/PUT /api/settings
│   ├── llm.py              # CRUD /api/llm/config, /providers, /validate
│   ├── knowledge.py        # /api/knowledge/skills|agents|templates|examples
│   ├── tools.py            # /api/tools — marketplace install/start/stop
│   ├── workflows.py        # /api/workflows — scan, monitor, submit, analyze
│   ├── chat.py             # /api/chat/stream (SSE) + tool use loop
│   ├── files.py            # /api/files — workspace CRUD
│   ├── jupyter.py          # /api/jupyter — start/stop/status
│   ├── terminal.py         # WS /ws/terminal — bash PTY
│   └── ai_terminal.py      # WS /ws/terminal/{tool_id} — AI tool PTY
├── llm/
│   ├── providers.py        # Provider registry, model discovery (async httpx)
│   └── propagator.py       # Push LLM config to all installed tools
├── knowledge/
│   ├── adapters.py         # KnowledgeAdapter ABC + registry
│   ├── claude_code.py      # Claude Code adapter
│   ├── opencode.py         # OpenCode adapter
│   ├── codex_cli.py        # Codex CLI adapter
│   ├── antigravity.py      # Antigravity adapter
│   └── web_chat.py         # Built-in chat (no-op)
├── services/
│   ├── installer.py        # ToolInstaller + inline tool registry
│   ├── process_mgr.py      # ProcessManager for tool subprocesses
│   ├── workflow_scanner.py # Discover Pegasus run dirs via braindump.yml
│   ├── workflow_monitor.py # Query stampede DB, tail events
│   └── workflow_submitter.py # pegasus-plan --submit wrapper
├── init_db.py              # CLI: initialize SQLite schema
├── seed_config.py          # CLI: seed LLM config from ~/.pegasus-ai/.env
├── propagate_llm.py        # CLI: propagate LLM config to tools
└── tests/
    ├── conftest.py         # Fixtures: tmp_db, TestClient
    ├── test_health.py
    ├── test_llm.py
    ├── test_knowledge.py
    ├── test_tools.py
    ├── test_workflows.py
    └── test_chat.py
```

## Development Setup

### Prerequisites

- Python 3.11+
- Node.js 18+ (for AI tool npm installs at runtime — not needed for the API itself)
- Pegasus WMS 5.1+ and HTCondor (optional for dev — health checks degrade gracefully)

### 1. Create virtual environment

```bash
cd studio-api
python3 -m venv .venv
source .venv/bin/activate
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
pip install pytest httpx  # dev dependencies
```

### 3. Initialize the database

```bash
python init_db.py
```

This creates `~/work/.studio/studio.db` with the schema (4 tables: `tool_installations`, `llm_config`, `workflow_runs`, `chat_messages`).

### 4. (Optional) Seed LLM config from workbench env

If you have a `~/.pegasus-ai/.env` from the pegasus-ai-workbench:

```bash
python seed_config.py
```

### 5. Run the development server

```bash
uvicorn main:app --reload --port 8080
```

The API is now live at `http://localhost:8080`. Explore endpoints at `http://localhost:8080/docs` (Swagger UI).

### 6. Run tests

```bash
pytest tests/ -v
```

All tests use temporary SQLite databases and don't require Pegasus, HTCondor, or any LLM API keys.

## API Endpoints

### Health & Settings

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/health` | Basic health check |
| GET | `/api/health/detailed` | DB + Pegasus + HTCondor status |
| GET | `/api/settings` | LLM config + installed tools |
| PUT | `/api/settings` | Update LLM config |

### LLM Configuration

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/llm/config` | Current LLM provider/model/key |
| PUT | `/api/llm/config` | Update config + propagate to tools |
| GET | `/api/llm/providers` | List supported providers |
| POST | `/api/llm/validate` | Test API key connectivity |

### Knowledge Layer

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/knowledge/skills` | List available skills |
| GET | `/api/knowledge/skills/{name}` | Get skill content |
| GET | `/api/knowledge/agents` | List agent personas |
| GET | `/api/knowledge/templates` | List workflow templates |
| GET | `/api/knowledge/examples` | List example workflows |

### AI Tool Marketplace

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/tools` | List all tools + install status |
| GET | `/api/tools/{id}` | Tool detail |
| POST | `/api/tools/{id}/install` | Install a tool |
| POST | `/api/tools/{id}/uninstall` | Uninstall a tool |
| POST | `/api/tools/{id}/start` | Start tool process |
| POST | `/api/tools/{id}/stop` | Stop tool process |
| GET | `/api/tools/{id}/status` | Check if running |

### Workflows

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/workflows` | Discover and list runs |
| GET | `/api/workflows/{id}` | Workflow detail from stampede DB |
| GET | `/api/workflows/{id}/jobs` | Job-level status |
| GET | `/api/workflows/{id}/events` | SSE stream of events |
| POST | `/api/workflows/{id}/analyze` | Run pegasus-analyzer |
| DELETE | `/api/workflows/{id}` | Cancel / pegasus-remove |
| POST | `/api/workflows/submit` | Plan + submit a workflow |

### Chat (Built-in PegasusAI)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/chat/stream` | SSE streaming chat with tool use |
| POST | `/api/chat/stop` | Abort current response |
| GET | `/api/chat/agents` | List agent personas |
| GET | `/api/chat/history` | Chat history |

### Files

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/files` | List directory |
| GET | `/api/files/read` | Read file (100KB limit) |
| POST | `/api/files/write` | Write file |
| DELETE | `/api/files` | Delete file/dir |
| POST | `/api/files/mkdir` | Create directory |

### Jupyter

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/jupyter/start` | Launch JupyterLab |
| POST | `/api/jupyter/stop` | Stop JupyterLab |
| GET | `/api/jupyter/status` | Check status |

### WebSocket Terminals

| Protocol | Path | Description |
|----------|------|-------------|
| WS | `/ws/terminal` | Bash PTY session |
| WS | `/ws/terminal/{tool_id}` | AI tool PTY session |

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `HOME` | set by KubeSpawner | User home — workspace defaults to `$HOME/work/` |
| `STUDIO_WORKSPACE_DIR` | `$HOME/work` | Override workspace root (SQLite DB, tool configs, projects) |
| `KNOWLEDGE_ROOT` | `/opt/pegasus-ai/knowledge` | Shared knowledge store |
| `ANTHROPIC_API_KEY` | — | For Claude Code / Anthropic provider |
| `OPENAI_API_KEY` | — | For Codex CLI / OpenAI provider |
| `FABRIC_AI_API_KEY` | — | For FABRIC AI gateway |
| `NRP_API_KEY` | — | For NRP Nautilus eLLM |

## Container Deployment

In the production container, the API runs behind Nginx alongside the Next.js frontend:

```
Nginx (port 80)
├── /           → Next.js frontend (port 3000)
├── /api/       → studio-api (port 8080)
├── /ws/        → studio-api WebSocket
└── /jupyter/   → JupyterLab (port 8889, on-demand)
```

The container entrypoint:
1. Runs `python init_db.py` to ensure schema exists
2. Runs `python seed_config.py` to import workbench-era `.env` config
3. Starts `uvicorn main:app --port 8080`

## Supported LLM Providers

| Provider | Models Endpoint | Default Model |
|----------|----------------|---------------|
| Anthropic | `/v1/models` | claude-sonnet-4-5-20250929 |
| OpenAI | `/v1/models` | gpt-4o |
| FABRIC AI | `/v1/models` | qwen3-coder-30b |
| NRP Nautilus | `/v1/models` | qwen3-coder-30b |
| Custom | `/v1/models` | (user-specified) |
| Ollama | `/v1/models` | qwen2.5-coder:7b |
