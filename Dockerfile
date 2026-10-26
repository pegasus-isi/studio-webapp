# PegasusAI Studio — Multi-stage Docker build
#
# Stage 1: Build Next.js frontend (standalone output ~15MB)
# Stage 2: Runtime — Pegasus/HTCondor + studio-api + frontend + supervisord

# Debian suite for the runtime stage. Global (declared before the first FROM)
# because that is the only scope a FROM line can interpolate; the runtime stage
# re-declares it to use it in RUN. See the stage 2 header for why Debian.
ARG DEBIAN_SUITE=trixie

# ============================================================
# Stage 1: Build frontend
# ============================================================
FROM node:20-alpine AS frontend-build

WORKDIR /build
# Next.js uploads build telemetry at the end of `next build` and waits for it;
# where egress is restricted or flaky the build then hangs after printing its
# route table (#500).
ENV NEXT_TELEMETRY_DISABLED=1
COPY studio-web/package.json studio-web/package-lock.json* ./
RUN npm ci --ignore-scripts 2>/dev/null || npm install

COPY studio-web/ ./
RUN npm run build

# ============================================================
# Stage 2: Runtime
# ============================================================
# Debian trixie, not Ubuntu, because of what the HTCondor access point runs.
#
# Pegasus assumes the planner and the schedd live on one machine and bakes its
# own absolute paths into the submit files it writes. Those paths run through
# Pegasus's Python venv, so they carry a Python minor version:
#
#     /usr/lib/pegasus/venv/lib/python3.13/site-packages/Pegasus/data/sh/…
#
# The AP (studio-system, salt-managed) is Debian trixie / python3.13. On
# ubuntu:24.04 this pod was python3.12, and on 2026-09-02 every workflow died
# on its first node with "Failed to execute … pegasus-lite-local.sh (errno=2)" —
# same Pegasus version, same apt channel, one Python minor apart (#334). Same
# distro as the AP means the two agree by construction rather than by
# workaround. `services/submit_host.py` keeps its preflight, since the AP can
# drift again.
#
# Everything version-specific about the suite is below: the HTCondor and
# Pegasus apt channels, and the Apptainer artifact. Keep them moving together —
# and note the Apptainer asset only exists for trixie, so DEBIAN_SUITE is not
# freely swappable, it is a single place to change when the AP moves.
FROM debian:${DEBIAN_SUITE}-slim AS runtime
ARG DEBIAN_SUITE

ENV DEBIAN_FRONTEND=noninteractive

# ---- apt resilience (matters under QEMU emulation for amd64 builds) ----
# Emulated builds intermittently fail apt downloads/post-install with dpkg
# error code 1 due to mirror hiccups or timeouts. Apply retries + generous
# timeouts globally so every apt invocation below inherits them.
RUN printf '%s\n' \
    'Acquire::Retries "5";' \
    'Acquire::http::Timeout "120";' \
    'Acquire::https::Timeout "120";' \
    'Acquire::ftp::Timeout "120";' \
    > /etc/apt/apt.conf.d/80-retries

# ---- User accounts ----
# The container runs as whatever UID KubeSpawner injects via the pod security
# context, and libnss-sss (see docker/nsswitch.conf, `passwd: files sss`) is what
# resolves that UID to an account in production.
#
# A passwd entry for uid 1000 is added because deployments without an sssd
# socket — every local run, which is uid 1000 by way of local-test-instance.sh —
# otherwise resolve uid 1000 to nothing, and anything that looks up the calling
# user fails. `apptainer build` is the one that bites:
#     FATAL: Couldn't determine user account information: user: unknown userid 1000
# It exits before a single %post line runs, so no workflow container image can
# ever be built. When a different UID is injected the entry is simply unused, so
# the arbitrary-UID design is unchanged.
#
# The home path is nominal: the real HOME is whatever the spawner mounts
# (`ap:/home/<user>` in production, a bind mount locally) and is always exported
# in the environment. Debian, unlike Ubuntu, ships no stock uid-1000 account, so
# there is nothing to delete first.
#
# It is created empty anyway (-M plus an explicit mkdir, rather than -m, so no
# skel files land in it and it stays obviously nominal). "HOME is always
# exported" is true of everything that reads the environment — and `apptainer
# build` does not. It reads the passwd entry, mounts that path to /root, and
# dies before the definition is touched if it is missing:
#
#   FATAL: failed to mount /home/studio to /root: no such file or directory
#
# There is no way around it from the caller: `apptainer build` accepts neither
# --home nor --no-home, and ignores APPTAINER_HOME and $HOME. So every workflow
# container build failed on this image. In hosted mode sssd resolves the injected
# UID to a real NFS home and this entry is never consulted, which is why the
# directory can be empty and unused there at no cost.
RUN groupadd -g 1000 studio && \
    useradd -u 1000 -g 1000 -M -d /home/studio -s /bin/bash studio && \
    mkdir -p /home/studio && chown 1000:1000 /home/studio

