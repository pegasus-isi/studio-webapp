---
name: review
description: Audit a Pegasus workflow against best practices
---
# Pegasus Workflow Review

Audit a Pegasus workflow project against an 8-category checklist. This is a
read-only analysis — do not modify files unless asked.

## Step 1: Gather Context

1. Ask the user which workflow directory to review (or auto-detect if the current
   directory has a `workflow_generator.py`).
2. Read ALL relevant files:
   - `workflow_generator.py`
   - All files in `bin/` (wrapper scripts)
   - `Docker/*` (Dockerfile)
   - `README.md` (if it exists)

## Step 2: Run the 8-Category Checklist

For each item, report: **PASS**, **ERROR** (will fail at runtime), **WARNING**
(may cause issues), or **SUGGESTION** (optional improvement).

### Category 1: Transformation Catalog

- [ ] Every wrapper script in `Transformation(pfn=...)` exists at that path
- [ ] `is_stageable=True` for scripts on submit host; `is_stageable=False` for scripts in container
- [ ] Support files (R scripts, JARs) are NOT in the Transformation Catalog — they go in Replica Catalog
- [ ] Container image string is well-formed (`docker://user/image:tag`)
- [ ] Memory and cores are set per tool
- [ ] External data dirs use CondorIO `transfer_input_files`, NOT container `mounts=[]`

### Category 2: Replica Catalog

- [ ] All support files called by wrappers (R scripts, JARs) are registered
- [ ] All input data files are registered (unless fetched at runtime by a fetch job)
- [ ] File paths use `"file://" + os.path.abspath(path)` (absolute with file:// prefix)
- [ ] No wrapper scripts are in the Replica Catalog (those go in Transformation Catalog)

### Category 3: DAG Correctness

- [ ] `infer_dependencies=True` is used (or all dependencies explicitly declared)
- [ ] Every `File` object in `add_outputs()` of one job and `add_inputs()` of another is the SAME Python object (not just same string)
- [ ] `stage_out=True` only on final user-facing outputs; intermediates use `stage_out=False`
- [ ] `register_replica=False` on all `add_outputs()` calls
- [ ] Job `_id` values are unique across all jobs
- [ ] Fan-in merge jobs collect all upstream outputs via `add_inputs(*all_files)`

### Category 4: File I/O Matching (Critical)

For EACH wrapper script:
- [ ] argparse arguments match the `add_args()` call in the workflow generator
- [ ] `--input {filename}` uses same string as the `File()` object's LFN
- [ ] Wrappers call `os.makedirs(os.path.dirname(output), exist_ok=True)` for subdirectory paths
- [ ] No `glob()`, `os.listdir()`, or directory scanning to find input files
- [ ] No `os.path.dirname(__file__)` for support files — use `os.getcwd()` instead

### Category 5: Wrapper Script Correctness

- [ ] Each wrapper propagates exit codes (`sys.exit(result.returncode)`)
- [ ] Each wrapper prints the command being run (for `pegasus-analyzer` debugging)
- [ ] Shell wrappers use `set -euo pipefail`
- [ ] Shell wrappers that flatten nested output copy the right files
- [ ] Fan-in wrappers accept multiple inputs via `action="append"` or `nargs="+"` (not directory scanning)

### Category 6: Resource Configuration

- [ ] Memory allocations are reasonable for each tool
- [ ] CPU cores match what the tool actually uses (`--threads` matches `cores=N`)
- [ ] Jobs that must run on submit node use `execution.site=local` profile

### Category 7: Dockerfile

- [ ] All tools referenced by wrappers are installed in the container
- [ ] `PYTHONUNBUFFERED=1` is set
- [ ] If `is_stageable=False`, scripts are COPYed and chmod +x
- [ ] Base image is appropriate (python-slim for simple, micromamba for bioinformatics)
- [ ] Tool versions are pinned

### Category 8: CLI and Usability

- [ ] `workflow_generator.py --help` would produce useful output
- [ ] Standard flags present: `-s` (skip sites), `-e` (execution site), `-o` (output)
- [ ] Input validation catches missing arguments before Pegasus API calls
- [ ] Error messages are descriptive

## Step 3: Generate Report

Output this structured report:

```
## Pegasus Workflow Review: [workflow_name]

### Summary
- Errors: N
- Warnings: N
- Suggestions: N

### Errors
1. [ERROR] Category N: description
   File: path/to/file:line_number
   Fix: what to change

### Warnings
1. [WARNING] Category N: description
   File: path/to/file:line_number
   Fix: recommendation

### Suggestions
1. [SUGGESTION] Category N: description
   Rationale: why this would help
```

Refer to `AGENTS.md` for detailed patterns and examples to compare against.
