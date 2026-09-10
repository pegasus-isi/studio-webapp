#!/usr/bin/env bash
# PegasusAI Studio container entrypoint.
#
# First-launch: initialize DB, seed config, prepare workspace.
# Then delegates to supervisord for process supervision.
#
# Runs as whatever UID KubeSpawner injects — no root assumed.

set -euo pipefail

WORK="${HOME:-/tmp}/work"
# Studio's own state lives beside the user's other config, not inside the
# directory they keep workflows in: a `rm -rf` in the workspace should not be
# able to take the settings database with it. init_db.py migrates an existing
# database from the old location on first start.
STUDIO_DIR="${HOME:-/tmp}/.studio"
MARKER="${STUDIO_DIR}/.initialized"

PYTHON="$(which python3 2>/dev/null || which python 2>/dev/null || echo python3)"

# ---- Ports and the per-session runtime directory ----
#
# Every listener is configurable because the container is no longer only run
# one-per-pod. Under Open OnDemand the same image runs on a shared host — and
# with Apptainer, which by default gives the container the *host* network and
# mount namespaces, so two users' sessions bind the same ports on the same
# machine and the second one loses. Ports:
#
#   STUDIO_PORT           front proxy, the only one anything connects to (8888)
#   STUDIO_API_PORT       uvicorn                                        (8080)
#   STUDIO_WEB_PORT       Next.js standalone                             (3000)
#   STUDIO_JUPYTER_PORT   JupyterLab                                     (8889)
#   STUDIO_OPENCODE_PORT  opencode serve                                 (4096)
#
# STUDIO_ALLOC_PORTS=1 picks a free port for each of the four internal
# listeners that was not set explicitly. The front port is deliberately NOT
# auto-allocated: whoever publishes it (OOD's own allocator, a -p mapping, the
# KubeSpawner service) has to know it in advance, so guessing here would
# produce a port nothing routes to.
STUDIO_PORT="${STUDIO_PORT:-8888}"
if [ "${STUDIO_ALLOC_PORTS:-0}" = "1" ]; then
    # One python invocation for all four: separate calls would each close their
    # socket before the next bound, and the kernel is free to hand out the same
    # port twice. Held open together, then released at once — still a race
    # against the rest of the host, which is why the services themselves fail
    # loudly on a bind conflict rather than trusting this.
    read -r ALLOC_API ALLOC_WEB ALLOC_JUPYTER ALLOC_OPENCODE <<EOF
$("${PYTHON}" - <<'PYEOF'
import socket
socks = []
for _ in range(4):
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    socks.append(s)
print(" ".join(str(s.getsockname()[1]) for s in socks))
for s in socks:
    s.close()
PYEOF
)
EOF
    STUDIO_API_PORT="${STUDIO_API_PORT:-${ALLOC_API}}"
    STUDIO_WEB_PORT="${STUDIO_WEB_PORT:-${ALLOC_WEB}}"
    STUDIO_JUPYTER_PORT="${STUDIO_JUPYTER_PORT:-${ALLOC_JUPYTER}}"
    STUDIO_OPENCODE_PORT="${STUDIO_OPENCODE_PORT:-${ALLOC_OPENCODE}}"
fi
STUDIO_API_PORT="${STUDIO_API_PORT:-8080}"
STUDIO_WEB_PORT="${STUDIO_WEB_PORT:-3000}"
STUDIO_JUPYTER_PORT="${STUDIO_JUPYTER_PORT:-8889}"
STUDIO_OPENCODE_PORT="${STUDIO_OPENCODE_PORT:-4096}"
# Loopback by default (#229). Nothing outside the container reaches these
# ports: nginx proxies to 127.0.0.1 for every backend, and its own `listen` has
# no address, so it still answers on all interfaces and KubeSpawner is
# unaffected. JupyterLab and opencode already bind loopback via their own
# flags, so this only ever governed uvicorn and Next.js — and there 0.0.0.0 put
# studio-api on the pod's cluster IP, reachable by any pod on a flat network
# with no NetworkPolicy. Same reasoning services/installer.py already applies
# to `opencode web`, and on a shared host it is not the cluster network but
# every other local user.
#
# A deployment that genuinely needs a backend reachable directly — a sidecar,
# a probe that does not go through nginx — sets 0.0.0.0 explicitly.
STUDIO_BIND_ADDR="${STUDIO_BIND_ADDR:-127.0.0.1}"

