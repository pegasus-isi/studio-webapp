# PegasusAI Studio as an Open OnDemand App — Design

Status: DRAFT v3 (2026-08-24, reviewed against the code). Target: deploy `studio-webapp` on any lab server
that runs Open OnDemand, has multiple POSIX user accounts, has Pegasus
installed on the host, and may or may not be an HTCondor submit host (possibly
pointing at a remote condor cluster).

**Two deployment models, one codebase.** The existing container/K8s deployment
(JupyterHub/KubeSpawner on studio-system) stays as-is. The OOD deployment runs
Studio **natively** — the user's own processes on the host, using the host's
Pegasus, Condor client, and Apptainer directly, no container. Both models must
be supported from the same repo without divergence.

## 1. The core design move: a launcher contract, two packagings

Today the "how Studio starts" knowledge lives in container-only places:
`docker/entrypoint.sh`, `docker/supervisor/supervisord.conf`,
`docker/nginx/default.conf`. A native deployment cannot reuse any of it, and a
copy would drift (the lesson the knowledge base already taught us once).

Extract it into a single, environment-driven launcher that both models call:

```
studio-launch            # one command, runs as the user
  ├─ prepare $HOME state        (what entrypoint.sh does today: ~/.studio,
  │                              ~/work, marketplace clone, init_db)
  ├─ render configs from env    (proxy conf, supervisord conf, jupyter args)
  │     STUDIO_PORT, STUDIO_URL_PREFIX, STUDIO_AUTH_TOKEN,
  │     STUDIO_SECRET_KEY, STUDIO_LLM_GATEWAY_URL, _CONDOR_*, ...
  └─ exec supervisord           (proxy, Next.js, FastAPI, JupyterLab —
                                 NOT opencode serve: studio-api spawns and
                                 owns that itself (services/opencode_engine.py),
                                 already env-tunable via STUDIO_OPENCODE_BIN
                                 and STUDIO_OPENCODE_PORT)
```

- **Container packaging**: the Dockerfile installs `studio-launch` and the
  entrypoint becomes a thin wrapper around it. K8s behavior is unchanged
  (same defaults: port 8888, `/user/<name>` prefix compat, no auth gate).
- **Native packaging**: a CI-built release tarball (see §6) unpacked at a
  shared path; the OOD app's job script calls the same `studio-launch`.

Everything below — prefix, port, auth, condor knobs — is a property of the
launcher, so it works identically in both models and is tested once.

## 2. OOD deployment shape: batch-connect interactive app, native runtime

OOD authenticates at the portal (lab IdP / PAM / Keycloak) and launches a
batch-connect job **as the user**; the job runs `studio-launch` on a free
port; OOD reverse-proxies the browser to `/node/<host>/<port>/`.

```
browser ── lab IdP ──> OOD portal (per-user NGINX)
                          │  batch-connect job (as $USER)
                          ▼
        [linux_host | slurm | condor adapter]
                          │
                          ▼
   studio-launch   (native processes, $HOME state,
                    host Pegasus + host Condor client + host Apptainer)
                          ▲
   OOD proxy  /node/<host>/<port>/ ─┘
```

Adapter is a site choice, not a design fork:

| Site has | Adapter | Notes |
|---|---|---|
| Just the one server | `linux_host` | Session on the web/login host itself. |
| Slurm | `slurm` | Session on an interactive partition; Slurm enforces walltime/memory from the form. Compute nodes need the dist path mounted and outbound egress for AI-CLI installs (or pre-baked tools). |
| HTCondor | `condor` | Session is itself a condor job. |

Why native is the right default for labs (and what it buys over the container):

- **Host Pegasus is the real Pegasus** — versions, patches, and site
  documentation match what users see elsewhere on the system. No bundled-vs-
  host version skew for the Python API used by `workflow_generator.py`.
  (Requirement: the dist pins a tested Pegasus version *range*; `studio-launch`
  warns loudly when the host version is outside it.)
- **Host Condor config is inherited automatically** — if the server is a
  submit host, `condor_submit`/DAGMan just work with FS auth, no binds, no
  tokens. The whole "bind /etc/condor into the container" machinery vanishes.
