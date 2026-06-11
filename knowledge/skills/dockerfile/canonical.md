---
name: dockerfile
description: Generate a Dockerfile for a Pegasus workflow's tool stack
---
# Pegasus Dockerfile Generator

Generate a Dockerfile for a Pegasus workflow. You MUST write the file to disk —
do NOT just display it in chat.

## Step 1: Gather Requirements

Ask the user (skip questions already answered):

1. **What tools are needed?** List all CLI tools and Python libraries used by wrapper scripts.
2. **Are there version conflicts?** Tools needing different Python versions or conflicting libraries?
   - If yes → micromamba/conda (resolves conflicts)
   - If no → pip-based (simpler, smaller image)
3. **Are wrapper scripts embedded in the container?** (`is_stageable=False` in transformation catalog)
   - If yes → need `COPY bin/*.sh /usr/local/bin/` and `chmod +x`
4. **Do any tools need headless/display support?** (FastQC, QUAST, matplotlib without display)
   - If yes → need `xvfb`, `libgl1-mesa-glx`, `libfontconfig1`
5. **Preferred base image?**
   - `python:3.8-slim` — lightweight, pip-only
   - `mambaorg/micromamba:1.5-jammy` — conda solver for complex bioinformatics
   - `ubuntu:22.04` — apt + pip + manual installs

## Step 2: Write the Dockerfile to Disk

First create the directory:

```bash
mkdir -p $HOME/work/<workflow-name>/Docker
```

Then write `Docker/Dockerfile` using one of these templates:

### Option A — pip-based (simple Python/data science)

```dockerfile
FROM python:3.8-slim

# System dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        wget curl git \
    && rm -rf /var/lib/apt/lists/*

# Python dependencies — pin versions for reproducibility
RUN pip install --no-cache-dir \
    pandas==2.0.3 \
    numpy==1.24.4 \
    matplotlib==3.7.5 \
    requests==2.31.0 \
    scipy==1.10.1 \
    scikit-learn==1.3.2

# Real-time log output for Pegasus
ENV PYTHONUNBUFFERED=1

WORKDIR /app
CMD ["/bin/bash"]
```

### Option B — micromamba (complex bioinformatics)

```dockerfile
FROM mambaorg/micromamba:1.5-jammy

USER root

# System deps (headless support for GUI tools like FastQC/QUAST)
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget curl git procps \
    xvfb libgl1-mesa-glx libfontconfig1 \
    && rm -rf /var/lib/apt/lists/*

USER $MAMBA_USER

# Install all tools in ONE solver pass
RUN micromamba install -y -n base -c conda-forge -c bioconda \
    python=3.8 \
    pandas numpy \
    [CUSTOMIZE: add bioinformatics tools here] \
    && micromamba clean --all --yes

# If embedding wrapper scripts in container (is_stageable=False):
# USER root
# COPY bin/*.sh /usr/local/bin/
# RUN chmod +x /usr/local/bin/*.sh

ENV PATH="/opt/conda/bin:$PATH"
ENV LC_ALL=C.UTF-8
ENV LANG=C.UTF-8
ENV PYTHONUNBUFFERED=1

WORKDIR /data
ENTRYPOINT ["/usr/local/bin/_entrypoint.sh"]
CMD ["bash"]
```

### Option C — Ubuntu (apt + pip + manual installs)

```dockerfile
FROM ubuntu:22.04
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        python3 python3-pip wget curl git \
    && rm -rf /var/lib/apt/lists/*

RUN pip3 install --no-cache-dir \
    pandas numpy matplotlib requests

RUN ln -sf /usr/bin/python3 /usr/bin/python
ENV PYTHONUNBUFFERED=1

WORKDIR /app
CMD ["/bin/bash"]
```

## Step 3: Verify and Show Build Commands

After writing the file, verify it exists:

```bash
ls -la Docker/Dockerfile
```

Then show the user these commands:

```bash
# Build the image
docker build -t username/image:latest -f Docker/Dockerfile .

# Test interactively
docker run --rm -it username/image:latest bash

# Verify tools are installed
docker run --rm username/image:latest which tool1 tool2 tool3

# Push to Docker Hub
docker push username/image:latest
```

Also remind the user to update `workflow_generator.py`:
```python
container = Container(
    "my_container",
    container_type=Container.SINGULARITY,
    image="docker://username/image:latest",
    image_site="docker_hub",
)
```

## Key Rules

1. **All tools in ONE container**: Pegasus shares a single container across all jobs
2. **Pin versions**: `tool==1.2.3` (pip) or `tool=1.2.3` (conda) for reproducibility
3. **`PYTHONUNBUFFERED=1`**: Always set for real-time Pegasus log capture
4. **`--no-cache-dir` / `clean --all`**: Minimize image size
5. **Headless support**: If any tool uses Java GUI or matplotlib, add `xvfb`, `libgl1-mesa-glx`
6. **Embedded scripts**: If `is_stageable=False`, COPY and chmod +x the wrapper scripts
7. **No container mounts for data**: Do NOT use `mounts=[]` for caches/databases — use CondorIO `transfer_input_files` instead

Refer to `AGENTS.md` for Dockerfile patterns and container conventions.
