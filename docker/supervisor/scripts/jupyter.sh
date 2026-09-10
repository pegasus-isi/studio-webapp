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

# --ServerApp.token is empty unless the session token gate is on. An empty
# token is safe only while nothing else shares this network namespace; on a
# shared host it means any local user reaching this port gets terminals as us.
# With the gate on, the entrypoint mints a real token and the proxy injects it
# as an Authorization header, so the browser flow is unchanged.
#
# Keep every argument below on its own `\`-continued line with NO comments
# between them: a comment line inside a continuation ends the command, and the
# arguments after it become commands of their own. That fails silently — the
# server starts, just without the flags you thought you passed.
exec jupyter lab \
    --no-browser \
    --ip=127.0.0.1 \
    --port="${STUDIO_JUPYTER_PORT:-8889}" \
    --ServerApp.base_url="${BASE_URL}" \
    --ServerApp.root_dir="${ROOT}" \
    --ServerApp.token="${STUDIO_JUPYTER_TOKEN:-}"
