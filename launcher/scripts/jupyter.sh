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
# allow_remote_access: Jupyter refuses any request whose Host header it does
# not consider local. Behind Open OnDemand the session is proxied at
# /node/<host>/<port>/ and arrives with the node name as Host ("c1"), so
# JupyterLab returned a bare "403 : Forbidden" in the browser while its own
# API reported it healthy — the give-away is in Jupyter's log, not ours:
# "Blocking request with non-local 'Host' c1". Same request with
# Host: localhost returned 200 (#233).
#
# Turning the check off costs nothing here, and the reason matters: it guards
# a Jupyter that is directly reachable. Ours never is — it binds 127.0.0.1,
# its only client is this session's own proxy, and on a gated session that
# proxy must present a real token. The boundary is the loopback bind plus the
# session gate, not the Host string.
#
# Not fixed by rewriting Host to localhost in the proxy instead: Jupyter
# builds redirects from the Host it is given, which would trade the 403 for
# redirects pointing somewhere the browser cannot follow.
exec jupyter lab \
    --no-browser \
    --ip=127.0.0.1 \
    --port="${STUDIO_JUPYTER_PORT:-8889}" \
    --ServerApp.base_url="${BASE_URL}" \
    --ServerApp.root_dir="${ROOT}" \
    --ServerApp.allow_remote_access=True \
    --ServerApp.token="${STUDIO_JUPYTER_TOKEN:-}"
