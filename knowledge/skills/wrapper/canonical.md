---
name: wrapper
description: Generate a wrapper script for a single Pegasus pipeline step
---
# Pegasus Wrapper Script Generator

Generate a wrapper script for a single pipeline step. You MUST write the file to
disk — do NOT just display code in chat.

## Step 1: Gather Requirements

Ask the user (skip questions already answered):

1. **Tool name**: What tool does this wrapper invoke? (e.g., `samtools sort`, `bwa mem`, a Python library, an API)
2. **Inputs and outputs**: What files does it read and write? Include filenames.
3. **Does the tool produce nested output?** If yes (MEGAHIT, QUAST, Prokka, GTDB-Tk), use a shell wrapper with output flattening.
4. **Python or shell?**
   - Python (recommended): subprocess calls, API fetches, pure-Python analysis
   - Shell (when needed): tools with nested output directories, headless display handling
5. **Multiple input files?** For fan-in/merge jobs, use `action="append"` or `nargs="+"`
6. **Support files?** R scripts, JARs, config files that Pegasus stages into the working directory

## Step 2: Write the Wrapper to Disk

First ensure the directory exists:

```bash
mkdir -p $HOME/work/<workflow-name>/bin
```

### Python Wrapper Template

Use this template, customizing the marked sections:

```python
#!/usr/bin/env python3
"""[CUSTOMIZE] Description of what this pipeline step does."""

import argparse
import logging
import os
import subprocess
import sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="[CUSTOMIZE] Step description")

    # [CUSTOMIZE] Arguments MUST match what workflow_generator.py passes in add_args()
    parser.add_argument("--input", required=True, help="Input file")
    parser.add_argument("--output", required=True, help="Output file")
    parser.add_argument("--threads", type=int, default=1, help="Thread count")

    # For fan-in jobs accepting multiple files:
    # parser.add_argument("--input", action="append", required=True)

    args = parser.parse_args()

    logger.info(f"Input: {args.input}")
    logger.info(f"Output: {args.output}")

    # Create output directory if path contains /
    out_dir = os.path.dirname(args.output)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    # [CUSTOMIZE] Choose execution pattern:
    #
    # Pattern A: subprocess call to CLI tool
    cmd = ["mytool", "--input", args.input, "--output", args.output]
    logger.info(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    print(result.stdout)
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        sys.exit(result.returncode)
    #
    # Pattern B: shell command with pipes
    # cmd = f"tool1 {args.input} | tool2 > {args.output}"
    # result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    #
    # Pattern C: API fetch
    # import requests
    # response = requests.get(url, params=params, timeout=60)
    # response.raise_for_status()
    # with open(args.output, "w") as f:
    #     f.write(response.text)
    #
    # Pattern D: Pure Python analysis
    # import pandas as pd
    # df = pd.read_csv(args.input)
    # results = analyze(df)
    # results.to_json(args.output)
    #
    # Pattern E: Support file in working directory
    # (Pegasus stages support files from Replica Catalog into job cwd)
    # script_path = os.path.join(os.getcwd(), "analysis.R")

    # Verify output exists
    if not os.path.exists(args.output):
        logger.error(f"Expected output not found: {args.output}")
        sys.exit(1)
    logger.info(f"Output written: {args.output}")


if __name__ == "__main__":
    main()
```

### Shell Wrapper Template

Use for tools with nested output directories:

```bash
#!/bin/bash
# [CUSTOMIZE] Description of what this wrapper does.
set -euo pipefail

echo "=== [Step Name] ==="
echo "Arguments: $@"

# Parse arguments
OUTPUT_DIR=""
SAMPLE=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        -o|--out-dir) OUTPUT_DIR="$2"; shift 2 ;;
        --sample) SAMPLE="$2"; shift 2 ;;
        *) shift ;;
    esac
done

# [CUSTOMIZE] Run the tool
echo "Running: mytool ..."
mytool --input "$SAMPLE" --output "$OUTPUT_DIR"

# [CUSTOMIZE] Flatten nested output for Pegasus
# Pegasus expects output files in the working directory root.
# Copy specific files from nested tool output:
if [[ -n "$OUTPUT_DIR" ]]; then
    cp "${OUTPUT_DIR}/result.txt" "${SAMPLE}_result.txt"
fi

echo "Completed successfully"
```

## Step 3: Make Executable and Verify

```bash
chmod +x bin/<step>.py
ls -la bin/
```

## Step 4: Show Integration Code

After writing the wrapper, show the user the corresponding workflow_generator.py code:

**Transformation Catalog entry:**
```python
tx = Transformation(
    "step_name",
    site=exec_site_name,
    pfn=os.path.join(self.wf_dir, "bin/step_name.py"),
    is_stageable=True,
    container=container,
).add_pegasus_profile(memory="2 GB", cores=1)
```

**Job definition:**
```python
job = (
    Job("step_name", _id=f"step_name_{item}")
    .add_args("--input", input_file, "--output", output_file)
    .add_inputs(input_file)
    .add_outputs(output_file, stage_out=False, register_replica=False)
)
wf.add_jobs(job)
```

## Critical Rules

1. **Arguments must match**: argparse flags in wrapper MUST exactly match `add_args()` in workflow_generator.py
2. **No directory scanning**: NEVER use `glob()`, `os.listdir()`, `list.files()`, or `find` to discover input files
3. **Support files via `os.getcwd()`**: Find support files with `os.path.join(os.getcwd(), "filename")` — NOT `__file__`
4. **Create subdirectories**: Any output path with `/` needs `os.makedirs(os.path.dirname(output), exist_ok=True)`
5. **Print the command**: Always log the command being run — essential for debugging via `pegasus-analyzer`
6. **Propagate exit codes**: Always use `sys.exit(result.returncode)` after subprocess calls

Refer to `AGENTS.md` for wrapper script examples and best practices.
