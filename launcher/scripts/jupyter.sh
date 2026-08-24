#!/bin/bash
# Start JupyterLab rooted at the user's HOME.
#
# It used to be rooted at ~/work, which hid everything else the user owns: the
# generated tool config (CLAUDE.md, AGENTS.md) now lives at $HOME, workflow runs
# are discovered by scanning all of $HOME, and workflow projects are not
# required to sit under ~/work. A browser that could not see above ~/work made
# those files look absent.
#
# HOME is set by KubeSpawner; fall back to /tmp if unset.
set -euo pipefail

ROOT="${HOME:-/tmp}"
# Still created: it remains the default home for workflow projects and holds
# .studio/studio.db.
mkdir -p "${ROOT}/work"
cd "$ROOT"

BASE_URL="${STUDIO_JUPYTER_BASE_URL:-/jupyter/}"
if [ -n "${JUPYTERHUB_SERVICE_PREFIX:-}" ]; then
    BASE_URL="${JUPYTERHUB_SERVICE_PREFIX%/}/jupyter/"
fi

# Port comes from the launcher (allocated per session on shared native hosts
# where a fixed 8889 is host-global and collides between users — issue #216);
# routers/jupyter.py reads the same env var to probe the right listener.
# Empty token in the container (the pod network namespace is the boundary);
# a real launcher-generated token on gated native sessions — an empty token
# on a shared host hands terminals to any local user who can reach the port.
# The rendered proxy injects it as an Authorization header, so the browser
# flow is unchanged either way.
exec jupyter lab \
    --no-browser \
    --ip=127.0.0.1 \
    --port="${STUDIO_JUPYTER_PORT:-8889}" \
    --ServerApp.base_url="${BASE_URL}" \
    --ServerApp.root_dir="${ROOT}" \
    --ServerApp.token="${STUDIO_JUPYTER_TOKEN:-}"
