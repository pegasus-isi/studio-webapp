# SSH backend safety and synchronization

The SSH backend runs commands on a configured submit host and exposes an
editable local mirror. Local execution keeps its existing command transport,
submit-host planning, and NFS deletion behavior.

## Host keys

Generated SSH configurations now default to:

```json
{
  "strict_host_key_checking": "accept-new",
  "known_hosts_file": "~/.ssh/known_hosts"
}
```

Existing backend definitions need no additional fields. OpenSSH remembers the
first key it accepts and rejects a changed key, for both the submit host and
its jump host. To require a previously verified key even on the first
connection, set `strict_host_key_checking` to `yes` and provision the verified
keys in the known-hosts file. A custom file's parent directory must exist.

The old generated configurations discarded host keys. On upgrade, hosts not
already in the known-hosts file therefore get a first-use trust decision. If a
FABRIC node is recreated, verify its new fingerprint before replacing its old
known-hosts entry. `no` remains available only as an explicit backend setting
for environments that intentionally accept that risk. Changing the backend
configuration changes the SSH control-socket name so an older multiplexed
connection cannot silently retain the previous trust policy.

## Editable mirrors

Rsync downloads into a separate snapshot under
`~/.studio/backend-sync/<backend-id>/<workspace-identity>/remote/`. A persistent
`baseline.json` beside that snapshot records the last content shared by both
sides. The normal mirror remains under `~/.studio/mirrors/<backend-id>/`, or
its configured `mirror_root`.

- Remote changes update a working file only when it still matches the shared
  baseline. Saved local edits and local deletions survive background pulls.
- Generate, Plan, and Run upload only changed local files. Unchanged copies of
  running workflow databases and logs are not sent back to the submit host.
- If both sides changed a file, Studio preserves both copies and reports the
  paths in the backend status. Operations on that project stop with a conflict
  error; unrelated projects can still run.
- To resolve a conflict, compare the working mirror with the remote snapshot
  and make the working file and the actual remote file agree with the chosen
  content, or delete it on both sides. The next successful pull records that
  agreement. Do not edit the snapshot or baseline journal.
- The first pull after upgrading has no shared baseline. Different existing
  local and remote copies are treated as conflicts, preserving both versions.
- Successful generation waits for its generated files to reach the mirror
  before reporting success. A failed refresh reports an error. Synchronization
  and project command stages are serialized on each SSH backend.

The snapshot requires disk space for a second copy of mirrored files. Large
`.sif` images remain excluded from synchronization. Keep SSH backend IDs and
mirror roots stable while their runs are in use; each configured mirror is the
namespace used to identify its runs' execution host.

## Deletion and output files

Deleting a remote run or project checks a fresh remote snapshot, cancels runs
that have submission markers and no recorded terminal state, and removes the
remote files before the working mirror. Failed cancellation or remote deletion
is reported instead of silently dropping the run from Studio's database.
Remote parent symlinks cannot redirect deletion outside the workspace.

Site-catalog output paths under the remote workspace are translated into that
run's own mirror. Outputs outside the synchronized workspace cannot be served
through the local mirror; Studio does not interpret those paths as files on the
API host. Output symlinks must also remain inside the mirrored workspace.

## Backend switching

In-flight workflow operations stay bound to their original backend. Run status,
event streams, cancellation, analysis, and output lookup resolve the owning
configured mirror independently of the current Settings selection. Guided-flow
state is separated by backend and project name. Previously started mirrors
continue syncing until application shutdown so those runs remain monitorable.

Startup restores the saved selection before notifications begin. Switching
restarts the watcher with the selected root and backend context, retaining
notification preferences, persisted history, and subscribers. Resource caches
also check the backend that produced their snapshot.

Settings, workflow forms, and the terminal menu share one frontend selection.
Switching cancels old backend-dependent queries and discards their data; project
parameters and project form state are separated by backend. Existing terminal
sessions keep their original host. Status polling and window-focus refresh pick
up selections changed in another browser tab.

## Verification scope

Regression tests simulate the submit host with temporary filesystem trees and
stub command transport. They cover saved edits, conflicts, upload failures,
generation barriers, ownership after switching, deletion and cancellation,
output translation, notification lifecycle, SSH trust configuration, and
frontend cache races. They do not constitute live FABRIC end-to-end validation.
