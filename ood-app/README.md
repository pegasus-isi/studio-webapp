# PegasusAI Studio — Open OnDemand app

An Apptainer/Singularity port of the same product as [`../Dockerfile`](../Dockerfile)
(nginx + supervisord fronting studio-api, studio-web, and JupyterLab, plus
HTCondor, Pegasus WMS, and Apptainer for running workflow job containers),
packaged for [Open OnDemand](https://openondemand.org/) instead of
K8s/KubeSpawner. [`studio-el9.def`](studio-el9.def) builds the container image
only; the OOD batch-connect app that launches it is
[pegasus-isi/bc_pegasus_ai_studio](https://github.com/pegasus-isi/bc_pegasus_ai_studio).

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

- Image-side support for a Studio **home directory of its own** (#482), so a
  session never writes into the user's real home. See
  [Studio's own home directory](#studios-own-home-directory). That includes
  what runs *outside* the container: planning on the access point exports
  Studio's `HOME` itself, because HTCondor resets it to the passwd home and
  Pegasus's master database followed it there (#508), and the JVM is pointed
  at Studio's home (#489).
- **Validated end to end (2026-10-13)** on a FABRIC test cluster
  ([kthare10/fabric-slurm-test](https://github.com/kthare10/fabric-slurm-test):
  Slurm + HTCondor access point + Open OnDemand 4.0, three users): sessions on
  Slurm compute nodes with the token gate and a dotted `/node/<fqdn>/<port>`
  prefix (#483); the user's real home unchanged by a session; chat on a
  provider the user added (#498, #505); in-session image builds (#485);
  workflows submitted from Studio whose jobs ran as Slurm jobs through
  HTCondor's BLAHP on the access point, using an administrator's site catalog
  (#496). Fixes it took: #485, #487, #491, #495, #501, #508.
- **Site settings an administrator passes in** (catalog, LLM gateway,
  HTCondor access point); see [the table below](#site-settings-an-administrator-passes-in).

**Not done / open:**
- **Slurm from inside the session** (the original design for this image).
  Slurm is already reachable the way the validation above does it: an
  administrator's site catalog sends `compute` to Slurm through BLAHP on the
  *access point* (`grid_resource = batch slurm`, as pegasushub's `unity.yml`
  does), which needs nothing in the image. What is not built is the
  alternative: HTCondor submitting onward into Slurm from a local schedd in
  the session, which is *why* the OS-matching in the section above matters
  enough to justify a second image — bind-mounting the host's
  `sbatch`/`squeue`/`scontrol` and munge socket/key only works if they run
  against compatible libraries. **This isn't built yet**:
  [`../docker/htcondor/90-studio.conf`](../docker/htcondor/90-studio.conf),
  installed unmodified into this image too, is still the plain remote-AP
  client config (`CONDOR_HOST = ap.pegasus-ai.org`) that the K8s deployment
  uses. Until a local-pool config and the bind-mount list exist, an OOD
  session submits to the same remote AP as everywhere else, unless the site's
  `_CONDOR_SCHEDD_HOST`/`_CONDOR_COLLECTOR_HOST` point it at the cluster's own
  (fully qualified names) — the Rocky9 base is prerequisite, not sufficient,
  for local submission.
- **Full builds need the Pegasus 6.0 development channel**, which `%post`
  installs from. On 2026-10-13 it listed 0 packages (RHEL, Debian, Ubuntu)
  and every from-scratch build failed at `dnf install pegasus`; check the
  repository before suspecting the definition.
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

## Studio's own home directory

Everything Studio writes is `$HOME`-relative: its SQLite database
(`~/.studio`), the marketplace checkout (`~/.local/share/pegasus-ai`),
`~/.pegasusrc` and `~/.pegasus/pegasus.conf`, the generated `CLAUDE.md`,
`AGENTS.md` and `.mcp.json`, `~/workflows` and `~/data`, and the configs the
knowledge adapters write for the AI tools (`~/.codex`, `~/.config/opencode`,
`~/.claude*`). Under KubeSpawner that home is a volume that exists only for
Studio. On a cluster it is the user's real home, and Apptainer binds it by
default, so a plain `apptainer run` would overwrite the user's own Codex,
Claude, OpenCode and Pegasus setup on first launch.

So the batch-connect app ([pegasus-isi/bc_pegasus_ai_studio](https://github.com/pegasus-isi/bc_pegasus_ai_studio)) never lets Studio see the real home as
`$HOME`:

```bash
apptainer run --home "$STUDIO_HOME" --bind "$HOME" studio-el9.sif
```

- `--home` moves `$HOME` (and, unless the site binds the host's
  `/etc/passwd`, the passwd entry too) to the Studio directory. Everything
  Studio and its AI tools write lands there. No application code changes:
  this is the same model as K8s, where Studio owns its home.
- The real home stays bind-mounted at its own path, so terminals can reach
  existing data, and the app links credentials (`.condor/tokens.d`,
  `.gitconfig`) in from it.
- How the directory is chosen and checked (a form field, never the real home
  or anything containing it) is the app's business, not the image's.

The directory must be on storage the HTCondor submit host also mounts at the
same path. DAGMan and remote planning (`studio-api/services/remote_exec.py`)
read the workflow's submit directory and `HOME` from there. The default under
an NFS home satisfies that; node-local scratch does not.

`docker/entrypoint.sh` backs this up: when `STUDIO_HOME` is set and `$HOME`
differs (a launch that forgot `--home`), it exports `HOME=$STUDIO_HOME` and
warns that passwd-based lookups still see the real home.

## Installing the batch-connect app

See [pegasus-isi/bc_pegasus_ai_studio](https://github.com/pegasus-isi/bc_pegasus_ai_studio): clone it into `/var/www/ood/apps/sys/`, put the `.sif` where every
session host can read it, and set the image path in its `form.yml.erb`.
Enable `node_uri` in `ood_portal.yml`, since the session is proxied at
`/node/<host>/<port>`.

## Site settings an administrator passes in

Studio reads these from its environment; the batch-connect app is what sets
them for every session. None needs any privilege the user lacks.

| Env | Purpose |
|---|---|
| `STUDIO_SITE_CATALOG_FILE` | Path to the site's own Pegasus site catalog (#496). It replaces Studio's default `pegasus-studio.yml`, so site `compute` means this cluster, e.g. a Slurm partition through HTCondor BLAHP like the published `unity.yml`. Copied into each user's Studio directory every start, where the submit host can read it. |
| `STUDIO_SITE_CATALOG_LABEL` | What the Resources page calls it (default "This cluster"). |
| `STUDIO_SITE_CATALOG` | Alternatively, the name of a catalog published in `pegasushub/pegasus-site-catalogs`. |
| `STUDIO_LLM_GATEWAY_URL`, `STUDIO_LLM_DEFAULT_MODEL` | An OpenAI-compatible gateway the site runs for the AI assistant. Without one, users add their own provider and key in Settings. |
| `_CONDOR_SCHEDD_HOST`, `_CONDOR_COLLECTOR_HOST` | The HTCondor access point, by fully qualified name, when the session host is not it. |

## Running the image by hand

Simulate what the batch-connect app does, with its own Studio directory:

```bash
mkdir -p ~/pegasus-studio
STUDIO_HOME=~/pegasus-studio \
STUDIO_PORT=20153 STUDIO_ALLOC_PORTS=1 \
STUDIO_URL_PREFIX=/node/$(hostname)/20153 \
STUDIO_AUTH_TOKEN=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))') \
apptainer run --home ~/pegasus-studio --bind "$HOME" ood-app/studio-el9.sif
```

Then reach it through something that proxies under that prefix (a local
nginx/OOD-shaped reverse proxy, or a cluster's real OOD portal). Hitting the
port directly with a browser won't exercise the prefix rewriting.

| Env | Purpose |
|---|---|
| `STUDIO_HOME` | Studio's home; see above. Pair it with `--home`. |
| `STUDIO_URL_PREFIX` | The path Open OnDemand's proxy assigns this session (e.g. `/node/compute-1/20153`). Without it, every asset/API request under the prefix 404s. See `../docker/nginx/prefix-proxy.conf.tmpl`. |
| `STUDIO_AUTH_TOKEN` | Turns on the session token gate (#218). The proxy port has no isolation on a shared host otherwise. |
| `STUDIO_PORT`, `STUDIO_ALLOC_PORTS` | Front port, and pick free internal ports; Apptainer shares the host network namespace. |

## See also

- `studio-el9.def`'s own header comment and `%help` — build/run commands and
  a one-line summary, kept in sync with this file.
- [`../SPEC.md`](../SPEC.md#open-ondemand--apptainer-packaging) — how this
  packaging fits the overall design.
- [`../README.md`](../README.md) — the Docker/K8s deployment this shares most
  of its code with.
