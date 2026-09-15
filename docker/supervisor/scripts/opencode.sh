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

# Rooted at the workspace root, which is where the always-on knowledge tells
# every model to put a workflow — `workflows/<name>/`, relative to here, the
# only directory the Workflows tab scans.
#
# The engine joins a *relative* path onto the directory its session was created
# in, so this is what decides whether `workflows/<name>/workflow_generator.py` —
# the exact string the overlay's path table prints — means the right place.
# Root it anywhere else and the project lands somewhere invisible to the tab,
# with no Generate/Plan/Run button and nothing erroring. Measured with a small
# model on a one-step task, back when the two disagreed: four of its six files
# stranded (#400).
#
# The chat panel no longer overrides this per session either (see
# use-opencode-chat.ts), so one relative path means one place in every
# conversation — which is the only way the guidance can stay a single sentence.
WORKSPACE="${STUDIO_WORKSPACE_DIR:-${ROOT}}"
mkdir -p "${WORKSPACE}"
cd "${WORKSPACE}"

# The npm prefix that `npm install -g` writes to for this user comes first, so
# a user-upgraded opencode wins over the image-baked one — same precedence the
# interactive shells get from /etc/profile.d/10-studio-tools.sh.
export PATH="${ROOT}/.npm-global/bin:${ROOT}/.local/bin:/opt/npm-global/bin:${PATH}"

exec opencode serve \
    --hostname 127.0.0.1 \
    --port "${STUDIO_OPENCODE_PORT:-4096}" \
    --print-logs \
    --log-level "${STUDIO_OPENCODE_LOG_LEVEL:-INFO}"
