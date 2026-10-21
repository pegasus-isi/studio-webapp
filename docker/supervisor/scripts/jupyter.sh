#!/bin/bash
# Start JupyterLab rooted at the user's HOME.
#
# It used to be rooted at ~/work, which hid everything else the user owns: the
# generated tool config (CLAUDE.md, AGENTS.md) lives at $HOME, and workflow runs
# are discovered by scanning all of $HOME. A browser that could not see above
# ~/work made those files look absent. Since #401 the workspace is $HOME too,
# so this is now the same directory the file panel and the agent tools show.
#
# HOME is set by KubeSpawner; fall back to /tmp if unset.
set -euo pipefail

ROOT="${HOME:-/tmp}"
cd "$ROOT"

BASE_URL="${STUDIO_JUPYTER_BASE_URL:-/jupyter/}"
if [ -n "${JUPYTERHUB_SERVICE_PREFIX:-}" ]; then
    BASE_URL="${JUPYTERHUB_SERVICE_PREFIX%/}/jupyter/"
elif [ -n "${STUDIO_URL_PREFIX:-}" ]; then
    # Open OnDemand's equivalent of JUPYTERHUB_SERVICE_PREFIX above: nginx's
    # prefix-proxy.conf.tmpl jupyter location passes the full request path
    # through unmodified (no URI on its proxy_pass), so this server has to be
    # told the same prefix or every asset link it renders 404s one level up.
    BASE_URL="${STUDIO_URL_PREFIX%/}/jupyter/"
fi

# The token is NOT passed here. It used to be (--ServerApp.token=...), and a
# command line is readable by every user on the host through /proc/<pid>/
# cmdline and `ps`. On Open OnDemand that host is shared and Apptainer gives
# this server the host network, so a local user who read the token could open
# terminals as us on 127.0.0.1:<port>, past nginx. jupyter_server_config.py
# reads it from STUDIO_JUPYTER_TOKEN instead: a process's environment is
# readable only by its owner.
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
    --ServerApp.root_dir="${ROOT}"
