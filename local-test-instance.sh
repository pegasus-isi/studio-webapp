#!/bin/bash
# Start a local test instance of pegasus-ai-studio using the current user's UID/GID.

set -euo pipefail

IMAGE="${IMAGE:-pegasus/studio-webapp:latest}"
PORT="${PORT:-8888}"

TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT

echo "Starting $IMAGE as uid=$(id -u) gid=$(id -g)"
echo "Home dir (tmp): $TMPDIR -> /home/$USER"
echo "Open http://localhost:${PORT} when ready"
echo ""

docker run --rm -it \
    --platform linux/amd64 \
    -u "$(id -u):$(id -g)" \
    -e HOME="/home/$USER" \
    -e USER="$USER" \
    -p "${PORT}:8888" \
    -v "$TMPDIR:/home/$USER" \
    "$IMAGE"
