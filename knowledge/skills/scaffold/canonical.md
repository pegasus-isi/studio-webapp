---
name: scaffold
description: Generate a complete new Pegasus workflow project from scratch
---
# Pegasus Workflow Scaffold

You are a Pegasus workflow generator. Create a complete new workflow project from scratch.

## CRITICAL RULES — NEVER VIOLATE

- **NEVER create files in `/tmp/`** — they are invisible to the user's file browser
- **ALWAYS create the workflow under `$HOME/work/workflows/`** — the ONLY persistent, visible directory
- **ALWAYS ask the user for the workflow name first** — do NOT invent a name
- **You MUST write every file to disk** using the Write tool or file-creation command. Do NOT just display code in chat.
- **You MUST create ALL FOUR file types**: (1) workflow_generator.py, (2) bin/*.py wrappers, (3) Docker/Dockerfile, (4) README.md. Skipping ANY of these is a failure. Do NOT tell the user to "add a Dockerfile later" or "feel free to add" — create it NOW.
- After creating all files, run `ls -R` to confirm they exist and show the user.
- **Do NOT finish until all four file types exist on disk.** If `ls -R` shows a missing file, write it before responding.

## Step 1: Gather Requirements

Ask the user these questions (skip any they already answered):

1. **Pipeline name**: What should the workflow be called? (e.g., "rnaseq", "weather-analysis") — WAIT for answer
2. **Pipeline steps**: Describe each step — what tool, what inputs, what outputs?
3. **Data source**: Where does input data come from?
   - Local files (FASTQ, CSV) → needs Replica Catalog entries
   - API fetch at runtime (USGS, OpenAQ) → first job fetches, no RC entries
   - Both (reference files + API data)
4. **Iteration pattern**: How does the pipeline parallelize?
   - Per-sample (each sample goes through same pipeline independently)
   - Per-region/location (loop over geographic regions)
   - Single linear pipeline (no parallelism)
   - Fan-out/fan-in (process items in parallel, then merge)
5. **Tools needed**: All command-line tools or Python libraries each step uses
6. **ML component?**: Train-once-predict-many or train-per-item?
7. **Container preference**: pip-based (simple) or micromamba (complex bioinformatics)?
8. **Wrapper type**: Python (recommended) or shell (for tools with nested output)?

## Step 2: Create Directory Structure

```bash
mkdir -p $HOME/work/workflows/{pipeline-name}-workflow/{bin,Docker}
```

## Step 3: Write Each File to Disk

You MUST use the Write tool to create EACH file below. Do NOT skip any file.

### File 1 (MANDATORY): `workflow_generator.py`

Use this template as your starting point. Customize the sections marked `[CUSTOMIZE]`:

```python
#!/usr/bin/env python3
"""Pegasus workflow generator for [WORKFLOW_NAME]."""

import argparse
import logging
import os
import sys
from pathlib import Path

from Pegasus.api import *

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# [CUSTOMIZE] Per-tool resource configuration
TOOL_CONFIGS = {
    "step1": {"memory": "2 GB", "cores": 1},
    "step2": {"memory": "4 GB", "cores": 2},
}


class MyWorkflow:
    """[CUSTOMIZE] Describe your workflow."""

    wf = None
    sc = None
    tc = None
    rc = None
    props = None
    dagfile = None
    wf_dir = None
    wf_name = "my_workflow"  # [CUSTOMIZE]

    def __init__(self, dagfile="workflow.yml"):
        self.dagfile = dagfile
        self.wf_dir = str(Path(__file__).parent.resolve())
        self.shared_scratch_dir = os.path.join(self.wf_dir, "scratch")
        self.local_storage_dir = os.path.join(self.wf_dir, "output")

    def write(self):
        if self.sc is not None:
            self.sc.write()
        self.props.write()
        self.rc.write()
        self.tc.write()
        self.wf.write(file=self.dagfile)

    def create_pegasus_properties(self):
        self.props = Properties()
        self.props["pegasus.transfer.threads"] = "16"

    def create_sites_catalog(self, exec_site_name="condorpool"):
        self.sc = SiteCatalog()
        local = Site("local").add_directories(
            Directory(Directory.SHARED_SCRATCH, self.shared_scratch_dir)
            .add_file_servers(FileServer("file://" + self.shared_scratch_dir, Operation.ALL)),
            Directory(Directory.LOCAL_STORAGE, self.local_storage_dir)
            .add_file_servers(FileServer("file://" + self.local_storage_dir, Operation.ALL)),
        )
        exec_site = (
            Site(exec_site_name)
            .add_condor_profile(universe="vanilla")
            .add_pegasus_profile(style="condor")
        )
        self.sc.add_sites(local, exec_site)

    def create_transformation_catalog(self, exec_site_name="condorpool"):
        self.tc = TransformationCatalog()
        container = Container(
            "my_container",  # [CUSTOMIZE]
            container_type=Container.SINGULARITY,
            image="docker://username/image:latest",  # [CUSTOMIZE]
            image_site="docker_hub",
        )
        transformations = []
        for tool_name, config in TOOL_CONFIGS.items():
            tx = Transformation(
                tool_name,
                site=exec_site_name,
                pfn=os.path.join(self.wf_dir, f"bin/{tool_name}.py"),
                is_stageable=True,
                container=container,
            ).add_pegasus_profile(memory=config["memory"], cores=config.get("cores", 1))
            transformations.append(tx)
        self.tc.add_containers(container)
        self.tc.add_transformations(*transformations)

    def create_replica_catalog(self):
        self.rc = ReplicaCatalog()
        # [CUSTOMIZE] Register input files:
        # self.rc.add_replica("local", "input.csv", "file://" + os.path.abspath("data/input.csv"))

    def create_workflow(self, args):
        self.wf = Workflow(self.wf_name, infer_dependencies=True)
        # [CUSTOMIZE] Build DAG — create Job objects with add_args, add_inputs, add_outputs
        # Key rules:
        #   - stage_out=True ONLY on final outputs
        #   - register_replica=False on ALL outputs
        #   - Job _id must be unique: f"{step}_{item}"
        #   - Share File objects between producer/consumer jobs (same Python object)


def main():
    parser = argparse.ArgumentParser(description="[CUSTOMIZE] Workflow description")
    parser.add_argument("-s", "--skip-sites-catalog", action="store_true")
    parser.add_argument("-e", "--execution-site-name", default="condorpool")
    parser.add_argument("-o", "--output", default="workflow.yml")
    # [CUSTOMIZE] Add workflow-specific arguments
    parser.add_argument("--items", nargs="+", required=True, help="Items to process")
    args = parser.parse_args()

    workflow = MyWorkflow(dagfile=args.output)
    workflow.create_pegasus_properties()
    if not args.skip_sites_catalog:
        workflow.create_sites_catalog(args.execution_site_name)
    workflow.create_transformation_catalog(args.execution_site_name)
    workflow.create_replica_catalog()
    workflow.create_workflow(args)
    workflow.write()
    logger.info(f"Workflow written to {args.output}")


if __name__ == "__main__":
    main()
```

Key rules for customizing the template:
- Use `infer_dependencies=True` on the Workflow
- Use `stage_out=True` only on final outputs; `stage_out=False` for intermediates
- Use `register_replica=False` on all outputs
- Job `_id` must be unique — use `f"{step}_{item}"` pattern
- File objects must be shared between producer and consumer jobs (same Python object)
- For fan-in merge steps, collect output files in a list and pass to merge job via `add_inputs(*files)`

### File 2 (MANDATORY): `bin/{step}.py` (one per pipeline step)

Write one wrapper script per step. Use this template:

```python
#!/usr/bin/env python3
"""[CUSTOMIZE] Description of what this step does."""

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
    args = parser.parse_args()

    logger.info(f"Input: {args.input}")
    logger.info(f"Output: {args.output}")

    # Create output directory if path contains /
    out_dir = os.path.dirname(args.output)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    # [CUSTOMIZE] Run the tool — choose one pattern:
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
    # Pattern B: API fetch
    # import requests
    # response = requests.get(url, params=params, timeout=60)
    # response.raise_for_status()
    # with open(args.output, "w") as f:
    #     f.write(response.text)
    #
    # Pattern C: Pure Python
    # import pandas as pd
    # df = pd.read_csv(args.input)
    # result = process(df)
    # result.to_json(args.output)

    # Verify output exists
    if not os.path.exists(args.output):
        logger.error(f"Expected output not found: {args.output}")
        sys.exit(1)
    logger.info(f"Output: {args.output}")


if __name__ == "__main__":
    main()
```

Critical wrapper rules:
- argparse flags MUST match what `workflow_generator.py` passes in `add_args()`
- NEVER use `glob()`, `os.listdir()`, or directory scanning to find input files
- If the wrapper uses a support file (R script, JAR), find it with `os.path.join(os.getcwd(), "filename")` — NOT relative to `__file__`
- Always log the command being run — essential for debugging via `pegasus-analyzer`

After writing each wrapper, run: `chmod +x bin/{step}.py`

### File 3 (MANDATORY): `Docker/Dockerfile`

**You MUST create this file. Do NOT skip it or suggest the user add it later.**
Choose the right base image based on the tools needed and customize:

**Option A — pip-based (simple):**
```dockerfile
FROM python:3.8-slim
RUN apt-get update && \
    apt-get install -y --no-install-recommends wget curl && \
    rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir pandas numpy matplotlib requests
ENV PYTHONUNBUFFERED=1
CMD ["/bin/bash"]
```

**Option B — micromamba (bioinformatics):**
```dockerfile
FROM mambaorg/micromamba:1.5-jammy
USER root
RUN apt-get update && apt-get install -y --no-install-recommends wget curl xvfb && \
    rm -rf /var/lib/apt/lists/*
USER $MAMBA_USER
RUN micromamba install -y -n base -c conda-forge -c bioconda \
    python=3.8 pandas numpy [tools] && \
    micromamba clean --all --yes
ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["/usr/local/bin/_entrypoint.sh"]
CMD ["bash"]
```

Key Dockerfile rules:
- ALL tools go in ONE container shared across all jobs
- Pin all tool versions for reproducibility
- Always set `ENV PYTHONUNBUFFERED=1` for real-time Pegasus log capture
- Use `--no-cache-dir` (pip) or `clean --all` (micromamba) to minimize image size
- If `is_stageable=False`, COPY wrapper scripts and `chmod +x`

### File 4 (MANDATORY): `README.md`

**You MUST create this file. Do NOT skip it.**
Write a README covering: pipeline overview (with ASCII DAG), directory structure,
prerequisites, setup (Docker build, input data), usage (CLI options, submit, monitor),
outputs, and resource requirements.

## Step 4: Verify All Files Exist

After writing ALL files, run:

```bash
ls -R $HOME/work/workflows/{pipeline-name}-workflow/
```

Show the output to the user. Verify that ALL FOUR mandatory file types appear:
1. `workflow_generator.py` — if missing, STOP and write it
2. `bin/*.py` — if missing, STOP and write them
3. `Docker/Dockerfile` — if missing, STOP and write it
4. `README.md` — if missing, STOP and write it

**Do NOT respond to the user until all four exist.** If any file is missing,
write it immediately before saying anything else.

## Step 5: Validation Checklist

Before finishing, verify:
- [ ] Every `add_args()` filename in the generator matches a `File()` LFN
- [ ] Wrapper argparse matches the generator's `add_args()` calls
- [ ] File objects are shared between producer/consumer jobs (same Python object)
- [ ] Only final outputs have `stage_out=True`
- [ ] All job IDs are unique
- [ ] Container Dockerfile installs every tool used by every wrapper
- [ ] All local input files are in the Replica Catalog
- [ ] No directory scanning (`glob()`, `os.listdir()`) between jobs

Refer to `AGENTS.md` for real-world examples from earthquake, airquality, tnseq,
mag, soilmoisture, and proteinfold workflows.
