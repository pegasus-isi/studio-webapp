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
- The workspace defaults to `$HOME/work` and holds workflow projects; Studio's
  own state lives at `$HOME/.studio`, and generated tool config (`CLAUDE.md`,
  `AGENTS.md`, `.mcp.json`) at `$HOME`.

## Running locally (for testing)

In production the image is built and published by CI (a merge to `main` pushes
`ghcr.io/pegasus-isi/studio-webapp:main`, which studio-system pulls). To build
and run it yourself for testing:

```bash
docker build --platform linux/amd64 -t pegasus/studio-webapp:latest .
./local-test-instance.sh          # → http://localhost:8888
```

> **Build and run on native `amd64` hardware.** On Apple Silicon the image runs
> under Rosetta/QEMU, but live Jupyter kernels fail the `kernel_info` handshake —
> use an amd64 Linux VM, lab host, or Kubernetes node for full functionality.

`local-test-instance.sh` runs the image as your UID/GID with a throwaway home.
All knobs are optional environment variables:

| Env | Purpose |
|-----|---------|
| `IMAGE` / `PORT` | Image to run / host port (default `pegasus/studio-webapp:latest`, `8888`) |
| `STUDIO_HOME` | Persist the container home here instead of a tmp dir (keeps SQLite state, tool configs, cloned workflows across runs) |
| `CONDOR_HOST` | Point Studio's HTCondor client at a different AP / central manager (also sets the schedd host) |
| `CONDOR_SCHEDD_HOST` | Schedd host, if different from `CONDOR_HOST` |
| `CONDOR_TOKENS_DIR` | Host dir of HTCondor IDTOKENs, mounted read-only and used as the token directory — **required to authenticate to a real pool** |
| `CONDOR_CONFIG_DROPIN` | Host path to a condor config file, mounted as `/etc/condor/config.d/99-local.conf` (wins over the baked config; alternative to the `CONDOR_*` knobs) |
| `PRIVILEGED=1` | Run `--privileged` (needed for local Apptainer workflow execution) |
| `EXTRA_DOCKER_ARGS` | Extra args appended to `docker run` |
| `DRY_RUN=1` | Print the `docker run` command instead of executing it |

