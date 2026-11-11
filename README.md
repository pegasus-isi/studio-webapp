# PegasusAI Studio

PegasusAI Studio is a single-user web IDE for building, running, and monitoring
[Pegasus WMS](https://pegasus.isi.edu/) workflows. It packages a Next.js UI,
FastAPI backend, JupyterLab, Pegasus WMS, HTCondor, Apptainer, workflow
monitoring, and AI coding tools into one `linux/amd64` container.

Studio has no login screen or user accounts of its own. It runs one container
per user, as the UID the spawner provides, and takes identity from what spawned
it, checking it on every request:

- **JupyterHub/KubeSpawner:** nginx asks studio-api (`routers/hub_auth.py`)
  about each request, which runs the hub's OAuth flow and sets a signed session
  cookie scoped to the server's prefix (#592). The entrypoint turns this on
  (`STUDIO_HUB_AUTH=1`) when the hub's OAuth variables are present.
- **Open OnDemand:** a per-session token gate (`STUDIO_AUTH_TOKEN`); see
  [ood-app/README.md](ood-app/README.md).
- A local run with neither (`local-test-instance.sh`) is unauthenticated.

See [SECURITY.md](SECURITY.md) for the security model and how to report a
vulnerability.

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
- Workflow status comes from a run's JSONL event log (`workflow-events.jsonl`
  from `workflow-monitor --serve`, or `wfevents.jsonl` from pegasus-monitord's
  `wfevents` plugin), falling back to the stampede DB when neither exists.
