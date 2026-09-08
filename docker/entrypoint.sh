#!/usr/bin/env bash
# PegasusAI Studio container entrypoint.
#
# First-launch: initialize DB, seed config, prepare workspace.
# Then delegates to supervisord for process supervision.
#
# Runs as whatever UID KubeSpawner injects — no root assumed.

set -euo pipefail

WORK="${HOME:-/tmp}/work"
# Studio's own state lives beside the user's other config, not inside the
# directory they keep workflows in: a `rm -rf` in the workspace should not be
# able to take the settings database with it. init_db.py migrates an existing
# database from the old location on first start.
STUDIO_DIR="${HOME:-/tmp}/.studio"
MARKER="${STUDIO_DIR}/.initialized"

# workflows/ is where the Marketplace "Add" clones into and where the scaffold
# skills are told to put projects, so it should exist before anyone looks.
mkdir -p "${WORK}" "${STUDIO_DIR}" "${WORK}/workflows"

# Nginx temp dirs must exist before nginx starts (non-root can't create them
# under /var/lib/nginx, so we use /tmp paths configured in nginx.conf).
mkdir -p /tmp/nginx-client-body /tmp/nginx-proxy /tmp/nginx-fastcgi \
         /tmp/nginx-uwsgi /tmp/nginx-scgi

PYTHON="$(which python3 2>/dev/null || which python 2>/dev/null || echo python3)"

# ---- Marketplace checkout (skills, agents, templates) ----
# The image ships a pinned copy at /opt/pegasus-ai/marketplace. It is a template
# to copy from, not the tree tools read: it is root-owned and is replaced
# wholesale on every image rebuild.
#
# (Not to be confused with the Claude Code *plugin seed directory* written by
# knowledge/seed.py — that one points Claude Code at whichever checkout wins.)
#
# Clone it into the user's own $HOME on first launch: there it is writable, so
# skills can be updated with `git pull` or rolled back with `git checkout` —
# no root, no rebuild — and it persists across pod restarts, which a change
# under /opt would not.
#
# Cloning from the local seed rather than GitHub keeps first launch fast and
# working with no egress. origin is then repointed upstream so `git pull` does
# the obvious thing. Non-fatal throughout: layout.py falls back to the seed.
IMAGE_MARKETPLACE="/opt/pegasus-ai/marketplace"
USER_MARKETPLACE="${XDG_DATA_HOME:-${HOME:-/tmp}/.local/share}/pegasus-ai/marketplace"
if [ ! -d "${USER_MARKETPLACE}/.claude-plugin" ] \
   && [ -d "${IMAGE_MARKETPLACE}/.claude-plugin" ]; then
    CLONE_ERR=""
    if mkdir -p "$(dirname "${USER_MARKETPLACE}")" \
       && CLONE_ERR="$(git clone --quiet "${IMAGE_MARKETPLACE}" "${USER_MARKETPLACE}" 2>&1)"; then
        git -C "${USER_MARKETPLACE}" remote set-url origin \
            "${KNOWLEDGE_REPO_URL:-https://github.com/pegasus-isi/claude-plugin-marketplace.git}" \
            2>/dev/null || true
        echo "[studio] Marketplace checked out to ${USER_MARKETPLACE}"
    else
        # Print git's own message: a bare "could not create" sent one
        # investigation looking at NFS permissions when the cause was git
        # refusing the source repository's ownership.
        echo "[studio] WARNING: could not create ${USER_MARKETPLACE} —" \
             "falling back to the read-only ${IMAGE_MARKETPLACE}: ${CLONE_ERR}" >&2
    fi
fi

# The checkout is kept current by studio-api at startup, not here. Pulling from
# the entrypoint raced the tools that read the result: CLI tools load skills
# once when they start and never reload, so whether the assistant got today's
# skills came down to which background job finished first, with nothing in any
# log to say which way it went. studio-api owns it now and re-propagates when
# the checkout actually moves.

# What the tools will actually read, for the AGENTS.md seed below and the log.
if [ -d "${USER_MARKETPLACE}/plugins/pegasus-ai" ]; then
    RESOLVED_KNOWLEDGE="${USER_MARKETPLACE}/plugins/pegasus-ai"
else
    RESOLVED_KNOWLEDGE="${IMAGE_MARKETPLACE}/plugins/pegasus-ai"
fi

if [ ! -f "${MARKER}" ]; then
    echo "[studio] First launch — initializing..."

    cd /opt/studio-api
    # Hard timeout: this touches the NFS-backed SQLite DB, and a stale lock
    # from a crashed prior pod (or an NFS stall) would otherwise block here
    # forever — supervisord never starts, JupyterHub kills the spawn at 30s,
    # and every retry hits the same lock. Same rationale as the timeout on
    # propagate_llm.py below, but init is a hard prerequisite, so fail loud
    # instead of backgrounding. (timeout exits 124 on timeout.)
    if ! timeout 60 "${PYTHON}" init_db.py; then
        echo "[studio] FATAL: init_db.py failed or timed out (>60s) —" \
             "check for a stale SQLite lock on ${STUDIO_DIR}/studio.db" >&2
        exit 1
    fi

    touch "${MARKER}"
    echo "[studio] Initialization complete."
fi