# ---- Session token gate (#218) ----
#
# Unset (the K8s and plain-container default): no gate at all, and every
# fragment below renders to a comment. The pod owns its network namespace, so
# the front port is the only way in and the gateway already authenticated it.
#
# Set (an Open OnDemand session, whose before.sh.erb generates it the same way
# it generates any batch-connect password): Studio is running on a machine
# other people are logged into, and it has no auth of its own. Four things turn
# on together, because any one alone is a gate with a door beside it:
#
#   1. the proxy 403s everything without the session cookie, and only
#      GET /auth?token=... sets it;
#   2. the internal services bind loopback (already the default);
#   3. JupyterLab gets a real token — an empty one means any local user
#      reaching its port gets terminals as this user — injected by the proxy so
#      the browser flow is unchanged;
#   4. studio-api requires the proxy-stamped X-Studio-Internal secret, because
#      loopback is not a boundary between local users.
STUDIO_AUTH_TOKEN="${STUDIO_AUTH_TOKEN:-}"
# Only used by the gate's /auth location today. Full prefix rendering is a
# separate piece of work; empty here means /auth sits at the root, which is
# what the container and OOD's own path-stripping proxy both give us.
STUDIO_URL_PREFIX="${STUDIO_URL_PREFIX:-}"
STUDIO_URL_PREFIX="${STUDIO_URL_PREFIX%/}"
if [ -n "${STUDIO_AUTH_TOKEN}" ]; then
    # The token lands in a sed replacement, an nginx regex, an nginx string
    # literal and a cookie value. Constrain the charset so it cannot break out
    # of any of them — a token containing a quote would produce a config that
    # either fails to parse or, worse, parses into something else.
    case "${STUDIO_AUTH_TOKEN}" in
        *[!A-Za-z0-9_-]*)
            echo "[studio] FATAL: STUDIO_AUTH_TOKEN may only contain" \
                 "[A-Za-z0-9_-]" >&2
            exit 1
            ;;
    esac
    if [ -n "${STUDIO_URL_PREFIX}" ]; then
        case "${STUDIO_URL_PREFIX}" in
            /*) ;;
            *)
                echo "[studio] FATAL: STUDIO_URL_PREFIX must start with '/'" \
                     "(got '${STUDIO_URL_PREFIX}')" >&2
                exit 1
                ;;
        esac
    fi
    STUDIO_JUPYTER_TOKEN="${STUDIO_JUPYTER_TOKEN:-$("${PYTHON}" -c 'import secrets; print(secrets.token_urlsafe(32))')}"
    STUDIO_INTERNAL_SECRET="${STUDIO_INTERNAL_SECRET:-$("${PYTHON}" -c 'import secrets; print(secrets.token_urlsafe(32))')}"
    echo "[studio] Session token gate ENABLED"
else
    STUDIO_JUPYTER_TOKEN="${STUDIO_JUPYTER_TOKEN:-}"
    STUDIO_INTERNAL_SECRET="${STUDIO_INTERNAL_SECRET:-}"
fi
# jupyter.sh reads the token; security.InternalSecretMiddleware reads the
# secret. Both are supervisord children, so the environment is the channel.
export STUDIO_AUTH_TOKEN STUDIO_URL_PREFIX STUDIO_JUPYTER_TOKEN \
       STUDIO_INTERNAL_SECRET

# Read by supervisord's %(ENV_...)s expansion, by jupyter.sh and the Jupyter
# server config, by routers/jupyter.py (status), knowledge/opencode.py (the MCP
# URL it writes into opencode.json) and services/opencode_server.py.
export STUDIO_PORT STUDIO_API_PORT STUDIO_WEB_PORT STUDIO_JUPYTER_PORT \
       STUDIO_OPENCODE_PORT STUDIO_BIND_ADDR

# Not under $HOME: in K8s the home is NFS-backed and a unix domain socket
# (supervisor.sock) cannot live on NFS. /tmp is local in both models, and the
# uid suffix is what keeps two users on one shared host out of each other's
# runtime state — pid files, the supervisor socket, nginx temp dirs and the
# rendered configs all live here.
STUDIO_RUNTIME_DIR="${STUDIO_RUNTIME_DIR:-/tmp/studio-runtime-$(id -u)}"
export STUDIO_RUNTIME_DIR
mkdir -p "${STUDIO_RUNTIME_DIR}"
# The supervisor socket is chmod 0700 by supervisord, but the directory around
# it is ours to lock down: on a shared host /tmp is world-readable, and the
# rendered configs name every port this session is using.
chmod 700 "${STUDIO_RUNTIME_DIR}" 2>/dev/null || true

# Nginx temp dirs must exist before nginx starts (non-root can't create them
# under /var/lib/nginx, so the rendered config points them at the runtime dir).
mkdir -p "${STUDIO_RUNTIME_DIR}/nginx-client-body" \
         "${STUDIO_RUNTIME_DIR}/nginx-proxy" \
         "${STUDIO_RUNTIME_DIR}/nginx-fastcgi" \
         "${STUDIO_RUNTIME_DIR}/nginx-uwsgi" \
         "${STUDIO_RUNTIME_DIR}/nginx-scgi"

# ---- Render the nginx configs ----
# Templates rather than baked configs because /etc is not writable: the
# container runs as whatever uid KubeSpawner (or the OOD job) injects. nginx is
# started with `-c` against the rendered copy (see supervisord.conf).
render_template() {
    src="$1"; dst="$2"
    # Gate fragments carry directives only when the gate is on. The includes
    # that load them are unconditional — an include of a missing file is a
    # startup failure — so the file must always exist, empty of effect.
    case "$(basename "${src}")" in
        gate-*.conf.tmpl)
            if [ -z "${STUDIO_AUTH_TOKEN}" ]; then
                printf '# STUDIO_AUTH_TOKEN unset — session token gate off.\n' \
                    > "${dst}"
                return 0
            fi
            ;;
    esac
    sed -e "s|@STUDIO_PORT@|${STUDIO_PORT}|g" \
        -e "s|@STUDIO_AUTH_TOKEN@|${STUDIO_AUTH_TOKEN}|g" \
        -e "s|@STUDIO_URL_PREFIX@|${STUDIO_URL_PREFIX}|g" \
        -e "s|@STUDIO_JUPYTER_TOKEN@|${STUDIO_JUPYTER_TOKEN}|g" \
        -e "s|@STUDIO_INTERNAL_SECRET@|${STUDIO_INTERNAL_SECRET}|g" \
        -e "s|@STUDIO_API_PORT@|${STUDIO_API_PORT}|g" \
        -e "s|@STUDIO_WEB_PORT@|${STUDIO_WEB_PORT}|g" \
        -e "s|@STUDIO_JUPYTER_PORT@|${STUDIO_JUPYTER_PORT}|g" \
        -e "s|@STUDIO_OPENCODE_PORT@|${STUDIO_OPENCODE_PORT}|g" \
        -e "s|@STUDIO_RUNTIME_DIR@|${STUDIO_RUNTIME_DIR}|g" \
        "${src}" > "${dst}"
    # A placeholder left behind means a template gained one that this function
    # does not substitute. nginx would fail on the literal with a parse error
    # naming a line, not a cause, so say it here instead.
    if grep -q '@STUDIO_[A-Z_]*@' "${dst}"; then
        echo "[studio] FATAL: unsubstituted placeholder in ${dst}:" >&2
        grep -n '@STUDIO_[A-Z_]*@' "${dst}" >&2
        exit 1
    fi
}
render_template /opt/pegasus-ai/templates/nginx.conf.tmpl \
                "${STUDIO_RUNTIME_DIR}/nginx.conf"
render_template /opt/pegasus-ai/templates/studio-proxy.conf.tmpl \
                "${STUDIO_RUNTIME_DIR}/studio-proxy.conf"
for gate in gate-map gate-server gate-api-headers gate-jupyter-headers; do
    render_template "/opt/pegasus-ai/templates/${gate}.conf.tmpl" \
                    "${STUDIO_RUNTIME_DIR}/${gate}.conf"
done
# The rendered fragments hold this session's token, Jupyter token and internal
# secret in cleartext. The runtime dir is already 0700; be explicit about the
# files too, since /tmp on a shared host is world-readable.
chmod 600 "${STUDIO_RUNTIME_DIR}"/gate-*.conf 2>/dev/null || true
echo "[studio] Ports: front=${STUDIO_PORT} api=${STUDIO_API_PORT}" \
     "web=${STUDIO_WEB_PORT} jupyter=${STUDIO_JUPYTER_PORT}" \
     "opencode=${STUDIO_OPENCODE_PORT} bind=${STUDIO_BIND_ADDR}"

# Render and stop. The image build uses this to run `nginx -t` over the real
# templates (see Dockerfile), so a template that does not parse fails the build
# rather than the first session to start. Deliberately before anything that
# touches $HOME: at build time that is root's home, and this must leave nothing
# behind there.
if [ "${1:-}" = "--render-only" ]; then
    echo "[studio] Rendered into ${STUDIO_RUNTIME_DIR}; --render-only, stopping."
    exit 0
fi

# workflows/ is where the Marketplace "Add" clones into and where the scaffold
# skills are told to put projects, so it should exist before anyone looks.
mkdir -p "${WORK}" "${STUDIO_DIR}" "${WORK}/workflows"

# ---- Marketplace checkout (skills, agents, templates) ----
# The image ships a pinned copy at /opt/pegasus-ai/marketplace. It is a template
# to copy from, not the tree tools read: it is root-owned and is replaced
# wholesale on every image rebuild.
#
# (Not to be confused with the Claude Code *plugin seed directory* written by
# knowledge/seed.py — that one points Claude Code at whichever checkout wins.)
#
# Clone it into the user's own $HOME on first launch: there it is writable, so
# skills can be updated with `git pull` or rolled back with `git checkout` —
# no root, no rebuild — and it persists across pod restarts, which a change
# under /opt would not.
#
# Cloning from the local seed rather than GitHub keeps first launch fast and
# working with no egress. origin is then repointed upstream so `git pull` does
# the obvious thing. Non-fatal throughout: layout.py falls back to the seed.
IMAGE_MARKETPLACE="/opt/pegasus-ai/marketplace"
USER_MARKETPLACE="${XDG_DATA_HOME:-${HOME:-/tmp}/.local/share}/pegasus-ai/marketplace"
if [ ! -d "${USER_MARKETPLACE}/.claude-plugin" ] \
   && [ -d "${IMAGE_MARKETPLACE}/.claude-plugin" ]; then
    CLONE_ERR=""
    if mkdir -p "$(dirname "${USER_MARKETPLACE}")" \
       && CLONE_ERR="$(git clone --quiet "${IMAGE_MARKETPLACE}" "${USER_MARKETPLACE}" 2>&1)"; then
        git -C "${USER_MARKETPLACE}" remote set-url origin \
            "${KNOWLEDGE_REPO_URL:-https://github.com/pegasus-isi/claude-plugin-marketplace.git}" \
            2>/dev/null || true
        echo "[studio] Marketplace checked out to ${USER_MARKETPLACE}"
    else
        # Print git's own message: a bare "could not create" sent one
        # investigation looking at NFS permissions when the cause was git
        # refusing the source repository's ownership.
        echo "[studio] WARNING: could not create ${USER_MARKETPLACE} —" \
             "falling back to the read-only ${IMAGE_MARKETPLACE}: ${CLONE_ERR}" >&2
    fi
fi

# The checkout is kept current by studio-api at startup, not here. Pulling from
# the entrypoint raced the tools that read the result: CLI tools load skills
# once when they start and never reload, so whether the assistant got today's
# skills came down to which background job finished first, with nothing in any
# log to say which way it went. studio-api owns it now and re-propagates when
# the checkout actually moves.

# What the tools will actually read, for the AGENTS.md seed below and the log.
if [ -d "${USER_MARKETPLACE}/plugins/pegasus-ai" ]; then
    RESOLVED_KNOWLEDGE="${USER_MARKETPLACE}/plugins/pegasus-ai"
else
    RESOLVED_KNOWLEDGE="${IMAGE_MARKETPLACE}/plugins/pegasus-ai"
fi

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

    touch "${MARKER}"
    echo "[studio] Initialization complete."
fi

# AGENTS.md: the Studio environment overlay followed by the portable Pegasus
# guide, matching what the knowledge adapters generate.
#
# Written on every start when absent, not only on first launch. The adapters
# only create it during install(), and startup runs update_llm_config(), so a
# workspace that lost the file — a user clearing it out, say — otherwise never
# got it back and the tools ran with no Pegasus context at all.
# Written at $HOME, not $HOME/work: every tool looks in the directory it starts
# in and then upwards, and terminals start at $HOME, so a copy one directory
# down existed without being found.
CONFIG_ROOT="${HOME:-/tmp}"
OVERLAY="/opt/pegasus-ai/overlay/STUDIO_ENV.md"
GUIDE="${KNOWLEDGE_ROOT:-${RESOLVED_KNOWLEDGE}}/references/PEGASUS.md"
if [ ! -f "${CONFIG_ROOT}/AGENTS.md" ] && { [ -f "${OVERLAY}" ] || [ -f "${GUIDE}" ]; }; then
    # Written with explicit ifs, not `[ -f x ] && cat x`: under `set -e` a
    # failing test makes the whole && chain the script's last status and
    # kills the entrypoint before supervisord ever starts.
    : > "${CONFIG_ROOT}/AGENTS.md"
    if [ -f "${OVERLAY}" ]; then
        cat "${OVERLAY}" >> "${CONFIG_ROOT}/AGENTS.md"
        printf '\n' >> "${CONFIG_ROOT}/AGENTS.md"
    fi
    if [ -f "${GUIDE}" ]; then
        cat "${GUIDE}" >> "${CONFIG_ROOT}/AGENTS.md"
    fi
    echo "[studio] Wrote ${CONFIG_ROOT}/AGENTS.md"
elif [ ! -f "${CONFIG_ROOT}/AGENTS.md" ]; then
    echo "[studio] WARNING: no knowledge tree found — AGENTS.md not written" >&2
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

# ---- OpenCode server password ----
# The chat panel is a client of `opencode serve` (#367), which runs on loopback
# with the user's full file and shell authority. Basic auth is the only thing
# between it and anything else running in the pod (a workflow job, say), so a
# password is generated per user rather than shared or omitted.
#
# Written to a 0600 file under $STUDIO_DIR — not minted per boot — so a restart
# does not invalidate the credential studio-api and the wrapper script are
# both reading, and exported so supervisord's children inherit it.
#
# python3 rather than openssl: openssl is not guaranteed in this image, and a
# missing binary here would take the whole entrypoint down under `set -e`.
OPENCODE_PW_FILE="${STUDIO_DIR}/opencode-password"
if [ ! -s "${OPENCODE_PW_FILE}" ]; then
    # Per-process temp name, not a fixed ".tmp": the home directory is NFS and
    # shared, so two pods first-booting at once would write the same path and
    # the losing rename would leave studio-api reading a password the engine
    # never saw — a 401 nothing in the UI can clear.
    OPENCODE_PW_TMP="${OPENCODE_PW_FILE}.$$"
    if ( umask 077 && "${PYTHON}" -c 'import secrets,sys; sys.stdout.write(secrets.token_urlsafe(32))' \
         > "${OPENCODE_PW_TMP}" ) 2>/dev/null; then
        chmod 600 "${OPENCODE_PW_TMP}"
        mv "${OPENCODE_PW_TMP}" "${OPENCODE_PW_FILE}"
        echo "[studio] Generated OpenCode server password at ${OPENCODE_PW_FILE}"
    else
        rm -f "${OPENCODE_PW_TMP}"
        echo "[studio] WARNING: could not generate an OpenCode server password —" \
             "the chat engine will not start" >&2
    fi
fi
if [ -s "${OPENCODE_PW_FILE}" ]; then
    chmod 600 "${OPENCODE_PW_FILE}" 2>/dev/null || true
    OPENCODE_SERVER_PASSWORD="$(cat "${OPENCODE_PW_FILE}")"
    export OPENCODE_SERVER_PASSWORD
fi

# Propagate saved LLM config to installed tool adapters. Run in the background
# with a hard timeout so it can NEVER gate supervisord startup: this touches the
# NFS-backed SQLite DB, and a blocked write here would otherwise stall the
# notebook server past JupyterHub's 30s spawn timeout. Non-fatal; logged for
# inspection rather than discarded.
( cd /opt/studio-api && timeout 20 "${PYTHON}" propagate_llm.py \
    >"${STUDIO_DIR}/propagate_llm.log" 2>&1 || true ) &

exec supervisord -c /etc/supervisor/supervisord.conf
