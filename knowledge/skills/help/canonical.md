---
name: help
description: Show available Pegasus workflow skills and when to use each
---
## Pegasus AI Workbench — Available Skills

| Skill | When to Use | What It Does |
|-------|-------------|--------------|
| `/scaffold` | Starting a new workflow from scratch | Generates complete project: `workflow_generator.py`, wrapper scripts, Dockerfile, README |
| `/wrapper` | Adding a single pipeline step | Generates a Python or shell wrapper script for one tool |
| `/dockerfile` | Building the container image | Generates a Dockerfile for your workflow's tool stack |
| `/convert` | Migrating from Snakemake or Nextflow | Converts an existing pipeline to Pegasus with side-by-side mapping |
| `/debug` | Workflow failed | Diagnoses failures from logs using a pattern database and proposes fixes |
| `/review` | Workflow written but untested | Audits against an 8-category best practices checklist |

## Quick Start

1. **New workflow?** → Start with `/scaffold`
2. **Adding a step?** → Use `/wrapper` then update `workflow_generator.py`
3. **Need a container?** → Use `/dockerfile`
4. **Have a Snakemake/Nextflow pipeline?** → Use `/convert`
5. **Something broke?** → Use `/debug`
6. **Ready to submit?** → Run `/review` first

## Reference Material

- `AGENTS.md` in your workspace — Comprehensive Pegasus WMS development guide
  with templates, patterns, and real-world examples

## Example Workflows

| Workflow | Key Pattern |
|----------|-------------|
| tnseq | Per-sample parallelism, fan-in merge, R/JAR support files |
| earthquake | API data fetch, per-region loops, no replica catalog inputs |
| mag | Shell wrappers, container-embedded scripts (`is_stageable=False`), micromamba |
| soilmoisture | ML train-then-predict, per-polygon parallelism |
| airquality | Dual pipeline, skip flags, multiple data sources, fan-in merge |
| proteinfold | Nextflow conversion, GPU jobs, multi-mode pipelines, CondorIO caches |

## Key Pegasus Concepts (Quick Reference)

A Pegasus workflow has five components:

| Component | Purpose |
|-----------|---------|
| **Properties** | Configuration (transfer threads, retries) |
| **Site Catalog** | Execution sites (local, condorpool) |
| **Transformation Catalog** | Executables (wrapper scripts) + containers |
| **Replica Catalog** | Input data files + support files |
| **Workflow (DAG)** | Jobs, I/O files, and dependencies |

Common commands:
```bash
python workflow_generator.py --submit [args]   # Generate + submit
pegasus-status <run-dir>                       # Monitor
pegasus-analyzer <run-dir>                     # Debug failures
```
