# pegasus-monitor --json sample documents

Output of the native `pegasus-monitor` (pegasus-isi/pegasus GH-2270 at
`c7b1f3004`, `format_version` 1), captured on 2026-11-10 from real workflows on a
single-node HTCondor 25.14.1 pool with the Pegasus 6.0.0.dev0 nightly, Python 3.13.
Re-indented; otherwise as the tool wrote them, with one exception:

- `diagnose-why-idle-held-idle.json` is the call Studio makes
  (`--json --diagnose --why-idle`). It was captured with `--once` as well, so
  its per-job `snapshot` was set to null and `modes` trimmed to match what the
  tool emits without `--once`.

The workflows: `pm-held` has one job held with `condor_hold` and one idle job
asking for 9,000,000 MiB of memory; `pm-fail` has a job that exits 1 with its
retries exhausted. `before-db` means the hold had not yet reached the stampede
DB, so the classification comes from the HTCondor queue alone.

When the upstream contract changes, re-capture rather than hand-edit.
