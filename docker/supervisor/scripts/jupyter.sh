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

# Everything that is not environment-derived (bind address, port, auth, XSRF,
# origin policy) lives in /etc/jupyter/jupyter_server_config.py — the single
# source of truth; studio-api/tests/test_jupyter_config.py checks that no
# security flag creeps back onto this command line. Only the two per-user
# paths are passed here.
exec jupyter lab \
    --ServerApp.base_url="${BASE_URL}" \
    --ServerApp.root_dir="${ROOT}"