# ---- System packages (no python3-pip — we bootstrap pip cleanly below) ----
# software-properties-common is deliberately absent: trixie does not ship it,
# and it was already dead weight here — the pip bootstrap below wipes
# /usr/lib/python3/dist-packages, which takes apt_pkg with it and leaves
# add-apt-repository broken regardless (see the Apptainer step).
# Retry-with-recovery loop: under amd64 emulation a dpkg post-install can be
# killed mid-configure (exit 1) at a random package. Re-run dpkg --configure -a
# + apt-get install -f to repair the half-configured state, then retry.
RUN ok=0; for i in 1 2 3 4 5; do \
      apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        libnss-sss \
        nginx \
        xz-utils \
        curl \
        wget \
        git \
        vim \
        poppler-utils \
        build-essential \
        python3 \
        python3-venv \
        python3-dev \
        python-is-python3 \
      && { ok=1; break; }; \
      echo "[apt] attempt $i failed — repairing and retrying"; \
      dpkg --configure -a || true; apt-get install -f -y || true; sleep 5; \
    done; [ "$ok" = 1 ] || { echo "[apt] failed after 5 attempts"; exit 1; } \
    && rm -rf /var/lib/apt/lists/* \
    # Unversioned python3 on purpose: the suite decides the version (trixie is
    # 3.13) and Pegasus's venv is built against whatever /usr/bin/python3 is, so
    # naming a version here would be a second place to keep in sync. `python`
    # comes from python-is-python3 rather than update-alternatives, which on
    # Debian would take over the dpkg-managed /usr/bin/python3 symlink.
    && python3 --version \
    # ca-certificates is explicit because -slim does not carry it and every
    # curl below (the pip wheel, the repo keys, the Apptainer deb) is https.
    && test -f /etc/ssl/certs/ca-certificates.crt

# nsswitch - for user/group lookups
COPY docker/nsswitch.conf /etc/nsswitch.conf

# ---- pip bootstrap ----
# Wipe all apt-installed Python packages (they lack RECORD files and block
# pip from upgrading transitive deps like PyJWT, cryptography, etc.).
# Then bootstrap a clean pip from a pinned pip wheel.
# The glob covers whatever minor version the suite ships, so a suite bump does
# not silently leave the marker in place and turn every pip install below into
# an "externally-managed-environment" error.
#
# A released wheel, by version and PyPI's sha256, not bootstrap.pypa.io's
# get-pip.py: get-pip.py is one URL that always serves the newest pip, so its
# pinned checksum broke every build the day a pip release came out (pip 26.2.2,
# 2026-10-20). A released file never changes, so this stays checksummed (INF-4)
# without breaking on upstream releases. pip runs straight from its own wheel to
# install itself, which pip supports; bump both ARGs together, from
# https://pypi.org/pypi/pip/<version>/json (urls[].digests.sha256).
ARG PIP_VERSION=26.2.2
ARG PIP_WHEEL_URL=https://files.pythonhosted.org/packages/3f/06/3e4420302363fbb71be3c7f35ce042f54bfca330e3e66b3a2e3c4cca579f/pip-26.2.2-py3-none-any.whl
ARG PIP_WHEEL_SHA256=fd7f4dfa8190e014013b9f08f3e973f4e4c26ffd179a204895533a7254813a70
RUN rm -f /usr/lib/python3.*/EXTERNALLY-MANAGED && \
    rm -rf /usr/lib/python3/dist-packages/* && \
    whl=/tmp/pip-${PIP_VERSION}-py3-none-any.whl && \
    curl -fsSL -o "$whl" "${PIP_WHEEL_URL}" && \
    echo "${PIP_WHEEL_SHA256}  $whl" | sha256sum -c - && \
    python3 "$whl/pip" install --no-cache-dir --no-index "$whl" && \
    rm -f "$whl" && \
    pip --version && \
    pip install --no-cache-dir setuptools wheel

# ---- JupyterLab + extensions ----
# NOTE: jupyter-ai is intentionally NOT installed for now. jupyter-ai 3.x ships
# a collaborative-document stack (jupyter-server-documents,
# jupyterlab-notebook-awareness, jupyterlab-chat, jupyter-collaboration) whose
# frontend extensions request RTC "rooms" (/api/collaboration/room/...). With
# RTC disabled server-side, those 403 in a loop and notebooks never open
# ("Unable to reconnect to the server"). The studio is single-user and has its
# own chat, so without jupyter-ai JupyterLab uses the classic document model
# and notebooks open reliably.
RUN python3 -m pip install --no-cache-dir \
    jupyterlab>=4.2 \
    jupyterlab-git \
    notebook \
    ipykernel \
    numpy \
    pandas \
    matplotlib \
    scipy \
    pypdf

# Defensive: ensure no RTC/collaboration/awareness/document packages slipped in
# transitively — they drive the 403 collaboration-room loop behind the proxy.
RUN python3 -m pip uninstall -y \
    jupyter-collaboration jupyter-collaboration-ui jupyter-docprovider \
    jupyter-server-ydoc jupyter-server-documents \
    jupyterlab-notebook-awareness jupyterlab-chat \
    || true

# ---- Jupyter server configuration ----
COPY docker/jupyter/jupyter_server_config.py /etc/jupyter/jupyter_server_config.py

# ---- JupyterLab theme: match Studio ----
# The overrides are light-theme variables (sky accent, slate text, Studio
# surfaces), so they are appended to the light theme's own stylesheet rather
# than loaded as global custom CSS — global CSS would also clobber the dark
# theme's variables the moment someone picked it. Nothing later rebuilds the
# lab app (labextension builds are prebuilt bundles), so the append sticks.
# `test -f` makes a move of the theme directory fail the build instead of
# silently shipping an unthemed JupyterLab.
COPY docker/jupyter/studio-overrides.css /opt/pegasus-ai/jupyter/studio-overrides.css
RUN THEME="$(jupyter lab path | awk -F': *' '/Application directory/{print $2}')/themes/@jupyterlab/theme-light-extension/index.css" \
    && test -f "$THEME" \
    && printf '\n\n/* ---- PegasusAI Studio overrides (docker/jupyter/studio-overrides.css) ---- */\n' >> "$THEME" \
    && cat /opt/pegasus-ai/jupyter/studio-overrides.css >> "$THEME"