- **The image builder finally works everywhere** — it shells out to the host
  `apptainer`, no container nesting, no privileged anything.
- No `.sif`/image distribution channel to operate.

What the container model keeps being for: Kubernetes/cloud (studio-system),
and any lab that prefers a hermetic runtime over managing host dependencies —
the OOD app supports launching either (a `STUDIO_RUNTIME=native|apptainer`
site setting; the apptainer variant is the same launcher inside the image).

## 3. Per-user state: already solved, identical in both models

Everything Studio persists is already under `$HOME` by design:
`~/.studio/studio.db` (settings, providers, encrypted keys), `~/work`
(workspaces, runs), `~/.local/share/pegasus-ai/marketplace` (skills checkout,
cloned on first launch from the dist/image seed), tool configs
(`~/.config/opencode`, `~/.codex`, `~/.config/crush`, ...). On an OOD server
homes are real and persistent: sessions are stateless and restartable, no
controller, no provisioning step. First launch *is* the provisioning.

Caveat: **one Studio session per user** — `studio.db` is SQLite and the stack
assumes it owns its ports. `studio-launch` takes a lockfile under `~/.studio/`
and refuses a second concurrent start with a clear message; the OOD form says
so too.

## 4. Pegasus and HTCondor integration matrix

Native mode simplifies this dramatically because the session inherits the
host's condor configuration.

### Case A — the server IS the submit host (local schedd)

Nothing to configure. The session sees `/etc/condor` like any login shell;
FS auth applies (same UID, same host); DAGMan runs under the host schedd and
reads submit dirs in `$HOME/work` directly. This is the reference
configuration and the first one to validate.

### Case B — remote access point

Site config (one file, see §7) sets condor's native env overrides for the
session:

```
_CONDOR_CONDOR_HOST=<cm>  _CONDOR_SCHEDD_HOST=<ap>
_CONDOR_SEC_TOKEN_DIRECTORY=$HOME/.condor/tokens.d
```

(The container path already uses exactly these three knobs —
`local-test-instance.sh` wires them today — so this is shared, tested
behavior. `_CONDOR_CREDD_HOST` is not currently wired anywhere; add it via
site.env only if a site actually runs a credd.)

The deciding question: **does the AP share a filesystem with this server**
(NFS homes, common in labs)?

- **Shared FS**: remote-schedd submission works as-is; DAGMan on the AP sees
  the same submit dirs. This mirrors the K8s deployment (pods mount
  `ap:/home/<user>`), a proven path.
- **No shared FS**: Pegasus breaks on remote schedd (DAGMan can't read the
  submit dir). The answer is a **personal condor in the session**
  (`STUDIO_MINICONDOR=1`: launcher starts master+schedd under supervisord,
  using host binaries in native mode) that flocks/routes into the remote
  pool, or a BOSCO-style ssh submit site. Phase 3; don't block release one.

Token provisioning is the one thing the K8s controller did that OOD lacks:
the pool admin issues per-user IDTOKENs once
(`condor_token_create -identity <user>@<domain>` → `~/.condor/tokens.d/`,
0600). Ship a one-command admin helper and a runbook page.

### Case C — no condor at all (Slurm-only site)

Personal condor in the session + Pegasus Glite/BLAHP to the host's Slurm
commands. Real but fiddly; Phase 3+, documented as experimental. Near-term
answer for Slurm-only labs: Case B against a collaborating AP, or Studio as
authoring/monitoring UI with `pegasus-plan` only.

### Telemetry

Two phone-home defaults to handle, not one. Both are launcher-written,
per-user, opt-in (implemented in #219; the image ENVs both to keep hosted
behavior, the native dist sets neither):

- **AMQP workflow events**: previously baked at
  `/etc/pegasus/pegasus.properties` (reporting to `msgs.pegasus.isi.edu`),
  invisible and un-turn-off-able for labs. Now written into the user's
  `~/.pegasus/pegasus.properties` only when `STUDIO_TELEMETRY=on`
  (endpoint overridable via `STUDIO_TELEMETRY_AMQP_URL`), append-if-absent
  so user edits win. Stated plainly in the admin docs.