Example — drive a local build against a real submit host (see
[HTCondor connection](#htcondor-connection) for the credential requirement):

```bash
CONDOR_HOST=my-ap.example.org \
CONDOR_TOKENS_DIR="$HOME/.condor/tokens.d" \
STUDIO_HOME=/tmp/studio-home \
./local-test-instance.sh
```

`--privileged` (via `PRIVILEGED=1`) is needed for local Apptainer execution.
Kubernetes deployments should provide equivalent user-namespace support through
the cluster policy.

## Main Views

| View (nav label) | Route | Purpose |
|------------------|-------|---------|
| Home | `/` | Landing page: capabilities, recent runs, resources at a glance |
| Workflows | `/workflows` | Discover projects and runs; generate, plan, build, submit, monitor, and analyze workflows |
| Marketplace | `/marketplace` | Browse workflows sourced live from GitHub (PegasusHub registry + curated Pegasus-ISI list) and "Add" (git-clone) one into the workspace |
| Resources | `/provisioning` | HTCondor pool inventory, workflow demand, pressure findings, and an OSPool (OSG) capacity panel |
| Notebooks | `/notebooks` | Open the always-on JupyterLab instance |
| Settings | `/settings` | LLM providers, chat engine + shell-command approval, **Tools** (install + launch AI tools / bash terminal), container registry |
| PegasusAI Chat | (slide-in panel + `/chat`) | Built-in SSE chat with file, shell, and Pegasus tool use; per-turn approval before shell commands run |
| Terminal | (panel) | Browser PTY connected to the workspace |

Workflow Generate / Plan / Submit introspect each project's
`workflow_generator.py` `argparse` definition at runtime, so each workflow gets
its own parameter form.

## AI Tools

The built-in **PegasusAI Chat** panel is always available (not an installable
tool). Settings → Tools can install and launch the CLI/agent tools:

| Tool | Type |
|------|------|
| OpenCode | Terminal/web (bundled in the image; also powers the default chat engine) |
| Claude Code | Terminal |
| Codex CLI | Terminal |
| Antigravity | Terminal |

All tools share one knowledge base — skills, specialist subagents, templates and
the Pegasus reference guide — authored in
[pegasus-isi/claude-plugin-marketplace](https://github.com/pegasus-isi/claude-plugin-marketplace)
and checked out into the image at `/opt/pegasus-ai/marketplace`. Per-tool adapters
translate it into each tool's native config, so Claude Code and OpenCode answer
the same question the same way. npm globals install under `/opt/npm-global`,
which is writable by arbitrary runtime UIDs.

### JupyterLab extensions

Two independent JupyterLab extensions are bundled (distinct packages, no
conflict):

| Extension | Package | Purpose |
|-----------|---------|---------|
| PegasusAI Chat | `jupyter-pegasusai-chat` | Docks the Studio chat panel inside JupyterLab (iframe) |
| PegasusAI Bot client | `@gclab/pegasus-ai-client` (`pegasus_ai_client`) | Cell-toolbar button that sends a cell to the [PegasusAI Bot server](https://github.com/TauferLab/Src_PegasusAI_Client) for AI debugging help |

The Bot client is optional: build with `--build-arg ENABLE_PEGASUS_BOT_CLIENT=0`
to leave it out. Its upstream repo is private, so the source is **vendored**
under [`jupyter-pegasus-ai-client/`](jupyter-pegasus-ai-client/VENDORED.md)
(built at image time) rather than cloned. It is auto-configured (no manual
setup) — the server URL and the environment user are injected into the
JupyterLab page at server start; the only local change to the vendored source
is `src/configPlugin.tsx`, which reads that injected config. See `VENDORED.md`
to update the copy.

Because the bot server is a cluster-internal HTTP service (not reachable from
the browser over the HTTPS studio origin), the extension is pointed at a
same-origin proxy — `studio-api` `/api/pegasus-bot` forwards `search`/`feedback`
to `PEGASUS_BOT_SERVER_URL` from inside the pod. Set `PEGASUS_BOT_CLIENT_URL`
to bypass the proxy if the server is directly browser-reachable.

## Configuration

> **LLM providers and API keys are configured in the Settings UI, not here.**
> They're stored in the workspace SQLite DB; the default `gateway` provider
> needs no key, so chat and tools work out of the box.

| Variable | Default | Description |
|----------|---------|-------------|
| `STUDIO_WORKSPACE_DIR` | `$HOME/work` | Workspace root for projects, SQLite state, and tool configs |
| `STUDIO_SCAN_DIR` | `$HOME` | Root scanned for `braindump.yml` runs (the run list + notification scan) |
| `STUDIO_SECRET_KEY` / `STUDIO_SECRET_KEY_FILE` | unset | Master secret used to encrypt stored API keys + the registry password at rest. Unset → stored plaintext with a warning. Must come from a sealed secret, **not** the PVC |
| `STUDIO_LLM_GATEWAY_URL` | internal | Base URL for the zero-config `gateway` provider |
| `STUDIO_ALLOWED_ORIGINS` | (empty) | Extra CORS origins (same-origin is always allowed) |
| `STUDIO_TRUST_AUTH_HEADER` | `0` | Trust `X-Auth-User` for the display identity (opt-in; prod identity comes from `JUPYTERHUB_USER`) |
| `NPM_CONFIG_PREFIX` | `/opt/npm-global` | npm global install prefix |
| `STUDIO_POOL_CACHE_TTL` | `15` | Seconds between HTCondor pool/queue snapshots (provisioning + dashboard) |
| `STUDIO_RESOURCE_WAIT_SECONDS` | `300` | Idle-job queue wait past which a resource alert is raised |
| `STUDIO_OSPOOL_CM` | `cm-1.ospool.osg-htc.org` | OSPool central manager for the Provisioning OSPool panel (empty disables it) |
| `STUDIO_OSPOOL_CACHE_TTL` | `120` | Seconds to cache the (slow, remote) OSPool query |
| `STUDIO_OPENCODE_PORT` | `4747` | Localhost port for the shared `opencode serve` chat engine |
| `STUDIO_OPENCODE_BIN` | `opencode` | OpenCode binary used by the chat engine |
| `STUDIO_DEFAULT_TOOLS` | `opencode` | Image-bundled tools registered as installed at startup (csv; empty disables) |
| `STUDIO_CHAT_ENGINE_DEFAULT` | `opencode` | Chat engine when the user hasn't picked one (`opencode` or `builtin`); builtin remains the automatic fallback if OpenCode is unavailable |
| `STUDIO_BUILD_TIMEOUT` | `1800` | Seconds before a Build Image action (`apptainer build`, plus push) is aborted |
| `STUDIO_BUILD_APT_FIX` | `1` | Bind an apt.conf that keeps apt as root during `%post` (fixes the `_apt` privilege-drop where root is emulated); set `0` for non-Debian bases |
| `STUDIO_BUILD_FAKEROOT` | _(unset)_ | Force `apptainer build --fakeroot` on (`1`) or off (`0`); unset follows the process euid |
| `PEGASUS_BOT_SERVER_URL` | `http://server.ai-bot:8000` | Upstream PegasusAI Bot server the in-pod proxy (`/api/pegasus-bot`) forwards to |
| `PEGASUS_BOT_CLIENT_URL` | `<prefix>/api/pegasus-bot` | Server URL injected into the extension; defaults to the same-origin proxy path. Override to point the browser at a directly-reachable server |
| `STUDIO_ENABLE_PEGASUS_BOT` | `1` | Inject the PegasusAI Bot config (server + env user) into JupyterLab (`0` leaves the bundled extension inert / manual) |

### HTCondor connection

Studio is a pure HTCondor **client** (the container runs no Condor daemons); it
submits and monitors on the pool named by `CONDOR_HOST` in
`docker/htcondor/90-studio.conf`, baked into the image at
`/etc/condor/config.d/90-studio.conf` (default AP `ap.pegasus-ai.org`). In
production, studio-system injects the trust domain and per-user IDTOKEN at
spawn; the image ships no credentials.

Connecting to a different host is two things:

1. **Point at the host.** HTCondor honors `_CONDOR_<KNOB>` env vars, which
   override the baked config with no rebuild. `local-test-instance.sh` wires
   these for you via `CONDOR_HOST` / `CONDOR_SCHEDD_HOST` (or mount a full
   drop-in with `CONDOR_CONFIG_DROPIN`).
2. **Authenticate.** `condor_q`/`condor_status` and `pegasus-plan --submit`
   only work against a pool that trusts your client, so you need an HTCondor
   **IDTOKEN** for that pool's trust domain in the token directory — pass the
   host dir via `CONDOR_TOKENS_DIR` (mounted read-only). The token's issuer
   (`iss`) must match the pool's trust domain, and the AP's ports must be
   reachable. Without a token the UI still loads but the Resources panel shows
   "unavailable" and submits fail auth.

The separate OSPool (OSG) capacity panel is configurable via `STUDIO_OSPOOL_CM`
(empty disables it) and `STUDIO_OSPOOL_OWNER`.

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

Tests / gates (CI runs neither — run before every PR):

```bash
cd studio-api && pytest -q                       # ~235 tests
cd studio-web && node_modules/.bin/tsc --noEmit   # NOT `npm test`/`npm run lint` — those don't work
```

## Source Layout

| Path | Contents |
|------|----------|
| [Dockerfile](Dockerfile) | Multi-stage container build |
| [docker/](docker/) | nginx, Jupyter, HTCondor, entrypoint, and supervisord config |
| [studio-api/](studio-api/) | FastAPI backend, SQLite state, tool adapters, workflow services |
| [studio-web/](studio-web/) | Next.js frontend |
| [knowledge/overlay/](knowledge/overlay/) | Studio-specific environment context; the shared skills/agents come from the marketplace checkout at `/opt/pegasus-ai/marketplace` |
| [SPEC.md](SPEC.md) | Current design specification |

## License

Apache License 2.0.
