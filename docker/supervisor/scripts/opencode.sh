#!/bin/bash
# Start the OpenCode agent server that backs the chat panel (#367).
#
# The chat panel is a client of this process: studio-api proxies
# /api/opencode/* to it verbatim and the browser talks to nobody else. It binds
# loopback only — it has the user's full file and shell authority, so the one
# door to it is the proxy, which the upstream ingress has already
# authenticated.
#
# Sessions, history and compaction live in OpenCode's own SQLite database under
# the user's XDG data dir (the NFS home in production), so they survive a pod
# restart the same way ~/.studio/studio.db does.
#
# Password: the entrypoint writes one per user and exports it. Read it from the
# file too, so this still works when supervisord was started without that env
# (local dev, a hand-run supervisord). Refusing to start without a password is
# deliberate — an unauthenticated agent server on loopback is reachable by
# anything else in the pod, including a workflow job.
set -euo pipefail

ROOT="${HOME:-/tmp}"
PW_FILE="${ROOT}/.studio/opencode-password"

if [ -z "${OPENCODE_SERVER_PASSWORD:-}" ] && [ -r "${PW_FILE}" ]; then
    OPENCODE_SERVER_PASSWORD="$(cat "${PW_FILE}")"
    export OPENCODE_SERVER_PASSWORD
fi

if [ -z "${OPENCODE_SERVER_PASSWORD:-}" ]; then
    echo "[opencode] FATAL: no OPENCODE_SERVER_PASSWORD and no ${PW_FILE} —" \
         "refusing to serve without authentication" >&2
    exit 1
fi

# Rooted at $HOME, matching JupyterLab: workflow projects are not required to
# live under ~/work, and a session's own directory is set per session by the
# chat's workflow picker anyway. This is only the default for a session created
# without one.
mkdir -p "${ROOT}/work"
cd "$ROOT"

# The npm prefix that `npm install -g` writes to for this user comes first, so
# a user-upgraded opencode wins over the image-baked one — same precedence the
# interactive shells get from /etc/profile.d/10-studio-tools.sh.
export PATH="${HOME}/.npm-global/bin:${HOME}/.local/bin:/opt/npm-global/bin:${PATH}"

exec opencode serve \
    --hostname 127.0.0.1 \
    --port "${STUDIO_OPENCODE_PORT:-4096}" \
    --print-logs \
    --log-level "${STUDIO_OPENCODE_LOG_LEVEL:-INFO}"
