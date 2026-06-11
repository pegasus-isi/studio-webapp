# [Workflow Name] Pegasus Workflow

A [Pegasus WMS](https://pegasus.isi.edu/) workflow for [brief description of what the workflow does].

## Pipeline Overview

[Describe the pipeline as an ASCII DAG showing the flow of data through steps.]

```
Input ──> step1 ──> step2 ──> step3 ──> Output
                       └──> step2b ──> (parallel branch)
```

| Step | Tool | Description |
|------|------|-------------|
| 1. step1 | [tool name] | [what it does] |
| 2. step2 | [tool name] | [what it does] |
| 3. step3 | [tool name] | [what it does] |

## Directory Structure

```
my-workflow/
├── workflow_generator.py       # Pegasus workflow generator
├── bin/
│   ├── step1.py                # [description] wrapper
│   ├── step2.py                # [description] wrapper
│   └── step3.py                # [description] wrapper
├── Docker/
│   └── My_Dockerfile           # Container with all required tools
├── data/
│   └── test/                   # Test input data
└── README.md
```

## Prerequisites

- [Pegasus WMS](https://pegasus.isi.edu/) >= 5.0
- [HTCondor](https://htcondor.org/) >= 10.2
- Python 3.8+
- Docker or Singularity (for container execution)

## Setup

### 1. Build the Docker Container

```bash
cd my-workflow
docker build -t username/image:latest -f Docker/My_Dockerfile .
```

### 2. Prepare Input Data

[Describe the expected input format and where to place files.]

## Usage

### Generate Workflow

```bash
./workflow_generator.py \
    --items item1 item2 \
    --output workflow.yml
```

### CLI Options

| Option | Default | Description |
|--------|---------|-------------|
| `--items` | (required) | Items to process in parallel |
| `-e`, `--execution-site-name` | `condorpool` | HTCondor execution site name |
| `-s`, `--skip-sites-catalog` | false | Skip site catalog creation |
| `-o`, `--output` | `workflow.yml` | Output workflow file |

### Submit Workflow

```bash
pegasus-plan --submit -s condorpool -o local workflow.yml
```

### Monitor Workflow

```bash
pegasus-status <run-directory>
pegasus-statistics <run-directory>
```

## Outputs

The workflow produces the following final outputs in the `output/` directory:

| Output | Description |
|--------|-------------|
| `*_result.json` | [description] |
| `*_visualization.png` | [description] |

## Resource Requirements

| Step | Memory | Cores |
|------|--------|-------|
| step1 | 2 GB | 1 |
| step2 | 4 GB | 2 |
| step3 | 2 GB | 1 |

## Running on FABRIC

The workflow can also be run on the [FABRIC testbed](https://fabric-testbed.net/) by deploying a distributed Pegasus/HTCondor cluster across FABRIC sites.

### Deploy a Pegasus/HTCondor Cluster

You can provision a cluster using either of the following notebooks:

| Option | Link | Description |
|--------|------|-------------|
| FABRIC Artifact (Recommended) | [Pegasus-FABRIC Artifact](https://artifacts.fabric-testbed.net/artifacts/53da4088-a175-4f0c-9e25-a4a371032a39) | Pre-configured notebook from the FABRIC Artifacts repository |
| Jupyter Examples | [pegasus-fabric.ipynb](https://github.com/fabric-testbed/jupyter-examples/blob/f7be0c75f22544c72d7b3e3fa42bbdfd9d8bb841/fabric_examples/complex_recipes/pegasus/pegasus-fabric.ipynb) | Notebook from the official FABRIC Jupyter examples |

Both notebooks provision the following cluster architecture:

- **Submit Node** -- Central Manager running HTCondor scheduler and Pegasus WMS
- **Worker Nodes** -- Distributed execution points across multiple FABRIC sites
- **FABNetv4 Networking** -- Private L3 network connecting all nodes

### Setup Steps

1. Log into the [FABRIC JupyterHub](https://jupyter.fabric-testbed.net/)
2. Upload or clone one of the Pegasus-FABRIC notebooks above
3. Configure your desired sites and node specifications
4. Run the notebook to provision the cluster
5. Clone this repository on the submit node
6. Generate and submit the workflow on the submit node

## Dependencies

[List key Python/system packages and their versions.]
