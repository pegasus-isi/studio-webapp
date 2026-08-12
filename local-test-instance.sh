#!/bin/bash
# Start a local test instance of pegasus-ai-studio using the current user's UID/GID.
#
# All knobs are optional environment variables:
#
#   IMAGE                 Image to run (default: pegasus/studio-webapp:latest)
#   PORT                  Host port mapped to nginx :8888 (default: 8888)
#   STUDIO_HOME           Persist the container home here instead of a throwaway
#                         tmp dir — keeps the SQLite state, tool configs, and
#                         cloned workflows across runs.
#
#   Point Studio's HTCondor client at a different AP / submit host WITHOUT
#   rebuilding (HTCondor reads _CONDOR_<KNOB> env vars, which override the baked
#   /etc/condor/config.d/90-studio.conf):
#   CONDOR_HOST           AP/central-manager host (sets CONDOR_HOST + SCHEDD_HOST)
#   CONDOR_SCHEDD_HOST    schedd host if different (default: CONDOR_HOST)
#   CONDOR_TOKENS_DIR     host dir of HTCondor IDTOKENs, mounted read-only and
#                         used as the token directory. REQUIRED to actually
#                         authenticate to a real pool — a host override alone
#                         does not authenticate.
#   CONDOR_CONFIG_DROPIN  host path to a condor config file, mounted as
#                         /etc/condor/config.d/99-local.conf (wins over the
#                         baked file; alternative to the CONDOR_* knobs above).
#
#   PRIVILEGED=1          run --privileged (needed for local Apptainer workflow
#                         execution; not needed just to reach a remote AP).
#   EXTRA_DOCKER_ARGS     extra args appended verbatim to `docker run`.
#   DRY_RUN=1             print the docker command instead of running it.
#
# Example — drive a local build against a real submit host:
#   CONDOR_HOST=my-ap.example.org \
#   CONDOR_TOKENS_DIR="$HOME/.condor/tokens.d" \
#   STUDIO_HOME=/tmp/studio-home \
#   ./local-test-instance.sh

set -euo pipefail

IMAGE="${IMAGE:-pegasus/studio-webapp:latest}"
PORT="${PORT:-8888}"
# $USER isn't always exported (e.g. some CI/cron shells); fall back to id.
USER="${USER:-$(id -un)}"

# Home: persistent if STUDIO_HOME is set, else a throwaway tmp dir wiped on exit.
if [ -n "${STUDIO_HOME:-}" ]; then
    HOME_DIR="$STUDIO_HOME"
    mkdir -p "$HOME_DIR"
    echo "Home dir (persistent): $HOME_DIR -> /home/$USER"
else
    HOME_DIR=$(mktemp -d)
    trap 'rm -rf "$HOME_DIR"' EXIT
    echo "Home dir (tmp, wiped on exit): $HOME_DIR -> /home/$USER"
fi

ARGS=(
    --rm -it
    --platform linux/amd64
    -u "$(id -u):$(id -g)"
    -e "HOME=/home/$USER"
    -e "USER=$USER"
    -p "${PORT}:8888"
    -v "$HOME_DIR:/home/$USER"
)

# --- HTCondor: connect to a different AP / submit host ---
if [ -n "${CONDOR_HOST:-}" ]; then
    ARGS+=(-e "_CONDOR_CONDOR_HOST=$CONDOR_HOST")
    ARGS+=(-e "_CONDOR_SCHEDD_HOST=${CONDOR_SCHEDD_HOST:-$CONDOR_HOST}")
    echo "HTCondor host: $CONDOR_HOST (schedd: ${CONDOR_SCHEDD_HOST:-$CONDOR_HOST})"
elif [ -n "${CONDOR_SCHEDD_HOST:-}" ]; then
    ARGS+=(-e "_CONDOR_SCHEDD_HOST=$CONDOR_SCHEDD_HOST")
    echo "HTCondor schedd: $CONDOR_SCHEDD_HOST"
fi

if [ -n "${CONDOR_TOKENS_DIR:-}" ]; then
    ARGS+=(-v "$CONDOR_TOKENS_DIR:/home/$USER/.condor/tokens.d:ro")
    ARGS+=(-e "_CONDOR_SEC_TOKEN_DIRECTORY=/home/$USER/.condor/tokens.d")
    echo "HTCondor tokens: $CONDOR_TOKENS_DIR (read-only)"
else
    if [ -n "${CONDOR_HOST:-}" ]; then
        echo "WARNING: CONDOR_HOST set but no CONDOR_TOKENS_DIR — condor_q/" \
             "condor_status and submits will fail auth against a real pool." >&2
    fi
fi

if [ -n "${CONDOR_CONFIG_DROPIN:-}" ]; then
    ARGS+=(-v "$CONDOR_CONFIG_DROPIN:/etc/condor/config.d/99-local.conf:ro")
    echo "HTCondor config drop-in: $CONDOR_CONFIG_DROPIN"
fi

if [ "${PRIVILEGED:-0}" = "1" ]; then
    ARGS+=(--privileged)
    echo "Running --privileged (local Apptainer workflow execution enabled)"
fi

# Split EXTRA_DOCKER_ARGS on whitespace so callers can pass several flags.
if [ -n "${EXTRA_DOCKER_ARGS:-}" ]; then
    # shellcheck disable=SC2206
    ARGS+=(${EXTRA_DOCKER_ARGS})
fi

echo "Starting $IMAGE as uid=$(id -u) gid=$(id -g)"
echo "Open http://localhost:${PORT} when ready"
echo ""

if [ "${DRY_RUN:-0}" = "1" ]; then
    printf 'DRY_RUN — would execute:\ndocker run'
    printf ' %q' "${ARGS[@]}" "$IMAGE"
    printf '\n'
    exit 0
fi

docker run "${ARGS[@]}" "$IMAGE"
