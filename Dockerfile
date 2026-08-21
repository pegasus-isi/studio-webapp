# PegasusAI Studio — Multi-stage Docker build
#
# Stage 1: Build Next.js frontend (standalone output ~15MB)
# Stage 2: Runtime — Pegasus/HTCondor + studio-api + frontend + supervisord

# ============================================================
# Stage 1: Build frontend
# ============================================================
FROM node:20-alpine AS frontend-build

WORKDIR /build
# No glob on the lockfile and no `|| npm install` fallback: a missing or drifted
# package-lock.json must fail the build here, not silently float every frontend
# dependency to whatever npm resolves on the day of the build (which is also how
# a failing `npm ci` stayed invisible — its stderr went to /dev/null). CI's test
# job already proves `npm ci` works against this lockfile.
COPY studio-web/package.json studio-web/package-lock.json ./
RUN npm ci --ignore-scripts

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
        poppler-utils \
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

# ---- Pegasus 6.0 (pinned development build) ----
# Pegasus 6.0 is unreleased: the signed stable repo carries 5.1.x only, and the
# integrated monitord/workflow-monitor this studio reads its event log from
# exists only in the 6.0 development builds. That development apt repo publishes
# an UNSIGNED Release — no InRelease, no Release.gpg (probed 2026-08-21) — so
# apt cannot authenticate it, and the only way to consume it as a repo was with
# verification switched off. That is what this block did until now, and it
# silently un-did the checksums PR #66 added; the CI guard and
# studio-api/tests/test_dockerfile_guards.py now reject that shape.
#
# So: one development .deb, pinned by URL + sha256. The hash below is the
# integrity anchor — it is reviewed in the PR that bumps it — and apt resolves
# the .deb's dependencies (default-jre, graphviz, python3-{cryptography,yaml,git})
# from the signed Ubuntu archive. Its `Recommends: condor` is already satisfied
# by the HTCondor repo above.
#
# The nightly pool keeps only ~4 days of builds, so a pinned URL into the pool
# 404s within the week. The pinned .deb therefore wants a persistent home:
#   H1  https://download.pegasus.isi.edu/pegasus-ai/<filename> — Pegasus-side
#       copy, precedent exists (PR #66 pinned a .deb from there and it is still
#       served); one `cp` on the download host by the nightly's maintainer.
#   H2  a GitHub release asset on this repo (long-lived tag, not matching v*).
# To bump:
#   curl -s https://download.pegasus.isi.edu/pegasus/development/6.0/ubuntu/dists/noble/main/binary-amd64/Packages \
#     | awk '/^Filename:/{f=$2} /^SHA256:/{s=$2} END{print f; print s}'
#   download it, confirm sha256sum matches that index value, copy it to the
#   persistent host, then update BOTH args below in one commit.
# Long-term fix: ask Pegasus to sign the development repo's Release with the key
# that already signs the stable one (keyid F4F1E34BAB1B8BCF) — then this goes
# back to being a [signed-by=] apt source with the key vendored in docker/pegasus/.
ARG PEGASUS_DEB_URL="https://download.pegasus.isi.edu/pegasus/development/6.0/ubuntu/dists/noble/main/binary-amd64/pegasus_6.0.0~-dev.0202608200518-1+ubuntu24_amd64.deb"
ARG PEGASUS_DEB_SHA256="7f7d9901acfbe7ca9035397c6f5b2bf3543281a718e73ba78cf45fe93c121835"
RUN { curl -fsSL -o /tmp/pegasus.deb "${PEGASUS_DEB_URL}" || \
      { echo "[pegasus] download failed: ${PEGASUS_DEB_URL}" >&2; \
        echo "[pegasus] if 404: the pinned nightly was pruned — bump PEGASUS_DEB_URL and PEGASUS_DEB_SHA256 together (see comment above)" >&2; \
        exit 1; }; } && \
    echo "${PEGASUS_DEB_SHA256}  /tmp/pegasus.deb" | sha256sum -c - && \
    apt-get update && \
    apt-get install -y --no-install-recommends /tmp/pegasus.deb && \
    rm -f /tmp/pegasus.deb && \
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
# sha256 of apptainer_${APPTAINER_VERSION}_amd64.deb. Apptainer publishes no
# checksum file alongside the release and its tags are unsigned, so the hash
# lives here — a replaced asset fails the build instead of installing as root.
# On a version bump, recompute and change both args in the same commit:
#   curl -fsSL https://github.com/apptainer/apptainer/releases/download/v<V>/apptainer_<V>_amd64.deb | sha256sum
ARG APPTAINER_DEB_SHA256=fbc27204d0ec0440dfa0ae589089e4b2baf192315b4ac22dfae02b78b28981ea
RUN curl -fsSL "https://github.com/apptainer/apptainer/releases/download/v${APPTAINER_VERSION}/apptainer_${APPTAINER_VERSION}_amd64.deb" \
        -o /tmp/apptainer.deb && \
    echo "${APPTAINER_DEB_SHA256}  /tmp/apptainer.deb" | sha256sum -c - && \
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
RUN npm install -g opencode-ai@1.18.8 && opencode --version \
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
# .yarnrc.yml (nodeLinker: node-modules) so the federated build resolves, and a
# committed yarn.lock (generated with the yarn 3.5.0 that jlpm bundles; see
# README "JupyterLab extension lockfile" to regenerate). --immutable makes any
# drift between package.json and the lockfile — or a future jupyterlab bump that
# changes the bundled yarn's lockfile format — fail here, loudly, instead of
# re-resolving the whole tree from the registry on every build, which is what
# `touch yarn.lock && jlpm install` did. --immutable must be explicit: Yarn
# Berry only turns it on by default when CI is set in the environment.
COPY jupyter-pegasusai-chat/ /opt/jupyter-pegasusai-chat/
RUN cd /opt/jupyter-pegasusai-chat \
    && jlpm install --immutable \
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

# ---- Nginx config ----
COPY docker/nginx/nginx.conf /etc/nginx/nginx.conf
COPY docker/nginx/default.conf /etc/nginx/sites-available/default

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

# ---- Runtime workspace configuration ----
# STUDIO_WORKSPACE_DIR: where the app stores the SQLite DB, tool configs, and
# workflow projects.  Defaults to $HOME/work (KubeSpawner sets $HOME).
# Override by setting STUDIO_WORKSPACE_DIR in the pod environment.
# The directory is created at first use by the API; no build-time mkdir needed.

WORKDIR /opt

EXPOSE 8888

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
