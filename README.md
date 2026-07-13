# PegasusAI Studio

PegasusAI Studio is a single-user web IDE for building, running, and monitoring
[Pegasus WMS](https://pegasus.isi.edu/) workflows. It packages a Next.js UI,
FastAPI backend, JupyterLab, Pegasus WMS, HTCondor, Apptainer, workflow
monitoring, and AI coding tools into one `linux/amd64` container.

Authentication is handled upstream by JupyterHub/KubeSpawner, Open OnDemand, or
an ingress layer. The container has no built-in auth and runs as the UID provided
by the spawner.

## Runtime

```
Browser
  |
JupyterHub / OOD / ingress
  |
nginx :8888
  |-- /          -> studio-web  :3000
  |-- /api/*     -> studio-api  :8080
  |-- /ws/*      -> studio-api  WebSocket PTYs
  `-- /jupyter/* -> JupyterLab  :8889
```

Long-running services are managed by `supervisord`
([docker/supervisor/supervisord.conf](docker/supervisor/supervisord.conf)).
JupyterLab is always on and opens in a separate browser tab.

## Architecture Notes

- Runtime image: `ubuntu:24.04`, system Python 3.12, Node.js 20.
- Pegasus WMS and HTCondor are installed from DEB packages.
- Apptainer 1.5 provides Singularity-compatible `docker://` workflow execution.
- JupyterLab runs in classic document mode. `jupyter-ai` and collaboration/RTC
  extensions are intentionally excluded because they break notebook loading
  behind the proxy.
- Workflow status comes from `workflow-monitor --serve` JSONL events, started
  automatically on submit.
- The workspace defaults to `$HOME/work`; Studio state is stored under
  `$HOME/work/.studio`.

## Platform Warning

Run the image on native `amd64` hardware. On Apple Silicon it can run through
Rosetta/QEMU, but live Jupyter kernels fail during the `kernel_info` handshake.
Use an amd64 Linux VM, lab host, or Kubernetes node for full functionality.

## Build And Run

```bash
docker build -t pegasus/studio-webapp:latest .

./local-test-instance.sh
```

Open <http://localhost:8888>.

`--privileged` is needed for local Apptainer execution. Kubernetes deployments
should provide equivalent user-namespace support through the cluster policy.

## Main Views

| View | Purpose |
|------|---------|
| Workflows | Discover projects and runs; generate, plan, submit, monitor, and analyze workflows |
| Workbench | Launch AI tools in terminal tabs |
| Notebooks | Open the always-on JupyterLab instance |
| Settings | Configure LLM providers, keys, and models |
| PegasusAI Chat | Built-in SSE chat with file, shell, and Pegasus tool use |
| Terminal | Browser PTY connected to the workspace |

Workflow Generate / Plan / Submit introspect each project's
`workflow_generator.py` `argparse` definition at runtime, so each workflow gets
its own parameter form.

## AI Tools

The Workbench can install and launch:

| Tool | Type |
|------|------|
| PegasusAI Chat | Built-in web chat |
| Claude Code | Terminal |
| OpenCode | Terminal/web tool integration |
| Codex CLI | Terminal |
| Antigravity | Terminal |

Tools share the canonical knowledge base in
[knowledge/](knowledge/) through per-tool adapters. npm globals install under
`/opt/npm-global`, which is writable by arbitrary runtime UIDs.

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `STUDIO_WORKSPACE_DIR` | `$HOME/work` | Workspace root for projects, SQLite state, and tool configs |
| `LLM_PROVIDER` | unset | `anthropic`, `openai`, `fabric`, `nrp`, `custom`, or `ollama` |
| `LLM_MODEL` | provider default | Optional model override |
| `ANTHROPIC_API_KEY` | unset | Anthropic key |
| `OPENAI_API_KEY` | unset | OpenAI key |
| `FABRIC_AI_API_KEY` | unset | FABRIC AI gateway key |
| `NRP_API_KEY` | unset | NRP Nautilus eLLM key |
| `CUSTOM_BASE_URL` / `CUSTOM_API_KEY` | unset | Custom OpenAI-compatible endpoint |
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama server URL |
| `NPM_CONFIG_PREFIX` | `/opt/npm-global` | npm global install prefix |
| `STUDIO_POOL_CACHE_TTL` | `15` | Seconds between HTCondor pool/queue snapshots (provisioning + dashboard) |
| `STUDIO_RESOURCE_WAIT_SECONDS` | `300` | Idle-job queue wait past which a resource alert is raised |

The Settings page can update LLM configuration at runtime; values are stored in
the workspace SQLite database.

## Development

Backend:

```bash
cd studio-api
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt pytest
uvicorn main:app --reload --port 8080
```

Frontend:

```bash
cd studio-web
npm ci
npm run dev
```

Tests:

```bash
cd studio-api && pytest
cd studio-web && npm test
```

## Source Layout

| Path | Contents |
|------|----------|
| [Dockerfile](Dockerfile) | Multi-stage container build |
| [docker/](docker/) | nginx, Jupyter, HTCondor, entrypoint, and supervisord config |
| [studio-api/](studio-api/) | FastAPI backend, SQLite state, tool adapters, workflow services |
| [studio-web/](studio-web/) | Next.js frontend |
| [knowledge/](knowledge/) | Skills, agents, templates, examples, references, MCP config |
| [SPEC.md](SPEC.md) | Current design specification |

## License

Apache License 2.0.
