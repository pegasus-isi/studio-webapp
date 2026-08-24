#!/usr/bin/env bash
# PegasusAI Studio container entrypoint — a thin wrapper only.
#
# All start logic (prepare $HOME state, render configs, exec supervisord) lives
# in the shared launcher so the container and the native Open OnDemand
# packaging cannot drift (docs/OOD-DEPLOYMENT-DESIGN.md §1). Do not add steps
# here: anything added container-only is a step the native deployment silently
# loses. Add it to launcher/studio-launch instead.
#
# The launcher's defaults ARE the container contract (port 8888, /opt/studio-*
# paths, /opt/pegasus-ai/marketplace seed), so no env is set here.

set -euo pipefail

exec /opt/pegasus-ai/launcher/studio-launch "$@"
