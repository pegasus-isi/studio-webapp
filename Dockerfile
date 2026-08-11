# PegasusAI Studio — Multi-stage Docker build
#
# Stage 1: Build Next.js frontend (standalone output ~15MB)
# Stage 2: Runtime — Pegasus/HTCondor + studio-api + frontend + supervisord

# ============================================================
# Stage 1: Build frontend
# ============================================================
FROM node:20-alpine AS frontend-build

WORKDIR /build
COPY studio-web/package.json studio-web/package-lock.json* ./
RUN npm ci --ignore-scripts 2>/dev/null || npm install

COPY studio-web/ ./
RUN npm run build

# ============================================================
# Stage 2: Runtime
# ============================================================
FROM ubuntu:24.04 AS runtime

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

# ---- Remove default ubuntu user ----
# The container runs as whatever UID KubeSpawner injects via the pod security
# context — no fixed user is created here.
RUN userdel -r ubuntu 2>/dev/null || true

# ---- System packages (no python3-pip — we bootstrap pip cleanly below) ----
# Retry-with-recovery loop: under amd64 emulation a dpkg post-install can be
# killed mid-configure (exit 1) at a random package. Re-run dpkg --configure -a
# + apt-get install -f to repair the half-configured state, then retry.
RUN ok=0; for i in 1 2 3 4 5; do \
      apt-get update && apt-get install -y --no-install-recommends \
        libnss-sss \
        nginx \
        xz-utils \
        curl \
        wget \
        git \
        vim \
        build-essential \
        software-properties-common \
        python3.12 \
        python3.12-venv \
        python3.12-dev \
      && { ok=1; break; }; \
      echo "[apt] attempt $i failed — repairing and retrying"; \
      dpkg --configure -a || true; apt-get install -f -y || true; sleep 5; \
    done; [ "$ok" = 1 ] || { echo "[apt] failed after 5 attempts"; exit 1; } \
    && update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.12 1 \
    && update-alternatives --install /usr/bin/python python /usr/bin/python3.12 1 \
    && rm -rf /var/lib/apt/lists/*

# nsswitch - for user/group lookups
COPY docker/nsswitch.conf /etc/nsswitch.conf

# ---- pip bootstrap ----
# Wipe all apt-installed Python packages (they lack RECORD files and block
# pip from upgrading transitive deps like PyJWT, cryptography, etc.).
# Then bootstrap a clean pip from get-pip.py.
RUN rm -f /usr/lib/python3.12/EXTERNALLY-MANAGED && \
    rm -rf /usr/lib/python3/dist-packages/* && \
    # sha256-pinned instead of curl|python3 into the root interpreter: a new
    # upstream get-pip.py fails the checksum loudly (bump the hash on a pip
    # release) rather than executing unreviewed code at build time.
    curl -fsSL -o /tmp/get-pip.py https://bootstrap.pypa.io/get-pip.py && \
    echo "fb24e693bab954209a063d90953621412ccad4a500905a726286e038f508ddf6  /tmp/get-pip.py" \
        | sha256sum -c - && \
    python3 /tmp/get-pip.py && \
    rm -f /tmp/get-pip.py && \
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
    scipy

# Defensive: ensure no RTC/collaboration/awareness/document packages slipped in
# transitively — they drive the 403 collaboration-room loop behind the proxy.
RUN python3 -m pip uninstall -y \
    jupyter-collaboration jupyter-collaboration-ui jupyter-docprovider \
    jupyter-server-ydoc jupyter-server-documents \
    jupyterlab-notebook-awareness jupyterlab-chat \
    || true

# ---- Jupyter server configuration ----
COPY docker/jupyter/jupyter_server_config.py /etc/jupyter/jupyter_server_config.py

# ---- supervisord (process supervisor) ----
RUN pip install --no-cache-dir supervisor

# ---- HTCondor + Pegasus WMS (amd64 DEB packages) ----
# Native DEB packages provide full daemon support (condor_master) and the
# Pegasus planner (pegasus-plan), and install their Python deps for the single
# system Python 3.12. The image is built for linux/amd64 (see the Makefile);
# HTCondor publishes no arm64 DEB, so other arches are unsupported here.
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
    curl -fsSL https://htcss-downloads.chtc.wisc.edu/repo/ubuntu/htcondor-25.x-noble.list \
        -o /etc/apt/sources.list.d/htcondor.list && \
    apt-get update && \
    apt-get install -y --no-install-recommends condor && \
    rm -rf /var/lib/apt/lists/*
COPY docker/htcondor/90-studio.conf /etc/condor/config.d/90-studio.conf
COPY docker/pegasus/pegasus.properties /etc/pegasus/pegasus.properties

# ---- Pegasus (pinned dev .deb with the monitord plugin patch) ----
# The Pegasus development apt repo publishes UNSIGNED metadata (no
# InRelease/Release.gpg as of 2026-08, so [signed-by=…] cannot work and it
# previously shipped with [trusted=yes]) — and the repo's `pegasus` package
# was immediately force-downgraded by this pinned .deb anyway. Installing the
# checksummed .deb directly drops the unsigned repo entirely: sha256 is the
# integrity anchor, and apt resolves its dependencies from the signed
# Ubuntu/HTCondor repos. Update BOTH the filename and the hash on a bump.
RUN PEG_DEB="pegasus_6.0.0~-dev.0202606301145-1+ubuntu24_amd64.deb" && \
    PEG_SHA256="1fe10d48403bd8c5ce8b0b42712755f518019681b949c47eb0e0ac4d645352bf" && \
    curl -fsSL -o "/tmp/${PEG_DEB}" \
        "https://download.pegasus.isi.edu/pegasus-ai/${PEG_DEB}" && \
    echo "${PEG_SHA256}  /tmp/${PEG_DEB}" | sha256sum -c - && \
    apt-get update && \
    apt-get install -y --no-install-recommends "/tmp/${PEG_DEB}" && \
    rm -f "/tmp/${PEG_DEB}" && \
    rm -rf /var/lib/apt/lists/* && \
    pegasus-version

# Pin Pegasus's CLI tools (pegasus-preflight-check, etc.) explicitly to the
# system Python 3.12 — the interpreter that carries Pegasus's DEB deps.
ENV PEGASUS_PYTHON=/usr/bin/python3

# ---- Apptainer (Singularity) container runtime ----
# Pegasus workflows run jobs in Singularity containers (e.g. container_type
# SINGULARITY, image "docker://python:3.11-slim"), so the studio needs a
# runtime that can pull and execute docker:// images. Apptainer runs rootless
# via user namespaces — which means the container must be started with
# --privileged (see the Makefile `run` target); inside an unprivileged
# container the kernel blocks unprivileged_userns_clone.
#
# Installed from the GitHub release .deb rather than the apptainer PPA: the
# pip-bootstrap step above wipes /usr/lib/python3/dist-packages/*, which removes
# apt_pkg and breaks add-apt-repository. `apt-get install ./*.deb` resolves the
# runtime deps (libseccomp, squashfs-tools, uidmap, fuse, …) from the repos.
ARG APPTAINER_VERSION=1.5.0
RUN curl -fsSL "https://github.com/apptainer/apptainer/releases/download/v${APPTAINER_VERSION}/apptainer_${APPTAINER_VERSION}_amd64.deb" \
        -o /tmp/apptainer.deb && \
    apt-get update && \
    apt-get install -y --no-install-recommends /tmp/apptainer.deb && \
    rm -f /tmp/apptainer.deb && \
    rm -rf /var/lib/apt/lists/* && \
    # pegasus-lite probes for the `singularity` command name — provide a compat
    # symlink so it picks up apptainer regardless of which name it looks for.
    { command -v singularity >/dev/null 2>&1 || ln -sf /usr/bin/apptainer /usr/bin/singularity; }

# ---- Podman (daemonless image builds) ----
# The workflow page's Build Image action builds project Dockerfiles with
# podman (rootless, no daemon — there is no dockerd in this container) and
# pushes to the registry configured in Settings. fuse-overlayfs backs the
# rootless storage driver; podman falls back to vfs where userns/fuse are
# unavailable (slower but correct). storage.conf tunes rootless builds for
# the deployment reality: NFS-backed $HOME (force_mask, kept deliberately —
# cache survives pod restarts) and spawner UIDs without /etc/subuid ranges
# (ignore_chown_errors).
RUN apt-get update && apt-get install -y --no-install-recommends \
      podman fuse-overlayfs slirp4netns && \
    rm -rf /var/lib/apt/lists/*
COPY docker/containers/storage.conf /etc/containers/storage.conf
# Mounted into Build Image runs so apt keeps running as root (avoids the
# _apt privilege-drop failing under podman's single-UID mapping). See
# studio-api/services/image_builder.py.
COPY docker/containers/apt-no-sandbox.conf /etc/studio/apt-no-sandbox.conf

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

# ---- npm global prefix ----
# AI tools (Claude Code, OpenCode, Codex CLI, …) are installed on-demand via
# `npm install -g`.  Use a fixed path under /opt so it is writable by any UID
# that KubeSpawner injects at runtime (no HOME dependency).
RUN mkdir -p /opt/npm-global && chmod 777 /opt/npm-global
ENV NPM_CONFIG_PREFIX=/opt/npm-global
ENV PATH="/opt/npm-global/bin:${PATH}"

# ---- default AI tools ----
# OpenCode ships in the image so the chat panel's OpenCode engine and
# `opencode attach` work out of the box. Version pinned: the chat engine's
# event translation is validated against this release's API (/doc OpenAPI).
# The startup seeding step (main._seed_default_tools) registers it as
# installed and runs its knowledge adapter on first boot.
RUN npm install -g opencode-ai@1.17.20 && opencode --version \
    # opencode's install (as root) creates /opt/npm-global/lib/node_modules
    # owned root:root 0755 — the earlier `chmod 777` only covered the top dir,
    # so an unprivileged notebook can't `npm install -g` Codex/Claude into it
    # (EACCES). Re-open the whole tree so any injected UID can install here.
    && chmod -R a+rwX /opt/npm-global

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
    && chmod -R a+rX "$EXTDIR"

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

# ---- Nginx config ----
COPY docker/nginx/nginx.conf /etc/nginx/nginx.conf
COPY docker/nginx/default.conf /etc/nginx/sites-available/default

# ---- supervisord config and service scripts ----
RUN mkdir -p /etc/supervisor/scripts
COPY docker/supervisor/supervisord.conf /etc/supervisor/supervisord.conf
COPY docker/supervisor/scripts/ /etc/supervisor/scripts/
RUN chmod +x /etc/supervisor/scripts/*.sh

# ---- Knowledge assets (skills, agents, MCP, templates, examples) ----
COPY knowledge/ /opt/pegasus-ai/knowledge/

# ---- Entrypoint ----
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# ---- Runtime workspace configuration ----
# STUDIO_WORKSPACE_DIR: where the app stores the SQLite DB, tool configs, and
# workflow projects.  Defaults to $HOME/work (KubeSpawner sets $HOME).
# Override by setting STUDIO_WORKSPACE_DIR in the pod environment.
# The directory is created at first use by the API; no build-time mkdir needed.

WORKDIR /opt

EXPOSE 8888

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
