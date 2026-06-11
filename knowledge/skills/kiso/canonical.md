---
name: kiso
description: >
  Generate a Kiso experiment.yml configuration file for running Pegasus workflows
  or shell experiments on cloud/edge/local testbeds. Use this skill whenever the user
  wants to create or edit a Kiso experiment configuration, provision infrastructure for
  a Pegasus workflow, set up HTCondor, configure sites on Vagrant/Chameleon/FABRIC,
  or run a workflow in a reproducible cloud environment. Trigger on: "create experiment.yml",
  "kiso experiment", "run workflow on chameleon", "provision HTCondor cluster",
  "kiso config", "set up kiso", "run pegasus on fabric", or any request to run a
  Pegasus workflow on provisioned infrastructure.
allowed-tools:
  - Read
  - Glob
  - Grep
  - Write
  - Edit
---

# Kiso Experiment Configuration Generator

You help users create a `experiment.yml` file for [Kiso](https://kiso.readthedocs.io) —
a framework that provisions infrastructure, installs software, and runs experiments
(including Pegasus workflows) on cloud/edge testbeds reproducibly.

## Step 1: Read the reference

Read `references/experiment-configuration.md` (in this skill's directory) for the full
schema, all supported options, and complete examples. Do this before asking any
questions.

## Step 2: Understand the context

Before asking questions, look around:

- Check for an existing `experiment.yml` or `experiment.yaml` — if found, read it and
  offer to extend or fix it rather than starting from scratch.
- Check for a `workflow_generator.py`, `bin/`, or `main.sh` to understand what script
  generates the Pegasus workflow (this becomes the `main:` field).
- Check for a `README.md` that might describe the experiment setup.

## Step 3: Gather requirements

Ask only what you don't already know. Group questions naturally — don't fire off a
numbered list unless needed. Key things to establish:

**Sites (infrastructure)**
- Where should the experiment run?
  - **Vagrant** — local VMs (VirtualBox), great for development/testing
  - **Chameleon KVM** — cloud VMs (TACC or UC sites)
  - **Chameleon Edge** — edge devices (Raspberry Pi, etc.)
  - **FABRIC** — research testbed
  - Multi-site combinations are possible
- How many nodes, and what roles? (e.g., 1 submit + 4 execute, or a single personal node)

**Software** (optional)
- Does the workflow need Apptainer (Singularity) or Docker containers on the execute nodes?
- Does it need Ollama for LLM serving?

**Deployment**
- Does the workflow need HTCondor? (Almost always yes for Pegasus workflows)
- For simple single-node testing, a `personal` HTCondor node works; for multi-node,
  use `central-manager` + `submit` + `execute` roles.

**Experiment**
- Pegasus workflow or shell script(s)?
- For Pegasus: what is the main script that generates and submits the workflow?
  (e.g., `bin/workflow_generator.py` or `bin/main.sh`)
- Any input files to stage to remote nodes before running?
- Any setup scripts to run (install dependencies, chmod, etc.)?
- Any output files to collect back to the local machine after the run?

## Step 4: Generate the experiment.yml

Use the patterns from `references/experiment-configuration.md`. Key rules:

1. **Labels are the glue** — invent short names (e.g., `submit`, `execute`,
   `central-manager`) and use them consistently across `sites`, `software`,
   `deployment`, and `experiments`. Every label in `software`/`deployment`/`experiments`
   must be defined in `sites`.

2. **submit_node_labels must point to HTCondor submit or personal nodes** — validate
   this before writing the file. If the user picks a label as submit but hasn't
   configured it as an HTCondor `submit` or `personal` node, flag the mismatch.

3. **Single-node setups**: A single machine can carry multiple labels and HTCondor
   roles (e.g., one Vagrant VM can be `central-manager` + `submit` + `execute`).

4. **The `main:` script** for Pegasus experiments should generate and submit the
   workflow. Include a `setup:` script if the submit node needs dependencies installed
   (e.g., `pip install pegasus-wms`).

5. **Secrets go in `secrets/`** — rc_files for Chameleon/FABRIC credentials should
   reference `secrets/` paths (e.g., `secrets/chi-tacc-openrc.sh`). Remind the user to
   create this directory and add it to `.gitignore`.

## Step 5: Show next steps

After writing `experiment.yml`, tell the user:

```sh
# Validate the configuration
kiso check

# Provision infrastructure
kiso up

# Run the experiment
kiso run

# Destroy infrastructure when done
kiso down
```

If they're using Chameleon or FABRIC, remind them they need credentials in `secrets/`
and the right pip extras installed (`pip install kiso[chameleon]` or
`pip install kiso[fabric]`).

If this is a Vagrant setup, remind them they need VirtualBox and Vagrant installed,
plus `pip install kiso[vagrant]`.
