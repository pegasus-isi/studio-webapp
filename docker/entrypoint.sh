#!/usr/bin/env bash
# PegasusAI Studio container entrypoint.
#
# First-launch: initialize DB, seed config, prepare workspace.
# Then delegates to supervisord for process supervision.
#
# Runs as whatever UID KubeSpawner injects — no root assumed.

set -euo pipefail

WORK="${HOME:-/tmp}/work"
STUDIO_DIR="${WORK}/.studio"
MARKER="${STUDIO_DIR}/.initialized"

mkdir -p "${WORK}" "${STUDIO_DIR}"

# Nginx temp dirs must exist before nginx starts (non-root can't create them
# under /var/lib/nginx, so we use /tmp paths configured in nginx.conf).
mkdir -p /tmp/nginx-client-body /tmp/nginx-proxy /tmp/nginx-fastcgi \
         /tmp/nginx-uwsgi /tmp/nginx-scgi

PYTHON="$(which python3 2>/dev/null || which python 2>/dev/null || echo python3)"

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

    # Seed AGENTS.md: the Studio environment overlay followed by the portable
    # Pegasus guide, matching what the knowledge adapters generate. Only the
    # tool adapters rewrite this afterwards, and the startup reconcile
    # reinstalls tool *binaries* without re-running them — so if this is
    # skipped, a workspace can end up with no AGENTS.md at all.
    OVERLAY="/opt/pegasus-ai/overlay/STUDIO_ENV.md"
    GUIDE="${KNOWLEDGE_ROOT:-/opt/pegasus-ai/marketplace/plugins/pegasus-ai}/references/PEGASUS.md"
    if [ -f "${OVERLAY}" ] || [ -f "${GUIDE}" ]; then
        # Written with explicit ifs, not `[ -f x ] && cat x`: under `set -e` a
        # failing test makes the whole && chain the script's last status and
        # kills the entrypoint before supervisord ever starts.
        : > "${WORK}/AGENTS.md"
        if [ -f "${OVERLAY}" ]; then
            cat "${OVERLAY}" >> "${WORK}/AGENTS.md"
            printf '\n' >> "${WORK}/AGENTS.md"
        fi
        if [ -f "${GUIDE}" ]; then
            cat "${GUIDE}" >> "${WORK}/AGENTS.md"
        fi
        echo "[studio] Wrote ${WORK}/AGENTS.md"
    else
        echo "[studio] WARNING: no knowledge tree found — AGENTS.md not written" >&2
    fi

    touch "${MARKER}"
    echo "[studio] Initialization complete."
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

# Propagate saved LLM config to installed tool adapters. Run in the background
# with a hard timeout so it can NEVER gate supervisord startup: this touches the
# NFS-backed SQLite DB, and a blocked write here would otherwise stall the
# notebook server past JupyterHub's 30s spawn timeout. Non-fatal; logged for
# inspection rather than discarded.
( cd /opt/studio-api && timeout 20 "${PYTHON}" propagate_llm.py \
    >"${STUDIO_DIR}/propagate_llm.log" 2>&1 || true ) &

exec supervisord -c /etc/supervisor/supervisord.conf
