# FABRIC Phase 0 review and main integration — 2026-09-10

Reviewed `feat/fabric-local-studio-phase0` at `f1cf09d` against `origin/main`
at `75ac6e7`. The feature branch had 14 unique commits and was missing 107
commits from main. The review focused on compute backends, workflow execution,
image building, monitoring, and their overlap with the incoming changes.

## Follow-up remediation

The subsequent review identified eight issues. Fixes have now been applied on
this feature branch; the original merge itself did not resolve them.

| Finding | Applied fix |
| --- | --- |
| Background pulls overwrite saved edits | Separate download snapshot and persistent shared baseline; preserve edits/deletions and stop affected projects on two-sided conflicts. |
| Generate returns before files reach the mirror | Await a refresh before success in both manual and guided Generate; serialize push/command/refresh stages. |
| SSH disables host-key verification | Persistent known hosts and `accept-new` by default; configurable strict verification and separate control sockets when configuration changes. |
| Run ownership follows the active backend | Resolve configured mirror ownership for run operations and bind in-flight work to its initial backend. |
| Remote deletion only removes the mirror | Inspect/cancel on the execution host, delete remotely first, then remove mirror state; retain DB records on failure. |
| Output catalogs expose remote absolute paths as local paths | Translate storage through the run's owning backend and confine remote output lookup to the mirrored workspace. |
| Notifications retain the old root | Restore selection before startup; restart the watcher with the selected backend and root on switching. |
| Frontend selection and parameter caches remain stale | Shared selection query/mutation, cancellation of stale requests, backend-specific project state, and reactive terminal menu. |

Operational behavior, migration notes, and limits are documented in
[fabric-backend-safety.md](fabric-backend-safety.md).

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

## Merge validation (before follow-up fixes)

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

## Remediation validation

- Final full backend suite: **727 passed**. The test environment excludes the
  developer's installed Condor/Pegasus commands and real backend configuration;
  the suite's loopback server was permitted. Only the existing Starlette/httpx
  deprecation warning remained.
- Frontend: **23 Vitest tests passed**; TypeScript passed.
- Added 26 backend regressions and four frontend cache regressions, including
  first/repeated generation, conflicting edits, failed transfers, deletion with
  a missing mirror, cancellation failure, owner routing after switching,
  notification startup/rebinding, and late frontend responses.
- `git diff --check` passed. No Docker changes were needed. The complete
  application image and live FABRIC workflow execution were not retested.
