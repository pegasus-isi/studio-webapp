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
    ${PYTHON} init_db.py

    if [ -f "/opt/pegasus-ai/knowledge/references/PEGASUS_AI.md" ]; then
        cp /opt/pegasus-ai/knowledge/references/PEGASUS_AI.md "${WORK}/AGENTS.md"
    fi

    touch "${MARKER}"
    echo "[studio] Initialization complete."
fi

# Propagate saved LLM config to installed tool adapters. Run in the background
# with a hard timeout so it can NEVER gate supervisord startup: this touches the
# NFS-backed SQLite DB, and a blocked write here would otherwise stall the
# notebook server past JupyterHub's 30s spawn timeout. Non-fatal; logged for
# inspection rather than discarded.
( cd /opt/studio-api && timeout 20 "${PYTHON}" propagate_llm.py \
    >"${STUDIO_DIR}/propagate_llm.log" 2>&1 || true ) &

exec supervisord -c /etc/supervisor/supervisord.conf
