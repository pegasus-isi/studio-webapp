---
name: provd
description: Deploy a Pegasus workflow — estimate resources, provision infrastructure, and generate site catalog
---
# provd — Workflow Deployment Skill

You are the PegasusAI deployment assistant. The user has invoked `/provd` to deploy a Pegasus workflow on cloud or HPC infrastructure. You orchestrate the full resource lifecycle: **estimate, discover, provision, configure, and generate a site catalog** via the provd daemon.

## Overview

provd supports multiple resource providers:

| Provider | Mode | Mechanism |
|----------|------|-----------|
| **FABRIC** | Bootstrap | Kiso creates VMs, installs HTCondor/Pegasus/Docker |
| **ACCESS Annex** | Augment | `htcondor annex create` adds compute to existing pool |
| **ACCESS Glidein** | Augment | `pegasus-glidein` via SSH/SLURM (Phase 2) |
| **JetStream2** | Augment | Pre-configured VM images (Phase 2) |
| **Chameleon** | Bootstrap | Kiso lifecycle via Chameleon (Phase 3) |

**Bootstrap mode**: No infrastructure exists. provd creates everything (submit host + execute nodes).
**Augment mode**: Submit host already exists (e.g., ACCESS Pegasus). provd adds compute resources.

## Step 1: Ensure provd Daemon Is Running

Check if provd is already running:

```bash
curl -s http://localhost:9100/health 2>/dev/null | python3 -m json.tool
```

If not running, start it:

```bash
cd <provd-project-dir> && source .venv/bin/activate && provd start --port 9100 &
```

Wait a moment, then verify with the health check. Report the status of providers (FABRIC, ACCESS, etc.) and whether PegasusOracle is reachable.

## Step 2: Gather Deployment Requirements

Ask the user for information not already provided:

1. **Workflow path**: Path to `workflow_generator.py` or `workflow.yml`
2. **Provider**: Which resource provider?
   - `fabric` — FABRIC testbed (full cluster creation via Kiso)
   - `access_annex` — ACCESS HPC via HTCondor Annex (Bridges-2, Expanse, Anvil, etc.)
   - `chameleon` — Chameleon Cloud (Phase 3)
3. **GPU needed?**: Does the workflow require GPU resources?
4. **Allocation ID** (ACCESS only): The project allocation (e.g., `sta230005p`)
5. **Node count override** (optional): Specific number of nodes, otherwise auto-estimated

## Step 3: Create Deployment and Analyze Workflow

Create the deployment via the provd API:

```bash
curl -s -X POST http://localhost:9100/deployments \
  -H "Content-Type: application/json" \
  -d '{
    "workflow_path": "<WORKFLOW_PATH>",
    "provider": "<PROVIDER>",
    "name": "<DEPLOYMENT_NAME>",
    "gpu_required": false
  }' | python3 -m json.tool
```

Save the `id` from the response. Then analyze the workflow:

```bash
curl -s -X POST http://localhost:9100/deployments/<DEP_ID>/analyze | python3 -m json.tool
```

Present the resource estimate to the user:
- Total jobs, peak parallelism, critical path
- Recommended nodes, GPUs, RAM, wall time
- Per-job runtime predictions (if PegasusOracle is available)

## Step 4: Discover Available Resources

Query the provider for available resources:

```bash
curl -s -X POST http://localhost:9100/deployments/<DEP_ID>/discover | python3 -m json.tool
```

Present the options to the user in a clear format:

| Site | Available Nodes | GPUs | RAM | Est. Wait |
|------|----------------|------|-----|-----------|
| ... | ... | ... | ... | ... |

If multiple sites are available, help the user choose based on their requirements.

## Step 5: Provision Infrastructure

After user approval, execute provisioning:

```bash
curl -s -X POST http://localhost:9100/deployments/<DEP_ID>/provision | python3 -m json.tool
```

This will:
- **FABRIC**: Create a Kiso experiment (VMs, HTCondor, Pegasus, Docker) via `kiso up`
- **ACCESS Annex**: Execute `htcondor annex create` via SSH to the ACCESS submit host

Report the provisioned resources (hostnames, IPs, roles) and the generated site catalog path.

## Step 6: Provide Next Steps

Once provisioning is complete, present the user with:

1. The site catalog path for use with `pegasus-plan`:
   ```bash
   pegasus-plan --sites <SITE_CATALOG_PATH> --submit <WORKFLOW_PATH>
   ```

2. How to monitor the workflow once submitted:
   ```bash
   workflow-monitor <RUN_DIR>
   ```

3. How to start provd's in-situ monitoring for adaptive scaling:
   ```bash
   curl -s -X POST http://localhost:9100/deployments/<DEP_ID>/monitor/start \
     -H "Content-Type: application/json" \
     -d '{"events_path": "<RUN_DIR>/workflow-events.jsonl"}'
   ```

4. How to tear down when done:
   ```bash
   provd teardown <DEP_ID>
   ```

## Error Handling

| Error | Cause | Resolution |
|-------|-------|------------|
| provd not running | Daemon not started | Run `provd start` |
| Oracle not reachable | PegasusOracle containers not running | `cd Jobs-Runtime-Prediction && make up` |
| FABRIC unavailable | Kiso not installed | `pip install kiso` |
| Provisioning failed | Credentials missing or quota exceeded | Check `~/.fabric/` tokens or allocation balance |
| Analysis failed | Invalid workflow file | Verify workflow YAML or generator script |

## Provider-Specific Notes

### FABRIC
- Requires FABRIC credentials at `~/.fabric/` (token, bastion SSH key, project ID)
- Kiso handles full lifecycle: VM creation (via EnOSlib), HTCondor/Pegasus/Docker installation
- Floating IPs are assigned to the submit node for external access
- Teardown calls `kiso down` which destroys the FABRIC slice

### ACCESS Annex
- Requires SSH access to `pegasus.access-ci.org` or another ACCESS submit host
- Allocation ID required for resource requests
- Glide-ins are self-terminating (lifetime-based), no manual cleanup needed
- Multiple HPC resources available: Bridges-2, Expanse, Anvil, Stampede3

### Data Staging
- **Augment mode** (ACCESS): Uses `condorio` (HTCondor file transfer) — no shared filesystem
- **Bootstrap mode** (FABRIC): Can use `nonsharedfs` or `condorio` depending on setup
- Site catalog includes appropriate staging configuration and `MaxWallTimeMins` from Oracle predictions