- **Pegasus agent endpoint**: `pegasus.agent.url` is written into
  `~/.pegasus/pegasus.conf` only when `STUDIO_PEGASUS_AGENT_URL` is set —
  unset writes nothing, so a lab's users are never defaulted to an external
  endpoint nobody chose.

## 5. Security design

OOD authenticates the browser and runs the session as the user — necessary
but not sufficient, because **Studio has no auth of its own** and on a
multi-user host a localhost port is not private: any local user can
`curl http://localhost:<port>/api/...` and act as the session owner (read
files, run terminals). The K8s deployment never had this exposure (pod
network namespace); native OOD absolutely does. Hard requirement before any
multi-user rollout:

**Per-session token gate (launcher-level, off by default).**
- `before.sh.erb` generates a random secret (OOD's standard `$password`
  batch-connect pattern) and exports `STUDIO_AUTH_TOKEN`.
- The rendered proxy config gains a gate: requests must carry a
  `studio_session` cookie matching the token; `GET /auth?token=...` (the OOD
  Connect button URL from `view.html.erb`) sets the cookie and redirects to
  `/`; everything else without it gets 403. WS/SSE inherit the cookie.
- Unset token (K8s path) ⇒ gate compiles away; zero change to production.

**The gate must extend past the front port.** The cookie gate protects only
the proxy listener; on a shared host the backends are still reachable
directly — and today they are worse than reachable: supervisord starts
uvicorn with `--host 0.0.0.0` and Next with `HOSTNAME=0.0.0.0` (harmless
inside a pod network namespace, an open door natively), and JupyterLab runs
with `--ServerApp.token=""`, so any local user at `127.0.0.1:8889` gets
terminals as the session owner. In native mode the launcher therefore also:

- binds every internal service to `127.0.0.1` (rendered, so the container
  keeps its current binds);
- gives Jupyter a real launcher-generated token, injected by the proxy as an
  `Authorization: token …` header so the browser UX is unchanged;
- has the proxy stamp an internal secret header that studio-api requires
  whenever `STUDIO_AUTH_TOKEN` is set, so hitting uvicorn directly earns 403.

Also:
- **LLM keys at rest**: launcher auto-generates a per-user secret file
  (`~/.studio/secret.key`, 0600) when unset and points
  `STUDIO_SECRET_KEY_FILE` at it — `studio-api/secretbox.py` already supports
  the file variant — activating the existing S7 encryption without asking
  labs to manage secrets.
- **No default LLM gateway**: the K8s-internal `gateway.llm:8080` default is
  meaningless in a lab. Site config sets `STUDIO_LLM_GATEWAY_URL` if they run
  one; otherwise first-run UI guides users to add a provider — the recent
  provider work (PRs #206/#208, custom endpoints + live model lists in every
  tool) makes bring-your-own-endpoint the well-supported path.

## 6. Native packaging: the release dist

New CI artifact alongside the image, built from the same tree:

```
studio-dist-<version>.tar.gz
├── bin/studio-launch                  # the contract entry point (§1)
├── studio-api/                        # app + requirements lock
├── studio-web/                        # Next.js standalone build (.next/standalone)
├── runtime/
│   ├── node/                          # pinned node binary (or use host node ≥20; site choice)
│   └── proxy/                         # see below
├── seed/marketplace/                  # knowledge checkout seed (clone source)
└── install.sh                         # creates the venv against host python3.11+,
                                       # verifies host deps, prints a report
```

Install: admin unpacks to `/opt/pegasus-ai/studio/<version>/` (or exposes it
as an Lmod module) and runs `install.sh` once. Per-user runtime state stays in
`$HOME`; the dist is read-only and versioned side-by-side, so rollback is a
symlink flip.

**Host dependency matrix** (checked by `install.sh` and at session start):

