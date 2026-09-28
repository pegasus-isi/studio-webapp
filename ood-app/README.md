# PegasusAI Studio — Open OnDemand app

An Apptainer/Singularity port of the same product as [`../Dockerfile`](../Dockerfile)
(nginx + supervisord fronting studio-api, studio-web, and JupyterLab, plus
HTCondor, Pegasus WMS, and Apptainer for running workflow job containers),
packaged for [Open OnDemand](https://openondemand.org/) instead of
K8s/KubeSpawner. [`studio-el9.def`](studio-el9.def) builds the container image
only; the OOD batch-connect app that launches it (`form.yml`,
`submit.yml.erb`, `before.sh.erb`) is a separate, site-specific piece — see
[Status](#status).

## Why a separate `.def` instead of running the Docker image under Apptainer

Apptainer can run the existing `ghcr.io/pegasus-isi/studio-webapp` Docker image
directly (`apptainer run docker://...`), so a second build only earns its keep
because of one requirement: **the container's OS has to match the target
Slurm cluster's**, so the cluster's own Slurm client tools and munge
authentication can be bind-mounted in and actually run — a bind-mounted
`sbatch` built against a different glibc/library set is a common source of
silent breakage. HPC clusters skew RHEL-family, so `studio-el9.def` builds on
`rockylinux:9` rather than the Docker image's `debian:trixie-slim` (chosen
there to match the K8s deployment's HTCondor access point, #334 — a different
constraint that doesn't apply here). Everything else — the FastAPI/Next.js
apps, the nginx templates, the session token gate, `entrypoint.sh` — is
reused as-is from `../docker/` and `../studio-api/`, `../studio-web/`; see
`%post` in `studio-el9.def` for exactly which RHEL/AppStream packages stand in
for the Dockerfile's apt list.

## Status

**Done:**
- The container image itself: same product as the Docker image, EL9-based,
  with dynamic per-session port allocation and the session token gate (#218)
  available (both are also needed by, but not exclusive to, this
  deployment — see `../docker/entrypoint.sh`).
- Reverse-proxy routing for an arbitrary Open OnDemand URL prefix (e.g.
  `/node/compute-1/20153`), not just KubeSpawner's `/user/<name>` shape —
  `../docker/nginx/prefix-proxy.conf.tmpl`.

**Not done / open:**
- **The OOD batch-connect app** (`form.yml`, `submit.yml.erb`,
  `before.sh.erb` — the Slurm job script that starts `apptainer instance
  start` and generates `STUDIO_AUTH_TOKEN`/reads `STUDIO_URL_PREFIX`) is not
  in this repo. It's expected to live in a site-specific OOD apps catalog
  repo that references this image, the same way a site's OOD deployment
  usually isn't checked into the application's own repo. Nothing here should
  be read as reserving a location for it.
- **Local Slurm submission.** The intent is for HTCondor itself to submit
  onward into the cluster's Slurm queue (a local schedd using HTCondor's
  Slurm/BLAHP grid support), which is *why* the OS-matching in the section
  above matters enough to justify a second image — bind-mounting the host's
  `sbatch`/`squeue`/`scontrol` and munge socket/key only works if they run
  against compatible libraries. **This isn't built yet**:
  [`../docker/htcondor/90-studio.conf`](../docker/htcondor/90-studio.conf),
  installed unmodified into this image too, is still the plain remote-AP
  client config (`CONDOR_HOST = ap.pegasus-ai.org`) that the K8s deployment
  uses. Until a local-pool config and the bind-mount list exist, an OOD
  session submits to the same remote AP as everywhere else — the Rocky9 base
  is prerequisite, not sufficient, for local submission.
- **Known flaky build step**: `jlpm install` (building the PegasusAI Chat
  JupyterLab extension) has segfaulted intermittently in `%post` on at least
  one build host, cause unresolved. A 3-attempt retry loop and
  `YARN_NETWORK_CONCURRENCY=4` are already in `studio-el9.def` as a
  mitigation (needed regardless, since `set -euo pipefail` means one segfault
  would otherwise kill the whole build) — they reduce but have not
  reliably eliminated it on every host tried. If a build fails here, retry
  before assuming something else broke.

## Build

From the repo root — `%files` sources in `studio-el9.def` resolve relative to
the current working directory, not the `.def` file's location:

```bash
apptainer build --build-arg KNOWLEDGE_REF=main \
    ood-app/studio-el9.sif ood-app/studio-el9.def
```

| `--build-arg` | Default | Purpose |
|---|---|---|
| `KNOWLEDGE_REF` | `main` | Branch/tag/SHA of the knowledge marketplace checkout baked in as the seed (see `../CLAUDE.md`'s Knowledge base section) |
| `KNOWLEDGE_REPO` | `https://github.com/pegasus-isi/claude-plugin-marketplace.git` | Override for a fork/mirror |
| `ENABLE_PEGASUS_BOT_CLIENT` | `1` | Set `0` to leave out the vendored PegasusAI Bot cell-toolbar client |

Build on `x86_64` — `%post` refuses any other arch, same reasoning as the
Dockerfile's `linux/amd64` requirement (HTCondor/Pegasus/Apptainer EL9
packages are pulled x86_64-specifically).

## Run

Foreground, mirrors `docker run`:

```bash
apptainer run --bind "$HOME" ood-app/studio-el9.sif
```

As a background instance (what an OOD batch-connect job would do):

```bash
apptainer instance start --bind "$HOME" ood-app/studio-el9.sif studio
apptainer instance stop studio
```

Apptainer auto-binds the real host `$HOME` by default, so `entrypoint.sh`'s
`$HOME`-relative state (SQLite DB, workspace, marketplace checkout) works
unmodified; `--bind "$HOME"` above is belt-and-suspenders, matching the
`.def` file's own documented run commands.

nginx listens on `:8888` inside the container, same as the Docker image —
whatever fronts it (an OOD portal's own allocator, a `-p`-style mapping) picks
the externally-visible port and is expected to set two environment variables
before starting the instance:

| Env | Purpose |
|---|---|
| `STUDIO_URL_PREFIX` | The path Open OnDemand's own proxy assigns this session (e.g. `/node/compute-1/20153`) — without it, the app answers at `/` and every asset/API request under the assigned prefix 404s. See `../docker/nginx/prefix-proxy.conf.tmpl`. |
| `STUDIO_AUTH_TOKEN` | Turns on the session token gate (#218): the proxy port itself has no isolation on a shared host otherwise. Generate one the same way any other batch-connect password is generated (e.g. `before.sh.erb`); the container does not need it pre-shared anywhere else. |

Both are read by `entrypoint.sh` at container start, not build time — see
the top-level [`README.md`](../README.md#configuration) and
`../CLAUDE.md`'s "session token gate" and "Prefix handling" notes for the
full design rationale (shared by both deployments).

## Testing without a batch-connect app yet

Simulate what the (not-yet-written) OOD job script will do:

```bash
STUDIO_URL_PREFIX=/node/compute-1/20153 \
STUDIO_AUTH_TOKEN=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))') \
apptainer instance start --bind "$HOME" ood-app/studio-el9.sif studio
```

Then reach it through something that actually proxies under that prefix
(a local nginx/OOD-shaped reverse proxy, or a cluster's real OOD portal) —
hitting the container's port directly with a browser won't exercise the
prefix rewriting, since there'd be no proxy adding the prefix in the first
place.

## See also

- `studio-el9.def`'s own header comment and `%help` — build/run commands and
  a one-line summary, kept in sync with this file.
- [`../SPEC.md`](../SPEC.md#open-ondemand--apptainer-packaging) — how this
  packaging fits the overall design.
- [`../README.md`](../README.md) — the Docker/K8s deployment this shares most
  of its code with.