# AGENTS.md: the Studio environment overlay followed by the portable Pegasus
# guide, matching what the knowledge adapters generate.
#
# Written on every start when absent, not only on first launch. The adapters
# only create it during install(), and startup runs update_llm_config(), so a
# workspace that lost the file — a user clearing it out, say — otherwise never
# got it back and the tools ran with no Pegasus context at all.
# Written at $HOME, not $HOME/work: every tool looks in the directory it starts
# in and then upwards, and terminals start at $HOME, so a copy one directory
# down existed without being found.
CONFIG_ROOT="${HOME:-/tmp}"
OVERLAY="/opt/pegasus-ai/overlay/STUDIO_ENV.md"
GUIDE="${KNOWLEDGE_ROOT:-${RESOLVED_KNOWLEDGE}}/references/PEGASUS.md"
if [ ! -f "${CONFIG_ROOT}/AGENTS.md" ] && { [ -f "${OVERLAY}" ] || [ -f "${GUIDE}" ]; }; then
    # Written with explicit ifs, not `[ -f x ] && cat x`: under `set -e` a
    # failing test makes the whole && chain the script's last status and
    # kills the entrypoint before supervisord ever starts.
    : > "${CONFIG_ROOT}/AGENTS.md"
    if [ -f "${OVERLAY}" ]; then
        cat "${OVERLAY}" >> "${CONFIG_ROOT}/AGENTS.md"
        printf '\n' >> "${CONFIG_ROOT}/AGENTS.md"
    fi
    if [ -f "${GUIDE}" ]; then
        cat "${GUIDE}" >> "${CONFIG_ROOT}/AGENTS.md"
    fi
    echo "[studio] Wrote ${CONFIG_ROOT}/AGENTS.md"
elif [ ! -f "${CONFIG_ROOT}/AGENTS.md" ]; then
    echo "[studio] WARNING: no knowledge tree found — AGENTS.md not written" >&2
fi

# Pegasus agent endpoint — without it pegasus-analyzer probes the default
# agent URL and stalls badly. HOME is the user PVC, so this must be written
# at runtime (an image-baked copy under /home would be shadowed by the
# mount). Runs on every start, not just first launch, so existing pods pick
# it up; appends only when the key is absent, preserving user edits.
PEGASUS_CONF="${HOME:-/tmp}/.pegasus/pegasus.conf"
PEGASUS_AGENT_URL="${STUDIO_PEGASUS_AGENT_URL:-https://api.pegasus-ai.org}"
if ! grep -qs '^pegasus\.agent\.url' "${PEGASUS_CONF}"; then
    mkdir -p "$(dirname "${PEGASUS_CONF}")"
    echo "pegasus.agent.url = ${PEGASUS_AGENT_URL}" >> "${PEGASUS_CONF}"
    echo "[studio] Wrote pegasus.agent.url to ${PEGASUS_CONF}"
fi

# ---- OpenCode server password ----
# The chat panel is a client of `opencode serve` (#367), which runs on loopback
# with the user's full file and shell authority. Basic auth is the only thing
# between it and anything else running in the pod (a workflow job, say), so a
# password is generated per user rather than shared or omitted.
#
# Written to a 0600 file under $STUDIO_DIR — not minted per boot — so a restart
# does not invalidate the credential studio-api and the wrapper script are
# both reading, and exported so supervisord's children inherit it.
#
# python3 rather than openssl: openssl is not guaranteed in this image, and a
# missing binary here would take the whole entrypoint down under `set -e`.
OPENCODE_PW_FILE="${STUDIO_DIR}/opencode-password"
if [ ! -s "${OPENCODE_PW_FILE}" ]; then
    # Per-process temp name, not a fixed ".tmp": the home directory is NFS and
    # shared, so two pods first-booting at once would write the same path and
    # the losing rename would leave studio-api reading a password the engine
    # never saw — a 401 nothing in the UI can clear.
    OPENCODE_PW_TMP="${OPENCODE_PW_FILE}.$$"
    if ( umask 077 && "${PYTHON}" -c 'import secrets,sys; sys.stdout.write(secrets.token_urlsafe(32))' \
         > "${OPENCODE_PW_TMP}" ) 2>/dev/null; then
        chmod 600 "${OPENCODE_PW_TMP}"
        mv "${OPENCODE_PW_TMP}" "${OPENCODE_PW_FILE}"
        echo "[studio] Generated OpenCode server password at ${OPENCODE_PW_FILE}"
    else
        rm -f "${OPENCODE_PW_TMP}"
        echo "[studio] WARNING: could not generate an OpenCode server password —" \
             "the chat engine will not start" >&2
    fi
fi
if [ -s "${OPENCODE_PW_FILE}" ]; then
    chmod 600 "${OPENCODE_PW_FILE}" 2>/dev/null || true
    OPENCODE_SERVER_PASSWORD="$(cat "${OPENCODE_PW_FILE}")"
    export OPENCODE_SERVER_PASSWORD
fi

# Propagate saved LLM config to installed tool adapters. Run in the background
# with a hard timeout so it can NEVER gate supervisord startup: this touches the
# NFS-backed SQLite DB, and a blocked write here would otherwise stall the
# notebook server past JupyterHub's 30s spawn timeout. Non-fatal; logged for
# inspection rather than discarded.
( cd /opt/studio-api && timeout 20 "${PYTHON}" propagate_llm.py \
    >"${STUDIO_DIR}/propagate_llm.log" 2>&1 || true ) &

exec supervisord -c /etc/supervisor/supervisord.conf