# ---- supervisord (process supervisor) ----
RUN pip install --no-cache-dir supervisor

# ---- HTCondor + Pegasus WMS (amd64 DEB packages) ----
# Native DEB packages provide full daemon support (condor_master) and the
# Pegasus planner (pegasus-plan). The image is built for linux/amd64 (see the
# Makefile); HTCondor publishes no arm64 DEB, so other arches are unsupported
# here.
#
# NOTE: we deliberately do NOT install conda/Miniforge. It was previously added
# only for an arm64 HTCondor fallback, but it shadowed the system python3 with
# its own (python3.13) and split deps across two interpreters — e.g. Pegasus's
# preflight ran under conda's Python, which lacked GitPython. One Python only.
RUN ARCH=$(dpkg --print-architecture) && \
    if [ "$ARCH" != "amd64" ]; then \
        echo "[install] ERROR: unsupported arch '$ARCH' — build with --platform linux/amd64" >&2 && \
        exit 1 ; \
    fi && \
    install -d /etc/apt/keyrings && \
    curl -fsSL https://htcss-downloads.chtc.wisc.edu/repo/keys/HTCondor-25.x-Key \
        -o /etc/apt/keyrings/htcondor.asc && \
    curl -fsSL "https://htcss-downloads.chtc.wisc.edu/repo/debian/htcondor-25.x-${DEBIAN_SUITE}.list" \
        -o /etc/apt/sources.list.d/htcondor.list && \
    apt-get update && \
    apt-get install -y --no-install-recommends condor && \
    rm -rf /var/lib/apt/lists/*
COPY docker/htcondor/90-studio.conf /etc/condor/config.d/90-studio.conf
COPY docker/pegasus/pegasus.properties /etc/pegasus/pegasus.properties

# ---- Pegasus ----
# Python minor version of the HTCondor access point, which trixie matches.
ARG AP_PYTHON=3.13
RUN curl -fsSL https://download.pegasus.isi.edu/pegasus/gpg.txt \
         -o /etc/apt/trusted.gpg.d/pegasus.asc && \
    echo "deb [arch=amd64, trusted=yes] https://download.pegasus.isi.edu/pegasus/development/6.0/debian/ ${DEBIAN_SUITE} main" >/etc/apt/sources.list.d/pegasus.list && \
    apt-get update && \
    apt-get install -y pegasus && \
    rm -rf /var/lib/apt/lists/* && \
    pegasus-version && \
    # The Debian package ships Pegasus in its own venv, built against
    # /usr/bin/python3 with include-system-site-packages=false — so the venv's
    # lib path carries the suite's Python minor version, and that path is what
    # the planner bakes into every submit file.
    #
    # Assert it equals the AP's, which is the whole point of moving to Debian
    # (#334). A suite whose Python drifts from the AP's must fail here, at
    # build time, and not 25 nodes into somebody's workflow with an error about
    # a file that exists on both machines. AP_PYTHON is what the AP advertises
    # as PegasusShareDir — see services/submit_host.py, which keeps checking
    # this at runtime because the AP can move independently of this image.
    test -d "/usr/lib/pegasus/venv/lib/python${AP_PYTHON}/site-packages/Pegasus/data"

# Pin Pegasus's CLI tools (pegasus-preflight-check, etc.) to /usr/bin/python3 —
# the same interpreter the packaged venv points at (`venv/bin/python3` is a
# symlink to it), so the tools get the venv's Pegasus deps rather than whatever
# else ends up on PATH. Unversioned: naming a minor version here is how it
# would rot on a suite bump.
ENV PEGASUS_PYTHON=/usr/bin/python3
# Studio's own pool as a hosted-style site catalog (#474): defines "compute",
# the site the workflow template plans on. pegasus-plan looks for it under
# <repo base>/<MAJOR.MINOR>/conf/, so the directory is named after the Pegasus
# just installed; the entrypoint points ~/.pegasusrc at it.
COPY docker/pegasus/site-catalogs/pegasus-studio.yml /tmp/pegasus-studio.yml
RUN series="$(pegasus-version | cut -d. -f1,2)" && \
    install -Dm644 /tmp/pegasus-studio.yml \
        "/opt/studio/site-catalogs/${series}/conf/pegasus-studio.yml" && \
    rm /tmp/pegasus-studio.yml

# ---- Apptainer (Singularity) container runtime ----
# Pegasus workflows run jobs in Singularity containers (e.g. container_type
# SINGULARITY, image "docker://python:3.11-slim"), so the studio needs a
# runtime that can pull and execute docker:// images. Apptainer runs rootless
# via user namespaces, which means the container must be started with
# --privileged (see the Makefile `run` target).
#
# --privileged is necessary but not sufficient. Creating a user namespace with
# UID mappings is also gated on the *host*: Ubuntu 24.04 ships
# `kernel.apparmor_restrict_unprivileged_userns=1`, which blocks unconfined
# unprivileged processes from doing it even inside a privileged container. Root
# in the container builds; the UID the studio runs as does not, and reports
#     ERROR: Failed to create mount namespace: mount namespace requires privileges
# On such a host, set `kernel.apparmor_restrict_unprivileged_userns=0`.
# apptainer-suid does not help: the setuid starter covers *running* a container,
# while an unprivileged *build* needs the user namespace either way.
#
# Installed from the GitHub release .deb rather than the apptainer PPA: the
# pip-bootstrap step above wipes /usr/lib/python3/dist-packages/*, which removes
# apt_pkg and breaks add-apt-repository. `apt-get install ./*.deb` resolves the
# runtime deps (libseccomp, squashfs-tools, uidmap, fuse, …) from the repos.
#
# Take the suite-specific asset, not the plain `apptainer_<ver>_amd64.deb`. The
# generic one is built against libfuse3-3, which trixie does not have (it ships
# libfuse3-4), so it fails dependency resolution outright. The `-trixie+` build
# depends on libfuse3-4 / libseccomp2 >= 2.6 / libsubid5, all present here. The
# `+` is percent-encoded because it is part of the filename, not a space.
ARG APPTAINER_VERSION=1.5.0
RUN curl -fsSL "https://github.com/apptainer/apptainer/releases/download/v${APPTAINER_VERSION}/apptainer_${APPTAINER_VERSION}-${DEBIAN_SUITE}%2B_amd64.deb" \
        -o /tmp/apptainer.deb && \
    apt-get update && \
    apt-get install -y --no-install-recommends /tmp/apptainer.deb && \
    rm -f /tmp/apptainer.deb && \
    rm -rf /var/lib/apt/lists/* && \
    # pegasus-lite probes for the `singularity` command name — provide a compat
    # symlink so it picks up apptainer regardless of which name it looks for.
    { command -v singularity >/dev/null 2>&1 || ln -sf /usr/bin/apptainer /usr/bin/singularity; }

# ---- Build Image support ----
# The workflow page's Build Image action builds a project's Apptainer definition
# into a .sif with the apptainer installed above; there is no separate OCI
# builder, because a workflow references the .sif by absolute path
# (image_site="local") rather than pulling an image from a registry.
#
# Nothing to install for it. An apt.conf used to be copied here and bound into
# builds to stop apt dropping privileges to _apt under an emulated root, but
# Apptainer will not create a bind destination that is absent from the
# container, so it failed every build. The setting now belongs in the definition
# file's %post, where /etc/apt/apt.conf.d exists.

# ---- workflow event log ----
# The studio drives workflow status/job/stats views from the JSONL event log
# (workflow-events.jsonl). That log is now produced by the integrated
# pegasus-monitord build (workflow-monitor is bundled with monitord), so the
# standalone workflow-monitor package is no longer installed explicitly here.
# As a safety net, the studio also reads the stampede DB directly when no JSONL
# is present (see services/stampede_reader.py).

# ---- Node.js runtime (for Next.js standalone server) ----
# Keyring'd apt repo instead of `curl | bash` of NodeSource's setup script as
# root — same pattern as HTCondor above, so package signatures are verified.
RUN install -d /etc/apt/keyrings && \
    curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key \
        | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg && \
    echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_20.x nodistro main" \
        > /etc/apt/sources.list.d/nodesource.list && \
    apt-get update && \
    apt-get install -y --no-install-recommends nodejs && \
    rm -rf /var/lib/apt/lists/* && \
    node --version

# ---- GitHub CLI ----
# Pinned .deb from cli/cli releases, same pattern as the Apptainer step:
# no extra apt source to maintain, reproducible across builds.
# Bump via --build-arg GH_VERSION=... ; verifies with gh --version.
ARG GH_VERSION=2.78.0
RUN curl -fsSL "https://github.com/cli/cli/releases/download/v${GH_VERSION}/gh_${GH_VERSION}_linux_amd64.deb" \
        -o /tmp/gh.deb && \
    apt-get update && \
    apt-get install -y --no-install-recommends /tmp/gh.deb && \
    rm -f /tmp/gh.deb && \
    rm -rf /var/lib/apt/lists/* && \
    gh --version


# ---- npm global prefix ----
# AI tools (Claude Code, OpenCode, Codex CLI, …) are installed on-demand via
# `npm install -g`.  Use a fixed path under /opt so it is writable by any UID
# that KubeSpawner injects at runtime (no HOME dependency).
RUN mkdir -p /opt/npm-global && chmod 777 /opt/npm-global
ENV NPM_CONFIG_PREFIX=/opt/npm-global
ENV PATH="/opt/npm-global/bin:${PATH}"

# ---- default AI tools ----
# OpenCode is both a launchable terminal AI tool and, since #367, the agent
# engine behind the chat panel: `opencode serve` runs under supervisord and
# studio-api proxies the browser to it. Version pinned for reproducible builds,
# and `@opencode-ai/sdk` in studio-web/package.json MUST be the same version —
# the client is generated from this server's OpenAPI document, and CI fails if
# the two drift. The startup seeding step (main._seed_default_tools) registers
# it as installed and runs its knowledge adapter on first boot.
RUN npm install -g opencode-ai@1.18.29 && opencode --version \
    # opencode's install (as root) creates /opt/npm-global/lib/node_modules
    # owned root:root 0755 — the earlier `chmod 777` only covered the top dir,
    # so an unprivileged notebook can't `npm install -g` Codex/Claude into it
    # (EACCES). Re-open the whole tree so any injected UID can install here.
    && chmod -R a+rwX /opt/npm-global \
    # npm keeps a content-addressed cache of everything it downloaded, which is
    # of no use once the package is installed.
    && npm cache clean --force

# ---- interactive shell env for on-demand AI tools ----
# JupyterLab terminals (spawned by jupyter-server, NOT studio-api) get their
# PATH from the image. Point npm at a HOME-writable prefix so `npm install -g`
# works without root AND persists on the PVC (~), and put the HOME bin dirs on
# PATH so tools installed there resolve — e.g. antigravity's CLI lands in
# ~/.local/bin as `agy`. /opt/npm-global/bin stays on PATH for baked opencode.
# Written to profile.d (login shells) and bash.bashrc (interactive non-login,
# which is what Jupyter terminals use).
RUN printf '%s\n' \
      'export NPM_CONFIG_PREFIX="$HOME/.npm-global"' \
      'export PATH="$HOME/.npm-global/bin:$HOME/.local/bin:/opt/npm-global/bin:$PATH"' \
      > /etc/profile.d/10-studio-tools.sh \
    && chmod 0644 /etc/profile.d/10-studio-tools.sh \
    && cat /etc/profile.d/10-studio-tools.sh >> /etc/bash.bashrc

# ---- PegasusAI Chat JupyterLab extension ----
# Docks the studio /chat panel inside JupyterLab (iframe; URL derived from the
# per-user base path). Built with the bundled jlpm; --development True avoids
# the license-webpack-plugin crash on Node 20. The extension dir ships a
# .yarnrc.yml (nodeLinker: node-modules) so the federated build resolves.
COPY jupyter-pegasusai-chat/ /opt/jupyter-pegasusai-chat/
RUN cd /opt/jupyter-pegasusai-chat \
    && touch yarn.lock \
    && jlpm install \
    && jlpm build:lib:prod \
    && jupyter labextension build --development True . \
    && EXTDIR="$(python3 -c 'import sys, os; print(os.path.join(sys.prefix, "share", "jupyter", "labextensions", "jupyter-pegasusai-chat"))')" \
    && mkdir -p "$EXTDIR" \
    && cp -r jupyter_pegasusai_chat/labextension/* "$EXTDIR/" \
    && chmod -R a+rX "$EXTDIR" \
    # $EXTDIR is the only thing read at runtime; node_modules and the yarn
    # caches are scaffolding for the build above. Removed in THIS layer on
    # purpose — a later `rm` would add a whiteout and leave the bytes in the
    # layer that created them, so the image would not shrink at all.
    && rm -rf node_modules .yarn /root/.yarn /root/.cache/yarn \
              /usr/local/share/.cache/yarn

# ---- PegasusAI Bot cell-toolbar client (optional, separate extension) ----
# TauferLab's cell-toolbar assistant (@gclab/pegasus-ai-client / py
# pegasus_ai_client). DISTINCT from the studio's own jupyter-pegasusai-chat
# iframe extension above — different package + labextension path, so the two
# coexist. Toggle the whole feature with ENABLE_PEGASUS_BOT_CLIENT
# (build with =0 to leave it out entirely).
#
# VENDORED (see jupyter-pegasus-ai-client/VENDORED.md): the upstream repo is
# private, so we ship a copy instead of cloning at build time. Our
# src/configPlugin.tsx seeds the server URL + user from PageConfig, injected
# by the Jupyter server (docker/jupyter/jupyter_server_config.py), so no
# manual setup is needed. `pip install` runs the hatch/jlpm labextension build.
ARG ENABLE_PEGASUS_BOT_CLIENT=1
COPY jupyter-pegasus-ai-client/ /opt/jupyter-pegasus-ai-client/
RUN if [ "$ENABLE_PEGASUS_BOT_CLIENT" = "1" ]; then \
      set -eux; \
      cd /opt/jupyter-pegasus-ai-client; \
      pip install --no-cache-dir .; \
      # pip install runs the hatch/jlpm labextension build, so this tree ends
      # up carrying a full node_modules. The wheel and its labextension are
      # already installed into site-packages by now; --no-cache-dir covers
      # pip's cache but nothing cleans jlpm's. Same-layer removal, per above.
      rm -rf node_modules .yarn lib tsconfig.tsbuildinfo \
             /root/.yarn /root/.cache/yarn /usr/local/share/.cache/yarn; \
    else \
      echo "[build] PegasusAI Bot client disabled (ENABLE_PEGASUS_BOT_CLIENT=0)"; \
    fi

# ---- Studio API (Python backend) ----
COPY studio-api/requirements.txt /opt/studio-api/requirements.txt
RUN pip install --no-cache-dir -r /opt/studio-api/requirements.txt

COPY studio-api/ /opt/studio-api/

# ---- Studio Web (Next.js standalone) ----
COPY --from=frontend-build /build/.next/standalone /opt/studio-web/
COPY --from=frontend-build /build/.next/static /opt/studio-web/.next/static
COPY --from=frontend-build /build/public /opt/studio-web/public

# ---- Nginx config templates ----
# Templates, not finished configs: every listener is rendered per session by
# docker/entrypoint.sh into $STUDIO_RUNTIME_DIR, and nginx is started with `-c`
# against the result (see docker/supervisor/supervisord.conf). They cannot be
# rendered into /etc, because the container runs as whatever uid KubeSpawner or
# an Open OnDemand job injects and /etc is not writable by it.
RUN mkdir -p /opt/pegasus-ai/templates
# The whole directory, deliberately, not a list of names. Naming them
# individually is how the four gate fragments were added to docker/nginx/ and
# silently left out of the image: the tests render from the repo directory, so
# they passed, and the only thing that noticed was `nginx -t` at build time.
# A glob cannot forget the next one.
COPY docker/nginx/*.conf.tmpl /opt/pegasus-ai/templates/
# The distro ships a default site listening on :80. Nothing includes it now
# that nginx runs from the rendered config, but leaving it in the image invites
# someone to start nginx without `-c` and get a server that answers on the
# wrong port with none of Studio's routes.
RUN rm -f /etc/nginx/sites-enabled/default /etc/nginx/conf.d/default.conf

# ---- supervisord config and service scripts ----
RUN mkdir -p /etc/supervisor/scripts
COPY docker/supervisor/supervisord.conf /etc/supervisor/supervisord.conf
COPY docker/supervisor/scripts/ /etc/supervisor/scripts/
RUN chmod +x /etc/supervisor/scripts/*.sh

# ---- Knowledge assets (skills, agents, MCP, templates, examples) ----
# Authored once in pegasus-isi/claude-plugin-marketplace and checked out here.
# Studio deliberately does not carry its own copy: when it did, the two drifted
# to the point of giving different container advice depending on which AI tool
# the user launched.
#
# A checkout rather than a package because the update story is better in every
# direction that matters here: the SHA is the version (a package version has to
# be bumped by hand and silently lies when it isn't), `git pull` moves kilobytes
# where a reinstall re-downloads everything, and rollback is a checkout. .git is
# kept — 320 KB — so all three work inside a running pod without a rebuild.
#
# This tracks main rather than pinning a SHA, deliberately. The user's checkout
# fast-forwards to origin/main at every start, so a pin here would have decided
# only what the first launch cloned before being superseded seconds later — it
# would have described the image, never the running system, while reading like a
# guarantee. What a session is actually running is its checkout's SHA, which the
# Settings > Tools panel reports and `git -C ~/.local/share/pegasus-ai/marketplace
# log` confirms.
#
# Override to build against a fixed commit when you need one — an incident, a
# bisect, an air-gapped host that will never pull:
#   docker build --build-arg KNOWLEDGE_REF=<sha> ...
ARG KNOWLEDGE_REF=main
ARG KNOWLEDGE_REPO=https://github.com/pegasus-isi/claude-plugin-marketplace.git
RUN git clone --quiet "${KNOWLEDGE_REPO}" /opt/pegasus-ai/marketplace && \
    git -C /opt/pegasus-ai/marketplace checkout --quiet "${KNOWLEDGE_REF}" && \
    git -C /opt/pegasus-ai/marketplace log -1 --format='knowledge %H %s' && \
    test -f /opt/pegasus-ai/marketplace/plugins/pegasus-ai/.mcp.json && \
    chmod -R a+rX /opt/pegasus-ai/marketplace
# The checkout is root-owned and the pod runs as whatever UID KubeSpawner
# injects, so git refuses it ("detected dubious ownership") — which would make
# the .git kept above useless for the pull/rollback/verify it exists for.
#
# BOTH paths are needed. `git -C <dir>` checks the working-tree path, but
# `git clone <dir>` resolves the source to <dir>/.git and checks that instead —
# so with only the first entry, inspection worked while the clone the entrypoint
# makes failed, and failed quietly.
RUN git config --system --add safe.directory /opt/pegasus-ai/marketplace && \
    git config --system --add safe.directory /opt/pegasus-ai/marketplace/.git

# Deliberately NOT setting KNOWLEDGE_ROOT: it is the highest-priority override,
# so pinning it here would force every user onto this read-only copy. The
# entrypoint clones this into the user's own $HOME checkout at first launch and
# layout.py prefers that, so skills can be updated or rolled back without root
# and the change survives a pod restart. This copy is the seed and the fallback.
ENV KNOWLEDGE_REPO_URL=${KNOWLEDGE_REPO}

# Studio-specific context (which directories persist, where runs land) that does
# not belong in a plugin someone installs on a laptop. Composed on top of the
# portable guide when a tool's context file is generated.
COPY knowledge/overlay/ /opt/pegasus-ai/overlay/

# ---- Entrypoint ----
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# Validate the rendered nginx configuration at build time. The templates are
# only exercised at session start, so a syntax error in one would otherwise
# first appear as a container that boots with no proxy — and nginx reports it
# by line number, from a file that does not exist until the session runs.
# --render-only stops before anything touches $HOME, so this leaves no state.
RUN STUDIO_RUNTIME_DIR=/tmp/nginx-validate /usr/local/bin/entrypoint.sh --render-only \
    && nginx -t -c /tmp/nginx-validate/nginx.conf \
    && rm -rf /tmp/nginx-validate

# And again with the session token gate on. The gate fragments render to bare
# comments by default, so the pass above never parses a single one of their
# directives — a broken map, `if` or location in them would first surface on a
# multi-user host, which is the one deployment where a proxy that fails to
# start is least acceptable. The prefix is set too, since it changes the
# rendered regexes.
RUN STUDIO_RUNTIME_DIR=/tmp/nginx-validate-gate \
    STUDIO_AUTH_TOKEN=buildtimevalidationtoken \
    STUDIO_URL_PREFIX=/node/example/12345 \
    /usr/local/bin/entrypoint.sh --render-only \
    && nginx -t -c /tmp/nginx-validate-gate/nginx.conf \
    && rm -rf /tmp/nginx-validate-gate

# ---- Runtime workspace configuration ----
# STUDIO_WORKSPACE_DIR: where the app stores the SQLite DB, tool configs, and
# workflow projects.  Defaults to $HOME/work (KubeSpawner sets $HOME).
# Override by setting STUDIO_WORKSPACE_DIR in the pod environment.
# The directory is created at first use by the API; no build-time mkdir needed.

WORKDIR /opt

# The default front port. A deployment that overrides STUDIO_PORT publishes
# that port itself (OOD allocates one per session); EXPOSE is documentation for
# the default, not a constraint on it.
EXPOSE 8888

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
