# FABRIC Phase 0 review and main integration — 2026-09-10

Reviewed `feat/fabric-local-studio-phase0` at `f1cf09d` against `origin/main`
at `75ac6e7`. The feature branch had 14 unique commits and was missing 107
commits from main. The review focused on compute backends, workflow execution,
image building, monitoring, and their overlap with the incoming changes.

## Remaining findings

These issues already existed on the feature branch before this merge. They
are follow-up work; the merge does not resolve them.

1. **P1 — SSH does not verify the remote host.**
   `studio-api/services/backends/ssh.py:133` generates
   `UserKnownHostsFile /dev/null` and `StrictHostKeyChecking no` for all hosts,
   including a configured jump host. An impersonated server can receive project
   files, commands, and credentials sent to the execution host. Persist known
   host keys and reject changed keys; make any disposable-slice exception an
   explicit configuration choice.

2. **P1 — Planning can use stale generator output.**
   `studio-api/services/backends/ssh.py:311` pulls immediately only after
   `pegasus-plan`, `pegasus-run`, and `pegasus-remove`. A generator invoked as
   `python3` returns before its generated files are pulled. The guided flow
   then inspects the local YAML and pushes the project again
   (`studio-api/services/run_flow.py:972`). If the periodic pull has not run,
   a first generation can appear to have produced no YAML; a repeat generation
   can have its new remote YAML overwritten by the old mirror copy. Add an
   explicit synchronization barrier after generation, with a defined policy
   for concurrent local edits and periodic pulls.

3. **P2 — Notifications retain the old backend's scan root.**
   `studio-api/main.py:314` starts the notification engine before applying the
   persisted execution backend. Switching backends in
   `studio-api/routers/settings.py:492` also leaves that engine's root unchanged.
   The workflow list follows the selected backend, but completion/failure
   notifications can continue scanning the former root. Apply the selection
   before starting notifications and update the watch root when switching.

## Integration changes

- Resolved conflicts in the workflow router, guided run flow, and Studio shell.
  Preserved main's top bar and the branch's dynamic viewport height.
- Kept SSH planning on the selected backend; local Condor detection and
  compatibility checks apply to local execution. Preserved main's submit-host
  planning behavior for the local backend and the branch's monitor flags.
- Added environment overrides to backend execution so both Generate actions
  receive workflow secrets. SSH receives only the stored overrides, preserving
  the remote host's environment instead of forwarding the server's HOME/PATH
  and unrelated credentials.
- Installed the workflow-monitor adapter and pegasus-monitor overlay into
  Debian's isolated Pegasus virtual environment, with a matching CLI launcher.
- Based remote Apptainer fakeroot selection on the remote user's subuid/subgid
  ranges instead of the machine holding the mirror.

## Validation

- Before the merge: TypeScript passed; 505 backend tests passed and one
  loopback-port test was blocked by sandbox permissions.
- After the merge, with loopback access: **701 backend tests passed**.
- Frontend: TypeScript passed; **19 Vitest tests passed**.
- Added six regressions covering guided/manual SSH secret forwarding, shell
  quoting and environment isolation, bypassing local Condor for SSH planning,
  and differing local/remote fakeroot capabilities.
- Built an isolated `linux/amd64` Debian test image using the exact Pegasus
  install and monitor-overlay steps from the merged Dockerfile. The wfmonitor
  entry-point check, `pegasus-monitor --version`, and `--help` passed.
- `git diff --check` passed. The complete application image and live FABRIC
  workflow execution were not tested.
