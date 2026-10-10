# PegasusAI Studio

PegasusAI Studio is a single-user web IDE for building, running, and monitoring
[Pegasus WMS](https://pegasus.isi.edu/) workflows. It packages a Next.js UI,
FastAPI backend, JupyterLab, Pegasus WMS, HTCondor, Apptainer, workflow
monitoring, and AI coding tools into one `linux/amd64` container.

Authentication is handled upstream by JupyterHub/KubeSpawner, Open OnDemand, or
an ingress layer. The container has no built-in auth and runs as the UID provided
by the spawner.

Two images share this codebase: the Debian-based one this README covers
(spawned by KubeSpawner/JupyterHub), and a Rocky9/Apptainer build for [Open
OnDemand](ood-app/README.md) — see that README and
[SPEC.md](SPEC.md#open-ondemand--apptainer-packaging) for what differs and why.

## Runtime

```
Browser
  |
JupyterHub / OOD / ingress
  |
nginx :8888                            STUDIO_PORT
  |-- /          -> studio-web  :3000  STUDIO_WEB_PORT
  |-- /api/*     -> studio-api  :8080  STUDIO_API_PORT
  |-- /ws/*      -> studio-api  WebSocket PTYs
  `-- /jupyter/* -> JupyterLab  :8889  STUDIO_JUPYTER_PORT
                    opencode    :4096  STUDIO_OPENCODE_PORT
```

**Those numbers are defaults.** Every listener is configurable, because the
image also runs on shared hosts — under Open OnDemand, where Apptainer gives the
container the *host* network namespace by default, two users' sessions would
otherwise collide on the same ports. Set the variables above, or set
`STUDIO_ALLOC_PORTS=1` to have the entrypoint pick free ports for the four
internal listeners. The front port is never auto-allocated: whatever publishes
it has to know it in advance.

The internal services bind `127.0.0.1` (`STUDIO_BIND_ADDR`); only nginx answers
on all interfaces. Per-session runtime state — rendered nginx configs, pid
files, the supervisor socket, nginx temp dirs — lives in `STUDIO_RUNTIME_DIR`
(default `/tmp/studio-runtime-<uid>`), uid-suffixed so two sessions on one host
stay apart.

Long-running services are managed by `supervisord`
([docker/supervisor/supervisord.conf](docker/supervisor/supervisord.conf)),
which takes those ports through its own `%(ENV_VAR)s` expansion. The nginx
configs are templates ([docker/nginx/](docker/nginx/)) rendered per session by
the entrypoint, because `/etc` is not writable by the injected uid.
JupyterLab is always on and opens in a separate browser tab.

## Architecture Notes

- Runtime image: `debian:trixie-slim`, system Python 3.13, Node.js 20. The
  distro matches the HTCondor access point, because Pegasus bakes its venv's
  Python minor version into every submit file it writes (#334).
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
| `IMAGE` / `PORT` | Image to run / host port to publish (default `pegasus/studio-webapp:latest`, matches `STUDIO_PORT`) |
| `STUDIO_PORT` | Port nginx listens on *inside* the container (default `8888`); the `-p` mapping follows |
| `ALLOC_PORTS=1` | Pick free ports per session for the four internal listeners — the Open OnDemand shape. The published port is unchanged, so a run that behaves differently from a default one means the port plumbing is wrong |
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

The shell is a **top bar** (wordmark, ⌘K search, account menu) above a **72px
icon rail**. Every rail cell carries a short label under its glyph.

| View (rail label) | Route | Purpose |
|------------------|-------|---------|
| Home | `/` | Welcome, four ways in, a gallery of openers that hand a prompt to the assistant, and a strip naming anything that needs attention |
| Analyses | `/workflows` | Discover projects and runs; set up and run a workflow (collapsible), watch the graph, read results, diagnose failures. Click a step in the graph to see the files it reads and writes (with previews); **Show files** draws them as nodes. **Rename** moves a project's folder (refused while a run is in progress) |
| Data | `/data` | Drag-and-drop your own input files into the workspace; copy a path to paste into a run |
| Examples | `/marketplace` | Browse workflows sourced live from GitHub: every public repo in the configured orgs with the `pegasus-studio-example` topic, so listing one is `gh repo edit pegasus-isi/<repo> --add-topic pegasus-studio-example`. The PegasusHub registry only enriches tagged repos it knows (id, image, Pegasus version, "PegasusHub" label). Configured by `STUDIO_MARKETPLACE_GITHUB_ORGS` (default `pegasus-isi`), `STUDIO_MARKETPLACE_TOPIC`, `STUDIO_MARKETPLACE_REGISTRY_URL` (empty disables) and `STUDIO_MARKETPLACE_UNTAGGED_REGISTRY=1` (list every registry entry, the pre-#462 behaviour), filter by subject/source, and "Add" (git-clone) one into the workspace. A workflow already in the workspace shows **In your workspace · Open** |
| Notebooks | `/notebooks` | JupyterLab, framed in Studio, with "Open in new tab" and Reload. `?path=` opens a specific folder |
| Resources | `/provisioning` | Whether there is capacity to run now, with HTCondor pool inventory, workflow demand, pressure findings and an OSPool (OSG) panel under Details |
| Settings | `/settings` | Notifications, Pegasus Options (Simplified Run, welcome page & tour, scan roots), Chat (engine, command approval, chat-text sharing), Feedback & privacy; LLM providers and **Tools** under Advanced |
| PegasusAI Chat | (resizable slide-in panel + `/chat`) | The assistant, on the OpenCode engine by default (the builtin loop stays selectable), with Pegasus tools over MCP; per-turn approval before shell commands run |
| Terminal | (panel) | Browser PTY connected to the workspace |

**Onboarding** is a guided tour that spotlights real elements rather than a
card describing them. It runs on every login (once per browser session) until
the user ticks **Don't show me this tour again** or turns it off in Settings,
and is restartable any time from **Take a tour** in the account menu. After the
walkthrough it says what Studio records and asks, until answered, whether chat
text may be shared (opt-in, off by default) and four optional profile questions.

**Running a workflow.** One **Run** button prepares and starts the workflow —
generating it, building the software environment only if it is stale, planning
and submitting — and reports each stage in plain language. The four Pegasus
steps remain individually available under "Advanced" on each workflow, and
turning off **Simplified Run** in Settings restores them as the primary
controls along with the full navigation.

The run form is built at runtime from each project's `workflow_generator.py`
`argparse` definition, so every workflow gets its own parameters, with dates,
numbers and file pickers inferred where the definition allows it. Anything that
cannot be inferred stays a plain text field, and `argparse` remains the
validator.

When a run finishes, its output files are on the **Results** tab, with previews
for images and PDFs (up to 25 MB), CSV/TSV as a sortable, filterable table, JSON
as a collapsible tree, and text. Text and tables over 2 MB preview their first
2 MB. When one fails, the failure is reported as a cause
and a suggested action, with the raw log kept underneath and an "Explain this
failure" button that hands it to the assistant.

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
| `STUDIO_SCAN_DIR` | `$HOME` | Root scanned for `braindump.yml` runs (the run list + notification scan). Users can add scan roots/excludes on top — see [Workflow discovery](#workflow-discovery) |
| `STUDIO_SECRET_KEY` / `STUDIO_SECRET_KEY_FILE` | unset | Master secret used to encrypt stored API keys + the registry password at rest. Unset → stored plaintext with a warning. Must come from a sealed secret, **not** the PVC |
| `STUDIO_LLM_GATEWAY_URL` | internal | Base URL for the zero-config `gateway` provider |
| `STUDIO_ALLOWED_ORIGINS` | (empty) | Extra CORS origins (same-origin is always allowed) |
| `STUDIO_TRUST_AUTH_HEADER` | `0` | Trust `X-Auth-User` for the display identity (opt-in; prod identity comes from `JUPYTERHUB_USER`) |
| `NPM_CONFIG_PREFIX` | `/opt/npm-global` | npm global install prefix |
| `STUDIO_POOL_CACHE_TTL` | `15` | Seconds between HTCondor pool/queue snapshots (provisioning + dashboard) |
| `STUDIO_RESOURCE_WAIT_SECONDS` | `300` | Idle-job queue wait past which a resource alert is raised |
| `STUDIO_OSPOOL_CM` | `cm-1.ospool.osg-htc.org` | OSPool central manager for the Provisioning OSPool panel (empty disables it) |
| `STUDIO_OSPOOL_CACHE_TTL` | `120` | Seconds to cache the (slow, remote) OSPool query |
| `STUDIO_DEFAULT_TOOLS` | `opencode` | Image-bundled tools registered as installed at startup (csv; empty disables) |
| `STUDIO_BUILD_TIMEOUT` | `1800` | Seconds before a Build Image action (`apptainer build`, plus push) is aborted |
| `STUDIO_BUILD_FAKEROOT` | _(unset)_ | Force `apptainer build --fakeroot --ignore-fakeroot-command` on (`1`) or off (`0`); unset follows the process euid |
| `PEGASUS_BOT_SERVER_URL` | `http://server.ai-bot:8000` | Upstream PegasusAI Bot server the in-pod proxy (`/api/pegasus-bot`) forwards to |
| `PEGASUS_BOT_CLIENT_URL` | `<prefix>/api/pegasus-bot` | Server URL injected into the extension; defaults to the same-origin proxy path. Override to point the browser at a directly-reachable server |
| `STUDIO_EXEC_SITE` | _(unset)_ | Execution site the Run form preselects and always offers (e.g. `compute` on a Slurm cluster). Unset: the project's `sites.yml` / hosted catalog decide, else `condorpool`. See [Where it runs](#where-it-runs) |
| `STUDIO_SITE_STYLE` / `STUDIO_SITE_QUEUE` / `STUDIO_SITE_PROJECT` | _(unset)_ | Prefill a generator's `--site-style`, `--queue` and `--project` (portable-sites generators), e.g. `slurm`, the partition and the allocation on a Slurm-only install |
| `STUDIO_ENABLE_PEGASUS_BOT` | `1` | Inject the PegasusAI Bot config (server + env user) into JupyterLab (`0` leaves the bundled extension inert / manual) |

### Workflow discovery

How things show up on the **My analyses** page (`services/workflow_scanner.py`):

- **Projects** are the immediate subdirectories of
  `$STUDIO_WORKSPACE_DIR/workflows` that look like a workflow: a generator
  (`workflow_generator.py`, `workflow-generator.py`, or a custom name declared
  under `scripts.generator` in a `.pegasushub.yml` marker), a top-level
  workflow YAML (anything but `braindump.yml`), or the `.pegasushub.yml`
  marker itself (so a freshly-cloned PegasusHub repo appears before its first
  Generate).
- **Runs** are found by recursively scanning for `braindump.yml` — the file
  Pegasus writes into every submit directory at plan time — under
  `$STUDIO_SCAN_DIR` (default: the whole home directory). Runs inside a
  project's tree are grouped under it; the rest are listed standalone. Paths
  containing `test(s)/`, `.git/`, `node_modules/`, `site-packages` and the
  Pegasus sample-data fragments are skipped so cloned repos don't pollute the
  list.

Both scans are extendable per user in **Settings → Pegasus Options →
Workflow discovery** (stored in the workspace DB, `GET/PUT
/api/settings/workflow-scan`):

- **Extra scan directories** — absolute paths also scanned for runs, e.g. a
  mounted volume outside the home dir. Merged with `$STUDIO_SCAN_DIR`; the
  notification engine watches the same effective set, so an added run is both
  listed and alerted on.
- **Exclude path fragments** — plain substrings matched against run-directory
  paths, for hiding a noisy subtree or keeping the recursive scan out of a
  large data directory. Merged with the builtin list.

User additions extend the deployment defaults; they never replace them, so
misconfiguration can't empty the Analyses page. Project-detection rules
(generator filenames, the `workflows/` directory) are Pegasus/PegasusHub
conventions and are deliberately not configurable.

### Where it runs

The Run form's "Where it runs" choices are the sites the project can actually
plan against, not a fixed pair: "This machine" (`local`), plus every site in
the project's `sites.yml`, the sites of a hosted site catalog named in
`~/.pegasusrc` (`pegasus.catalog.site.repo.file`; hosted catalogs call theirs
`compute`), and `STUDIO_EXEC_SITE`. A condor pool shows as "Shared cluster", a
Slurm or other batch site as "HPC cluster". When none of those defines an
execution site, the default HTCondor `condorpool` is offered, because
generators built on the pegasus-ai `custom_sites.py` template create it in
that case; otherwise the project's own site is preselected.

Those generators state only cores, memory and runtime, and take the site's
scheduler settings as options (`--site-style`, `--queue`, `--project`, ...).
On a Slurm-only install (e.g. Open OnDemand), set `STUDIO_EXEC_SITE=compute`,
`STUDIO_SITE_STYLE=slurm`, `STUDIO_SITE_QUEUE=<partition>` and
`STUDIO_SITE_PROJECT=<account>` in the deployment environment, or point
`~/.pegasusrc` at the cluster's hosted catalog: users then get a working Run
form with no site setup. Generators without those options are unaffected.

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
cd studio-api && pytest -q                       # ~930 tests
cd studio-web && node_modules/.bin/tsc --noEmit   # NOT `npm test`/`npm run lint` — those don't work
```

## Source Layout

| Path | Contents |
|------|----------|
| [Dockerfile](Dockerfile) | Multi-stage container build |
| [docker/](docker/) | nginx, Jupyter, HTCondor, entrypoint, and supervisord config (shared with `ood-app/`) |
| [studio-api/](studio-api/) | FastAPI backend, SQLite state, tool adapters, workflow services |
| [studio-web/](studio-web/) | Next.js frontend |
| [knowledge/overlay/](knowledge/overlay/) | Studio-specific environment context; the shared skills/agents come from the marketplace checkout at `/opt/pegasus-ai/marketplace` |
| [ood-app/](ood-app/README.md) | Open OnDemand/Apptainer image build (Rocky9), for HPC clusters instead of K8s |
| [SPEC.md](SPEC.md) | Current design specification |

## License

Apache License 2.0.