| Dependency | Native source | Notes |
|---|---|---|
| Python ≥3.11 | host | venv created at install time (not relocatable — fine, path is fixed) |
| Node ≥20 | host or bundled | Next standalone needs a node binary; bundling one removes the flakiest host dep |
| Pegasus | **host** | tested version range pinned in dist; loud warning outside it |
| HTCondor client | host | Case A/B; absent ⇒ Case C or authoring-only |
| Apptainer | host | image builder + containerized workflow jobs |
| JupyterLab | dist venv | same pinned version as the image |
| AI CLIs | per-user npm (`~/.npm-global`) | unchanged — same mechanism as today |
| Front proxy | see below | |

**The proxy question** (the only awkward native dependency): the stack fronts
three upstreams (web/api/jupyter) behind one port, today via nginx inside the
container. Options, in preference order:

1. **Bundle Caddy** in the dist (single static binary, ~40 MB, supports
   sub-path, WS, SSE, and the cookie gate natively). One proxy config template
   in the launcher, rendered for Caddy. Recommended: removes any host
   dependency and any doubt about per-user nginx quirks.
2. Reuse host nginx binary with a user-owned conf and `/tmp` temp paths (this
   is exactly what the container does as non-root today, so it is proven) —
   but nginx on Slurm compute nodes is not a given.
3. Longer term, converge the container on the same bundled proxy so both
   models render literally one template. Not required for Phase 1; the
   launcher's template abstraction keeps the door open.

## 7. The OOD app (new repo: `pegasus-isi/studio-ood`)

Standard batch-connect layout:

```
studio-ood/
├── manifest.yml            # Pegasus AI Studio, category: Interactive Apps
├── form.yml.erb            # cluster, hours, memory/cores, studio version,
│                           # condor target (site list); advanced: extra env
├── submit.yml.erb          # per-adapter submit params
├── template/
│   ├── before.sh.erb       # free port; generate STUDIO_AUTH_TOKEN
│   ├── script.sh.erb       # single-session lock; source site.env;
│   │                       # export STUDIO_PORT/URL_PREFIX/AUTH_TOKEN + _CONDOR_*;
│   │                       # exec $STUDIO_DIST/bin/studio-launch   (native)
│   │                       # — or apptainer run $SIF (site runtime choice)
│   └── after.sh.erb        # wait-for-port
├── view.html.erb           # Connect → /node/<host>/<port>/auth?token=<password>
└── icon.svg
```

Site admin one-time setup:

1. Unpack `studio-dist` to a shared path, run `install.sh` (or, container
   runtime: `apptainer pull` the `.sif`).
2. Clone `studio-ood` into `/var/www/ood/apps/sys/pegasus-studio`.
3. Write `/etc/ood/config/apps/pegasus-studio/site.env`: `STUDIO_RUNTIME`,
   dist path or sif path, condor Case A/B settings, optional LLM gateway URL.
4. (Case B) issue per-user condor IDTOKENs.

Per-user setup: none. Log into OOD, click the app, Connect.

## 8. Required studio-webapp changes

Ordered; 1–4 serve **both** models, 5–6 are the native packaging itself.
All are backward-compatible with the K8s deployment.

1. **Extract `studio-launch`** from `entrypoint.sh` + supervisor/nginx confs:
   env-driven prepare/render/exec (§1). Container entrypoint delegates to it.
2. **Per-session ports — front AND internal.** The proxy conf hardcodes
   `listen 8888`, and — the part the pod network namespace let us ignore —
   every internal port is fixed too: uvicorn `:8080` and Next `:3000` in
   `supervisord.conf`, Jupyter `:8889` in `jupyter.sh` *and*
   `routers/jupyter.py` (`_JUPYTER_PORT`), plus the nginx upstream blocks.
   On a shared OOD node two users' sessions collide on the first internal
   bind, before the single-session-per-user lock (which is per-user, not
   per-host) can help. The launcher allocates free ports for all listeners
   per session and renders them into the proxy/supervisor configs and
   service env (`STUDIO_PORT`, `STUDIO_API_PORT`, `STUDIO_WEB_PORT`,
   `STUDIO_JUPYTER_PORT`; `STUDIO_OPENCODE_PORT` already exists). Defaults
   stay 8888/8080/3000/8889 so container behavior is byte-identical.
