#!/bin/bash
# Start JupyterLab from the user's work directory.
# HOME is set by KubeSpawner; fall back to /tmp if unset.
set -euo pipefail

WORK="${HOME:-/tmp}/work"
mkdir -p "$WORK"
cd "$WORK"

BASE_URL="${STUDIO_JUPYTER_BASE_URL:-/jupyter/}"
if [ -n "${JUPYTERHUB_SERVICE_PREFIX:-}" ]; then
    BASE_URL="${JUPYTERHUB_SERVICE_PREFIX%/}/jupyter/"
fi

exec jupyter lab \
    --no-browser \
    --ip=127.0.0.1 \
    --port=8889 \
    --ServerApp.base_url="${BASE_URL}" \
    --ServerApp.token=""
