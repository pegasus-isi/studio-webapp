#!/bin/sh
# Make the packaged Pegasus read /etc/pegasus/pegasus.properties.
#
# Run once at image build, after Pegasus is installed (Dockerfile and
# ood-app/studio-el9.def).
#
# Why: Pegasus 6.0's native packages install into a venv, and pegasus-plan
# hands the Java planner `-Dpegasus.home.sysconfdir=` from
# Pegasus.cli._java.get_system_properties(), which is always
# <venv>/.../Pegasus/data/etc and never /etc/pegasus. The planner reads
# pegasus.properties only from sysconfdir, so the system-wide settings in
# /etc/pegasus (JSON monitord encoding, the AMQP workflow catalog) were
# ignored without any error. The `base_dir == "/usr"` check that the
# 2026-11-11 package added to pegasus-config.py never matches, because base_dir
# is /usr/lib/pegasus/venv, and pegasus-plan doesn't use that code anyway.
#
# This asks the planner's own code where it looks, rather than hardcoding
# python3.X, and symlinks the file there. When Pegasus is fixed and the answer
# becomes /etc/pegasus, it does nothing, so it can stay in place until it is
# removed. A symlink keeps one copy: an edit to /etc/pegasus applies to both.
#
# Limit: this covers planning in this image only. Hosted Studio plans on the
# HTCondor access point (services/remote_exec.py), whose Pegasus install is
# salt-managed in studio-system and needs the same workaround there.
#
# Overrides, for tests: PEGASUS_PLAN, PEGASUS_SYSTEM_PROPERTIES.
set -eu

src="${PEGASUS_SYSTEM_PROPERTIES:-/etc/pegasus/pegasus.properties}"
plan="${PEGASUS_PLAN:-$(command -v pegasus-plan || true)}"

if [ ! -f "$src" ]; then
    echo "[pegasus] $src not found; nothing to link" >&2
    exit 1
fi
if [ -z "$plan" ] || [ ! -f "$plan" ]; then
    echo "[pegasus] pegasus-plan not found; install Pegasus first" >&2
    exit 1
fi

# pegasus-plan is a console script; its shebang is the venv's interpreter, the
# one that imports the Pegasus the planner runs.
py="$(head -n 1 "$plan" | sed 's/^#![[:space:]]*//')"

if ! conf="$("$py" -c 'from Pegasus.cli._java import get_system_properties as g; print(g()["pegasus.home.sysconfdir"])')"; then
    # The API moved, which most likely means Pegasus was restructured (and
    # perhaps fixed). Say so loudly, but don't fail the image over a workaround.
    echo "[pegasus] WARNING: could not ask the planner for its sysconfdir;" \
         "$src is NOT linked and may not be read" >&2
    exit 0
fi
conf="${conf%/}"

if [ "$conf" = "$(dirname "$src")" ]; then
    echo "[pegasus] planner reads $conf already; no link needed"
    exit 0
fi

dest="$conf/pegasus.properties"
if [ -e "$dest" ] && [ ! -L "$dest" ]; then
    # The package started shipping its own file there; don't overwrite it.
    echo "[pegasus] WARNING: $dest exists and is not ours; leaving it," \
         "so $src may not be read" >&2
    exit 0
fi

mkdir -p "$conf"
ln -sfn "$src" "$dest"
echo "[pegasus] linked $dest -> $src"
