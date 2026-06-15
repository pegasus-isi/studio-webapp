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
    curl -fsSL https://bootstrap.pypa.io/get-pip.py | python3 && \
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
    curl -fsSL https://download.pegasus.isi.edu/pegasus/gpg.txt \
        | gpg --dearmor -o /usr/share/keyrings/pegasus.gpg && \
    echo "deb [trusted=yes] https://download.pegasus.isi.edu/pegasus/development/5.2/ubuntu noble main" \
        > /etc/apt/sources.list.d/pegasus.list && \
    apt-get update && \
    apt-get install -y --no-install-recommends condor pegasus && \
    rm -rf /var/lib/apt/lists/*
COPY docker/htcondor/90-studio.conf /etc/condor/config.d/90-studio.conf

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

# ---- workflow-monitor ----
# The studio drives all workflow status/job/stats views from the JSONL event
# log produced by `workflow-monitor --serve` (started automatically on submit),
# rather than parsing the stampede DB itself. Installed into the single system
# Python 3.12. HTCondor pool polling uses the condor CLI (already on PATH), so
# the optional [htcondor] bindings extra is intentionally omitted.
RUN pip install --no-cache-dir \
    "git+https://github.com/pegasus-isi/workflow-monitor"

# ---- Node.js runtime (for Next.js standalone server) ----
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && \
    rm -rf /var/lib/apt/lists/*

# ---- npm global prefix ----
# AI tools (Claude Code, OpenCode, Codex CLI, …) are installed on-demand via
# `npm install -g`.  Use a fixed path under /opt so it is writable by any UID
# that KubeSpawner injects at runtime (no HOME dependency).
RUN mkdir -p /opt/npm-global && chmod 777 /opt/npm-global
ENV NPM_CONFIG_PREFIX=/opt/npm-global
ENV PATH="/opt/npm-global/bin:${PATH}"

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