3. **Generic `STUDIO_URL_PREFIX`** — handled at the proxy, not in the app.
   Two facts make this cheaper than it looks: production nginx already
   *strips* the prefix before proxying to Next and sets `X-Forwarded-Prefix`,
   and `app/layout.tsx` already derives `<base href>` from that header
   (falling back to `x-script-name`, then `NEXT_PUBLIC_BASE_PATH`). The
   `/user/<name>` shape lives only in the proxy location regexes and in
   `studio-web/middleware.ts` — and the middleware *cannot* be generalized
   from env anyway (`config.matcher` is compiled at build time), which
   settles the approach: render the proxy locations from `STUDIO_URL_PREFIX`
   (strip + set header, exactly what the `/user` regexes do today, including
   the `sub_filter` asset rewrites) and leave the middleware untouched as
   the `/user/...` fallback for the K8s/dev paths. Jupyter `base_url`
   already has the needed knob (`STUDIO_JUPYTER_BASE_URL` in `jupyter.sh`,
   superseded by `JUPYTERHUB_SERVICE_PREFIX` when set).
4. **Session token gate** (§5) — rendered into the proxy conf; off when
   `STUDIO_AUTH_TOKEN` unset. Includes the backend hardening (loopback binds,
   Jupyter token, api secret header), per-user `STUDIO_SECRET_KEY_FILE`
   auto-generation, and the telemetry/agent-URL opt-ins (§4).
5. **Dist build in CI** (§6): tarball with lockfiles, standalone web build,
   bundled proxy (and optionally node), `install.sh` with dependency report.
6. **Host-tool resolution**: image builder and workflow tooling resolve
   `apptainer`/`pegasus-*`/`condor_*` from PATH rather than assuming image
   locations (confirmed for the image builder — `services/image_builder.py`
   PATH-resolves `apptainer`/`singularity` — and for opencode via
   `STUDIO_OPENCODE_BIN`; audit the remaining services + tests). Add a launcher preflight
   that reports what it found (pegasus version, condor schedd reachability,
   apptainer) into the dashboard's environment panel so "may or may not be a
   submit host" is *visible* to the user, not a mystery.
7. *(Phase 3)* **`STUDIO_MINICONDOR=1`** for no-shared-FS / Slurm-only cases.

## 9. Known risks / open questions

- **OOD proxy vs streams**: chat is SSE, terminals are WebSockets. OOD's node
  proxy supports WS; verify per-user NGINX read timeouts against long idle
  SSE early — the failure mode is silent stream resets at a suspiciously
  round number (the Envoy 15 s lesson).
- **Host-dependency drift** is the native model's tax: the dist pins what it
  can (venv, node, proxy, jupyter) and version-checks what it can't (pegasus,
  condor, python). The preflight report keeps failures diagnosable.
- **Two-model test burden**: CI must boot both packagings (container smoke
  test exists implicitly; add a dist smoke: unpack → `studio-launch` →
  health-check on a random port+prefix).
- **Concurrent sessions**: lockfile-enforced single session per user.
- **Egress from compute nodes** for per-user AI-CLI installs; LinuxHost
  adapter avoids it, or sites pre-bake the tools into the dist.
- **Resource footprint**: ~1.5–2 GB RSS per session; form default 4 GB.

## 10. Phasing

- **Phase 1 (studio-webapp)**: §8.1–8.4 — launcher extraction, port/prefix
  generalization, token gate — validated in the container (K8s regression) AND
  via a hand-run native checkout on one multi-user server. Exit criterion:
  native `studio-launch` on a plain server gives a working, token-gated
  Studio on an arbitrary port/prefix, submitting via the host schedd (Case A).
- **Phase 2**: dist build + `install.sh` (§8.5–8.6); `studio-ood` repo with
  `linux_host` adapter; deploy on one friendly lab's OOD; validate SSE/WS
  through the OOD proxy; Case B (remote AP, shared FS) with real tokens.
- **Phase 3**: slurm/condor adapters, `STUDIO_MINICONDOR`, admin runbook
  (tokens, site.env reference), Case C experiment.
- **Phase 4**: hardening (token-gate audit, telemetry default-off
  verification), versioned dist release channel alongside the `:main` image,
  optional proxy convergence (one template, both models).
