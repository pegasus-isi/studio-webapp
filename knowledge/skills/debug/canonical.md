---
name: debug
description: Diagnose and fix Pegasus workflow failures
---
# Pegasus Workflow Debugger

Diagnose a Pegasus workflow failure using error patterns and log analysis.

## Step 1: Gather Error Information

Ask the user for:

1. **Error message or log output**: Text from `pegasus-analyzer`, `.out`/`.err` files, or terminal
2. **Run directory path**: The Pegasus run directory (if available)
3. **Which step failed**: Job name or ID
4. **What they tried**: Any debugging steps already taken

If a run directory is provided, gather diagnostics:

```bash
pegasus-analyzer <run-dir>
find <run-dir> -name "*.out" -o -name "*.err" | head -20
cat <run-dir>/<job-id>.out
cat <run-dir>/<job-id>.err
```

## Step 2: Match Against Known Failure Patterns

### File Staging Failures

| Error Pattern | Cause | Fix |
|---------------|-------|-----|
| `No such file or directory` for input file | File not in Replica Catalog or typo in LFN | Add `rc.add_replica()` with correct filename |
| `No such file or directory` for support script (`.R`, `.jar`) | Script in Transformation Catalog instead of Replica Catalog | Move to Replica Catalog + add as job input |
| `No such file or directory` for output subdirectory | Wrapper doesn't create subdirs | Add `os.makedirs(os.path.dirname(output), exist_ok=True)` |
| `FileNotFoundError` for `../bin/script.R` | Wrapper uses `__file__`-relative path | Use `os.path.join(os.getcwd(), "script.R")` |
| `glob()` / `os.listdir()` returns empty | Directory scanning in job working dir | Pass explicit file paths as arguments |

### Container Failures

| Error Pattern | Cause | Fix |
|---------------|-------|-----|
| `FATAL: Unable to pull container` | Image name typo or network issue | Verify `docker://docker.io/user/image:tag` is correct |
| `invalid reference format` (stage-in, often with `docker:///...` triple slash) | Pegasus URL serialization mangled a bare official-library reference (`docker://python:3.11-slim` → `docker:///python:3.11-slim`) | Use a fully qualified reference: `docker://docker.io/python:3.11-slim`; check `transformations.yml` for the triple slash |
| `command not found` inside container | Tool not installed | Add tool to Dockerfile and rebuild |
| `ModuleNotFoundError` for Python package | Package not in container | Add `pip install` or `micromamba install` to Dockerfile |

### Resource Failures

| Error Pattern | Cause | Fix |
|---------------|-------|-----|
| `MemoryError` or OOM killed | Insufficient memory | Increase `.add_pegasus_profile(memory="N GB")` |
| `Bus error` (signal 7) | Memory or I/O issue | Increase memory; check for large temp files |
| Job timeout | Step takes too long | Increase timeout; optimize the tool call |

### Argument Parsing Failures

| Error Pattern | Cause | Fix |
|---------------|-------|-----|
| `unrecognized arguments` | Mismatch between `add_args()` and wrapper argparse | Align argument names in both files |
| `the following arguments are required` | Missing argument in `add_args()` | Add the missing `--flag` to job's `add_args()` |
| `error: argument --input: expected one argument` | Value has spaces or is missing | Quote values or check argument construction |

### Dependency Failures

| Error Pattern | Cause | Fix |
|---------------|-------|-----|
| Job runs before input ready | Missing dependency | Ensure `File` objects shared between producer `add_outputs()` and consumer `add_inputs()` |
| Circular dependency error | Circular file references | Check no file is both input and output of same job |

### Wrapper Script Failures

| Error Pattern | Cause | Fix |
|---------------|-------|-----|
| Exit code 1 but no stderr | Wrapper doesn't capture stderr | Add `print(result.stderr, file=sys.stderr)` |
| `Permission denied` on wrapper | Script not executable | `chmod +x bin/script.py` or add shebang |
| Output file not created | Output path doesn't match | Verify output filename matches `File()` LFN |

## Step 3: Read Source Files

Based on the failure pattern, read:

1. **Wrapper script** that failed — check argparse, `os.makedirs`, subprocess calls
2. **workflow_generator.py** — check the job's `add_args()`, `add_inputs()`, `add_outputs()`
3. **Dockerfile** — check if tool is installed
4. **Replica Catalog** entries — check file registrations

## Step 4: Propose Fix

Provide a specific, actionable fix:

1. **Show the exact code change** (before/after or diff-style)
2. **Explain why** the error occurred (root cause)
3. **Show how to verify** the fix:
   - Argument mismatch: `python3 bin/wrapper.py --help`
   - Container issue: `docker run --rm image:tag which tool`
   - File staging: check Replica Catalog entries
   - Whole workflow: `python3 workflow_generator.py --help`

## Step 5: Prevention Advice

After fixing, suggest:

1. Run `/review` to catch other potential issues
2. Test each step locally before Pegasus submission
3. Check the file I/O matching between wrappers and generator

Refer to `AGENTS.md` for additional error patterns and solutions.
