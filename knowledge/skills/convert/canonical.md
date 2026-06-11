---
name: convert
description: Convert a Snakemake or Nextflow pipeline to Pegasus WMS
---
# Snakemake/Nextflow to Pegasus Converter

Convert an existing Snakemake or Nextflow pipeline to Pegasus. You MUST write ALL
generated files to disk — do NOT just display code in chat.

## Step 1: Read the Source Pipeline

Ask the user for the path to their pipeline:
- **Snakemake**: `Snakefile` (and any `config.yaml`, `environment.yaml`)
- **Nextflow**: `main.nf` (and any `nextflow.config`, `modules/`)

Read ALL source files thoroughly before starting conversion.

## Step 2: Map Concepts

### Snakemake → Pegasus

| Snakemake | Pegasus |
|-----------|---------|
| `rule name:` | `Transformation("name", ...)` + `Job("name", ...)` + wrapper script |
| `input: "file.txt"` | `job.add_inputs(File("file.txt"))` |
| `output: "result.txt"` | `job.add_outputs(File("result.txt"), stage_out=..., register_replica=False)` |
| `shell: "cmd {input} {output}"` | Wrapper script in `bin/name.py` |
| `{wildcards.sample}` | `for sample in samples:` loop |
| `expand(...)` | Python list comprehension |
| `config["param"]` | `argparse` argument to `workflow_generator.py` |
| `conda: "env.yaml"` | `Dockerfile` with same packages |
| `threads: N` | `.add_pegasus_profile(cores=N)` |
| `resources: mem_mb=N` | `.add_pegasus_profile(memory="N MB")` |
| `params: data_dir="path"` | Explicit file paths (no directory scanning) |
| `rule all: input: [files]` | No equivalent — Pegasus runs all jobs in the DAG |

### Nextflow → Pegasus

| Nextflow | Pegasus |
|----------|---------|
| `process NAME { ... }` | `Transformation` + `Job` + wrapper script |
| `input: path(x) from ch` | `job.add_inputs(File(x))` |
| `output: path("*.txt") into ch` | `job.add_outputs(File("name.txt"))` — MUST be explicit, not glob |
| `script: """cmd"""` | Wrapper script in `bin/name.py` |
| Channel operations | Python loops and list operations |
| `params.x` | `argparse` argument |
| Container directive | `Container()` in transformation catalog |
| Shared filesystem mounts | CondorIO `transfer_input_files` (NOT container `mounts=[]`) |

## Step 3: Conversion Process

### 3a. List All Rules/Processes

For each rule (Snakemake) or process (Nextflow), document:
- Name
- Inputs (files)
- Outputs (files)
- Shell command
- Resources (memory, threads)
- Dependencies (which rules feed into this one)

### 3b. Map Wildcards/Channels to Python Loops

- `{sample}` → `for sample in self.samples:`
- `{region}` → `for region in args.regions:`
- Channel operations → Python list operations

### 3c. Identify Support Files

Files called by rules but not tracked as inputs/outputs:
- R scripts → Replica Catalog + job inputs
- JARs → Replica Catalog + job inputs
- Config files → Replica Catalog + job inputs

### 3d. Write ALL Files to Disk

Create the directory structure:

```bash
mkdir -p $HOME/work/<name>/{bin,Docker}
```

Then use the Write tool to create EACH of these files:

1. **`bin/<step>.py`** — One wrapper per rule/process. Each wrapper:
   - Uses argparse with flags matching the generator's `add_args()`
   - Runs the shell command via `subprocess.run()`
   - Propagates exit codes with `sys.exit(result.returncode)`
   - Logs the command for debugging

2. **`workflow_generator.py`** — Complete generator with:
   - All five catalogs (Properties, Site, Transformation, Replica, Workflow)
   - One Transformation per wrapper script
   - Jobs created in loops matching the original wildcards/channels
   - File objects shared between producer/consumer jobs

3. **`Docker/Dockerfile`** — Single container with ALL tools from `conda:` envs or container directives

4. **`README.md`** — Documents the converted workflow with original → Pegasus mapping

After writing all files, run `ls -R $HOME/work/<name>/` to confirm and show the user.

## Step 4: Handle Common Pitfalls

1. **Rules calling scripts** (`Rscript {input.script}`) → register script in Replica Catalog + add as job input
2. **`params.data_dir` scanning** → rewrite to explicit file lists
3. **Shell pipes** (`cmd1 | cmd2 > out`) → work fine inside wrapper scripts via `subprocess.run(cmd, shell=True)`
4. **`rule all`** → no equivalent; Pegasus runs all jobs
5. **`glob_wildcards()`** → resolve at workflow generation time, not inside jobs
6. **Shared filesystem caches** → use CondorIO `transfer_input_files`, NOT container `mounts=[]`
7. **Nextflow `output: path("*.txt")`** → MUST be explicit filenames in Pegasus (no globs)

## Step 5: Validation

After conversion, verify:
- [ ] Every rule/process has a wrapper + transformation + job(s)
- [ ] All wildcards map to Python loops
- [ ] All support files are in the Replica Catalog
- [ ] No directory scanning in wrappers
- [ ] Wrapper argparse matches job `add_args()`
- [ ] Dockerfile includes ALL tools from original environment

## Step 6: Show Side-by-Side Comparison

Present the mapping so the user can verify:

```
Snakemake rule: align          →  Wrapper: bin/align.py
  input: "{sample}.fq.gz"      →    --input {sample}.fq.gz
  output: "{sample}.bam"        →    --output {sample}.bam
  shell: "bwa mem ..."          →    subprocess.run(["bwa", "mem", ...])
  threads: 4                    →    .add_pegasus_profile(cores=4)
```

Refer to `AGENTS.md` for the full conversion guide and real-world examples.