- The workspace root is `$HOME` (#401): projects live in `~/workflows`,
  uploads in `~/data`, Studio's own state in `~/.studio`, and generated tool
  config (`CLAUDE.md`, `AGENTS.md`, `.mcp.json`) at `$HOME`. Projects under the
  older `~/work/workflows` are still listed, never written to. Files holding
  credentials (`~/.studio/studio.db`, the generated OpenCode and Codex configs)
  are written `0600` in `0700` directories (#600).

## Running locally (for testing)

In production the image is built and published by CI (a merge to `main` pushes
`ghcr.io/pegasus-isi/studio-webapp:main`, which the deployment repo,
studio-system, pulls). To build and run it yourself for testing:

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
| `NAME` | Container name, so a test harness can `docker exec` into it later (default: docker assigns one) |
| `DETACH=1` | Run in the background and print the container id instead of holding the terminal |
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

The shell is a **top bar** (wordmark and ⌘K search) above a **72px icon
rail**. Every rail cell carries a short label under its glyph. The foot of the
rail holds **Website** (pegasus-ai.org, in a new tab), **Feedback**,
**Settings**, and the account menu (Application Settings, Take a tour, Help,
PegasusAI website, and under JupyterHub the server control panel and sign
out). Studio opens on Home: `/` and the old `/welcome` address both redirect
to `/home`.

| View (rail label) | Route | Purpose |
|------------------|-------|---------|
| Home | `/home` | Welcome, four ways in, a gallery of openers that hand a prompt to the assistant, and a strip naming anything that needs attention |
| Analyses | `/workflows` | Discover projects and runs; set up and run a workflow (collapsible), watch the graph, read results, diagnose failures. Click a step in the graph to see the files it reads and writes (with previews); **Show files** draws them as nodes. **Rename** moves a project's folder (refused while a run is in progress) |
| Data | `/data` | Drag-and-drop your own input files into the workspace; copy a path to paste into a run |
| Examples | `/marketplace` | Browse workflows sourced live from GitHub: every public repo in the configured orgs with the `pegasus-studio-example` topic, so listing one is `gh repo edit pegasus-isi/<repo> --add-topic pegasus-studio-example`. The PegasusHub registry only enriches tagged repos it knows (id, image, Pegasus version, "PegasusHub" label). Configured by `STUDIO_MARKETPLACE_GITHUB_ORGS` (default `pegasus-isi`), `STUDIO_MARKETPLACE_TOPIC`, `STUDIO_MARKETPLACE_REGISTRY_URL` (empty disables) and `STUDIO_MARKETPLACE_UNTAGGED_REGISTRY=1` (list every registry entry, the pre-#462 behaviour), filter by subject/source, and **Add to workspace** (git-clone) one into `~/workflows`. A workflow already there shows **In your workspace** with **Open** |
| Notebooks | `/notebooks` | JupyterLab, framed in Studio, with "Open in new tab" and Reload. `?path=` opens a specific folder |
| Resources | `/provisioning` | How much computing power there is and how to get more, one section at a time from a menu on the left (collapsible; the section is in the URL hash). **Overview** has every capacity number: CPUs and GPUs free, steps waiting or held, machines, and waiting work by workflow. **OSPool** shows OSPool (OSG) capacity and, on the hosted deployment, holds the OSPool project name. The third section depends on the deployment (#532): hosted Studio has **Bring Your Own Compute**, which leases nodes from a cluster or ACCESS allocation the user already has as an HTCondor annex (create and download the setup file, status, shut down; cluster presets for Expanse, Anvil, Delta and Bridges-2, CPU or GPU nodes), with cloud and testbed providers marked as coming. Under Open OnDemand it is **Where workflows run**: the administrator's cluster catalog, the allocation fields it needs (saved to `~/.pegasusrc`, #468), and optional per-cluster site settings with a read-only view of the merged site (#478). Always in the rail; only Terminal sits behind **More** |
| Settings | `/settings` | The page is titled **Application Settings**. Notifications; Pegasus Options (Simplified Run, guided tour, where planning runs, workflow secrets, container registry, workflow discovery); Chat (engine, command approval, chat-text sharing); Feedback & privacy; **LLM Providers** and **Tools** under Advanced |
| PegasusAI Chat | (resizable slide-in panel + `/chat`) | The assistant, on the OpenCode engine, with Pegasus tools over MCP. With **Confirm shell commands** on (the default), it asks before running a shell command and before writing files outside `~/workflows` and `~/data` (#596). Replies can be rated 👍/👎 |
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

**Choose where to run the workflow**, in the Run panel, is where a run goes. On the hosted
deployment the choice is whose machines: **Studio's Compute Resources** (the
default: Studio's pool, and the OSPool for workflows with a project name) or
**Bring Your Own Compute** (only the annex nodes the user connects on
Resources). It is saved per workflow, as a site requirements profile in the
project's own `pegasus.properties`, so a `pegasus-plan` typed in a terminal
does the same as Run (`services/run_placement.py`, #612). *This machine*
(the `local` site, every step inside the user's own container) is not offered
on hosted unless a workflow already has it saved; under Open OnDemand it is
called **This Studio session's node**, since it runs on the one compute node
the session holds. Whether a step needs a GPU is the workflow's to declare
(Pegasus `gpus` or HTCondor `request_gpus` profiles); the panel names those
steps and warns when no machine has a free GPU (#576).

Before planning, a run that assumes a shared filesystem the HTCondor pool does
not have (`sharedfs`, bypassed input staging under `nonsharedfs`, a shared
scratch directory, or a container mount from the home) is stopped with a
message naming the setting and the deployment (`services/shared_fs.py`,
#520). While a run goes, a step that has run at least 10 minutes and at least
5 times longer than that step's usual duration is flagged (#586), and the
Steps tab shows the machine each step **Ran on**, summarised as **Where it
ran** (#556).

The run form is built at runtime from each project's `workflow_generator.py`
`argparse` definition, so every workflow gets its own parameters, with dates,
numbers and file pickers inferred where the definition allows it. Anything that
cannot be inferred stays a plain text field, and `argparse` remains the
validator.

When a run finishes, its output files are on the **Results** tab, with previews
for images and PDFs (up to 25 MB), HTML reports rendered in a sandbox (scripts
run but cannot reach Studio), CSV/TSV as a sortable, filterable table, JSON as
a collapsible tree, and text. Every inline preview except raster images and
PDF is served with a CSP `sandbox` (#594), and previews never serve files from
protected directories such as `~/.ssh` or the HTCondor token (#602). Text
and tables over 2 MB preview their first 2 MB. When one fails, the failure is reported as a cause
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
JupyterLab page at server start. Local changes to the vendored source are
limited to `src/configPlugin.tsx`, which reads that injected config, and
`src/api.ts` + `src/toolbarPlugin.ts`, which show the server's own error
message. See `VENDORED.md` to update the copy.

Because the bot server is a cluster-internal HTTP service (not reachable from
the browser over the HTTPS studio origin), the extension is pointed at a
same-origin proxy — `studio-api` `/api/pegasus-bot` forwards `search`/`feedback`
to `PEGASUS_BOT_SERVER_URL` from inside the pod. Set `PEGASUS_BOT_CLIENT_URL`
to bypass the proxy if the server is directly browser-reachable.

## Configuration

> **LLM providers and API keys are configured in the Settings UI, not here.**
> They're stored in the workspace SQLite DB; the default `gateway` provider
> needs no key, so chat and tools work out of the box. Where there is no
> gateway (`STUDIO_LLM_GATEWAY_URL` set to empty, the Open OnDemand image's
> default unless the site names one), nothing is configured until the user adds
> a provider with their own key; every saved provider is offered in the chat's
> model picker, and chat says so when none exists (#498, #505). Shared keys are
> never injected.

| Variable | Default | Description |
|----------|---------|-------------|
| `STUDIO_WORKSPACE_DIR` | `$HOME` | Workspace root: projects in `workflows/`, uploads in `data/` |
| `STUDIO_SCAN_DIR` | `$HOME` | Root scanned for `braindump.yml` runs (the run list + notification scan). Users can add scan roots/excludes on top — see [Workflow discovery](#workflow-discovery) |
| `STUDIO_SECRET_KEY` / `STUDIO_SECRET_KEY_FILE` | unset | Master secret used to encrypt stored API keys + the registry password at rest. Unset → stored plaintext with a warning. Must come from a sealed secret, **not** the PVC |
| `STUDIO_LLM_GATEWAY_URL` | internal | Base URL for the zero-config `gateway` provider. Set to an empty string for **no gateway**: no default provider, the preset is not offered, users add their own (#498) |
| `STUDIO_LLM_DEFAULT_MODEL` | `default` | Model the `gateway` provider asks for (the hosted gateway is case-sensitive: lowercase) |
| `STUDIO_ALLOWED_ORIGINS` | (empty) | Extra CORS origins (same-origin is always allowed) |
| `STUDIO_TRUST_AUTH_HEADER` | `0` | Trust `X-Auth-User` for the display identity (opt-in; prod identity comes from `JUPYTERHUB_USER`) |
| `STUDIO_HUB_AUTH` | _(set by the entrypoint)_ | JupyterHub OAuth check on every request (#592). On when the hub's OAuth variables are present and `STUDIO_AUTH_TOKEN` is not; `0` forces it off |
| `STUDIO_DEPLOYMENT` | _(derived)_ | `hosted` or `ood`: which Resources sections and Run choices are shown. Derived from whether an administrator catalog is configured (`STUDIO_SITE_CATALOG_FILE` or `STUDIO_SITE_CATALOG` means `ood`); set it to try either shape locally |
| `STUDIO_OTLP_ENDPOINT` / `STUDIO_OTLP_PROTOCOL` | _(unset)_ / `http/protobuf` | OpenTelemetry collector for chat telemetry. Unset: nothing is exported. Set: the OpenCode engine's metrics (token counts, durations, no text) and a gauge of chat reply ratings go out for everyone; traces, which carry prompt text, and per-vote rating spans only for users who opt in under Feedback & privacy (#608) |
| `NPM_CONFIG_PREFIX` | `/opt/npm-global` | npm global install prefix |
| `STUDIO_POOL_CACHE_TTL` | `15` | Seconds between HTCondor pool/queue snapshots (Resources + dashboard) |
| `STUDIO_RESOURCE_WAIT_SECONDS` | `300` | Idle-job queue wait past which a resource alert is raised |
| `STUDIO_OSPOOL_CM` | `cm-1.ospool.osg-htc.org` | OSPool central manager for the Resources OSPool section (empty disables it) |
| `STUDIO_OSPOOL_CACHE_TTL` | `120` | Seconds to cache the (slow, remote) OSPool query |
| `STUDIO_DEFAULT_TOOLS` | `opencode` | Image-bundled tools registered as installed at startup (csv; empty disables) |
| `STUDIO_BUILD_TIMEOUT` | `1800` | Seconds before a Build Image action (`apptainer build`, plus push) is aborted |
| `STUDIO_BUILD_FAKEROOT` | _(unset)_ | Force `apptainer build --fakeroot --ignore-fakeroot-command` on (`1`) or off (`0`); unset follows the process euid |
| `PEGASUS_BOT_SERVER_URL` | `http://server.ai-bot:8000` | Upstream PegasusAI Bot server the in-pod proxy (`/api/pegasus-bot`) forwards to |
| `PEGASUS_BOT_CLIENT_URL` | `<prefix>/api/pegasus-bot` | Server URL injected into the extension; defaults to the same-origin proxy path. Override to point the browser at a directly-reachable server |
| `STUDIO_SITE_CATALOG` | _(unset)_ | Published site catalog (e.g. `unity.yml`) written to each user's `~/.pegasusrc` at startup when it names none, so a cluster install needs no per-user setup beyond the allocation, which users enter on Resources. Setting it makes the deployment `ood`. Unset: Studio's own pool (`pegasus-studio.yml`, site `compute`) |
| `STUDIO_SITE_CATALOG_FILE` | _(unset)_ | A site's own Pegasus site catalog (a path) that **replaces** Studio's `pegasus-studio.yml` for every user, so `compute` means that cluster (e.g. a Slurm partition through HTCondor BLAHP). Copied into each user's `~/.studio/site-catalogs/<series>/conf/` at every start (#496) |
| `STUDIO_SITE_CATALOG_LABEL` | `This cluster` | What the Resources page calls that catalog, in place of "Studio's own pool" |
| `STUDIO_HOME` | _(unset)_ | Studio's own home directory under Open OnDemand. The batch-connect app runs the image with `apptainer --home "$STUDIO_HOME"`; if a launch only sets the variable, the entrypoint exports `HOME` from it (#482). Not to be confused with `local-test-instance.sh`'s `STUDIO_HOME`, a host directory to persist the container home |
| `STUDIO_EXEC_SITE` | _(unset)_ | Execution site the Run form preselects and always offers (e.g. `compute` on a Slurm cluster). Unset: the project's `sites.yml` / hosted catalog decide, else `condorpool`. See [Where it runs](#where-it-runs) |
| `STUDIO_SITE_STYLE` / `STUDIO_SITE_QUEUE` / `STUDIO_SITE_PROJECT` | _(unset)_ | **Legacy.** Prefill `--site-style`, `--queue` and `--project` for generators that still have them (the retired portable-sites template). Template generators take scheduler details from the site catalog instead: use `STUDIO_SITE_CATALOG` or Resources |
| `STUDIO_ENABLE_PEGASUS_BOT` | `1` | Inject the PegasusAI Bot config (server + env user) into JupyterLab (`0` leaves the bundled extension inert / manual) |

### Workflow discovery

How things show up on the **Analyses** page (`services/workflow_scanner.py`):

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

Workflows follow the pegasus-gromacs template: they never define where they
run, and plan on a site named **`compute`** that a site catalog defines. A
catalog is named in `~/.pegasusrc` (`pegasus.catalog.site.repo.file`):

- **Studio's own pool** (the default). The image ships
  `/opt/studio/site-catalogs/<MAJOR.MINOR>/conf/pegasus-studio.yml`, defining
  `compute` (and `condorpool`, for workflows generated before the template) as
  Studio's HTCondor pool. The entrypoint copies it to `~/.studio/site-catalogs`
  — the home directory is the submit host's own NFS export, so the planner can
  read it there, which it cannot under `/opt` in the container (#476) — and
  points `~/.pegasusrc` at that copy with a `file://` repo base when no catalog
  is chosen (#474). The template default,
  the notebooks and every README's `pegasus-plan -s compute` work unchanged.
- **A published cluster catalog**, named for everyone by an administrator
  with `STUDIO_SITE_CATALOG` (a catalog in pegasushub/pegasus-site-catalogs).
  The user fills in the `RESOURCE_*` allocation fields it uses on
  **Resources → Where workflows run** (#468). These also call their site
  `compute`. Users no longer pick from the published list (#532): on the
  hosted deployment more compute comes from an annex (Bring Your Own
  Compute), and under Open OnDemand the cluster is the install.
- **A site's own catalog** (`STUDIO_SITE_CATALOG_FILE`, set by an
  administrator, e.g. in the Open OnDemand app's `site.env`): it takes the
  place of `pegasus-studio.yml`, so the default above means *this* cluster
  and everything built on it (the shared copy, `~/.pegasusrc`, Resources,
  site overrides) works unchanged (#496).

Either way the catalog is "hosted": a project's own `sites.yml` is merged over
it, never replaced. The Run form offers the sites the project can plan against
(its `sites.yml`, the catalog's sites, `STUDIO_EXEC_SITE`, and `local`, which
hosted Studio hides; see **Choose where to run the workflow** under
[Main Views](#main-views)), and Run passes the chosen one as the generator's
`-e/--execution-site-name` (the template binds transformations to it, so it must equal the plan's `-s`),
plans with `--output-dir <project>/output`, and refuses a site nothing defines
before starting (#466). Only with no catalog at all is Pegasus's built-in
`condorpool` the default.

### HTCondor connection

Studio is a pure HTCondor **client** (the container runs no Condor daemons); it
submits and monitors on the pool named by `CONDOR_HOST` in
`docker/htcondor/90-studio.conf`, baked into the image at
`/etc/condor/config.d/90-studio.conf` (default AP `ap.pegasus-ai.org`). The
same file enables `htcondor annex` (`feature:HPC_ANNEX`, `ANNEX_COLLECTOR`) for
Bring Your Own Compute; annex nodes are named after the user and report to the
access point's annex collector, not the pool's central manager
(`services/annex.py`). In production, studio-system injects the trust domain
and per-user IDTOKEN at spawn; the image ships no credentials.

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

**Planning on the submit host.** When the schedd is on another machine, Studio
runs `pegasus-plan` there as an HTCondor local-universe job
(`services/remote_exec.py`), so the plan names the submit host's own paths.
The run itself is still submitted from the session, so the submit host's
Pegasus must be at the same paths (and the same series, whose site catalogs
Studio ships: a different series is warned about in the run, #490). Three
details matter off the hosted path, and are handled:

- The schedd starts local- and scheduler-universe jobs (the planning job,
  DAGMan) from its `SCHEDD_INTERVAL` timer, 300 s by default. Studio sends
  `condor_reschedule` after every submit (`services/schedd.py`, #488, #494),
  the same authorization a submit needs.
- HTCondor resets `HOME` in a local-universe job to the account's passwd home;
  the wrapper exports Studio's `HOME` itself, or DAGMan and `pegasus-monitord`
  would keep Pegasus's master database in the user's real home (#508).
- The JVM takes `user.home` from passwd, so `JAVA_TOOL_OPTIONS=-Duser.home`
  points the planner at Studio's `~/.pegasusrc` (#489); the entrypoint sets it
  too wherever passwd's home is not `$HOME`.

The separate OSPool (OSG) capacity panel is configurable via `STUDIO_OSPOOL_CM`
(empty disables it) and `STUDIO_OSPOOL_OWNER`.

## Development

Backend:

```bash
cd studio-api
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt pytest httpx
uvicorn main:app --reload --port 8080
```

Frontend:

```bash
cd studio-web
npm ci
npm run dev
```

Tests / gates. CI (`.github/workflows/deploy.yml`) runs all three as the job
that gates the image build; run them before every PR anyway:

```bash
(cd studio-api && pytest -q)                        # ~1150 tests; no Pegasus/Condor/keys needed
(cd studio-web && node_modules/.bin/tsc --noEmit)   # strict mode
(cd studio-web && npm test)                         # vitest, lib/ only
```

`npm run lint` is not a gate (eslint is not installed). Pull requests target
`develop`, whose pushes publish `ghcr.io/pegasus-isi/studio-webapp:develop`
for testing; a `develop` to `main` release publishes `:main`.

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
