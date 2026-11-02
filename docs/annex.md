# Bring Your Own Compute: Nodes from Your Own Cluster or Allocation

> **Status: shipped, hosted deployment only.** This page is the long form of
> **Resources → Bring Your Own Compute** (`studio-web/components/resources/annex-panel.tsx`,
> backed by `studio-api/services/annex.py`) and of the **Bring Your Own
> Compute** choice under **Where Workflow Runs** in a workflow's Run panel
> (`studio-web/components/workflows/run-flow-panel.tsx`,
> `studio-api/services/run_placement.py`). Keep the three in step.
> First drafted by Mats Rynge on 2026-10-15 for the command-line flow; rewritten
> 2026-11-02 for the page as it now stands (#532, #534, #544, #558, #566,
> #570, #576, #578, #582, #584, #612, #627). Under Open OnDemand there is no
> annex: the cluster is the install, and the same Resources section is
> called "Where workflows run".

If your workflows need more compute than Pegasus AI Studio provides, or
hardware it does not have (GPUs, for example), you can bring nodes from any
Slurm cluster you have an account on: a campus cluster or an ACCESS
allocation. The nodes join Studio's HTCondor access point and run only your
work. HTCondor calls a set of nodes leased this way an *annex*, which is the
word you will still see in its commands.

For the HTCondor side in full, see HTCondor's
[Bringing Your Own Resources](https://htcondor.readthedocs.io/en/latest/users-manual/bringing-your-own-resources.html)
guide. ACCESS keeps its own
[HTCondor Annex guide](https://access-ci.atlassian.net/wiki/spaces/ACCESSdocumentation/pages/564887666/HTCondor+Annex)
covering SSH keys and each cluster's OnDemand portal.

## How a run chooses your nodes

Each workflow's Run panel asks **Where Workflow Runs**, with two choices:

- **Studio's Compute Resources** (the default for every workflow): Studio's
  shared pool, plus the OSPool for steps that carry a project name. A run
  left here never uses your nodes.
- **Bring Your Own Compute**: only the nodes you connect. Steps wait until
  your nodes join, so start the run first, then the nodes.

The choice is per workflow and is saved in the workflow's own
`pegasus.properties` as a site requirements profile, so the Run button, a
`pegasus-plan` typed in a terminal, and one run by the assistant all plan the
same thing:

```
pegasus.catalog.site.sites.<site>.profiles.condor.requirements = TARGET.AnnexName =?= MY.Owner
```

The line holds no username, so it works for anyone who clones the workflow.
The default needs no line: Studio keeps
`TARGET.AnnexName =?= undefined` for the hosted sites (`compute`,
`condorpool`) in `~/.pegasusrc`, and a project's `pegasus.properties`
outranks it. A requirements line Studio did not write is left alone and
shown as set by the workflow. Studio re-applies its line after Generate.

Older versions kept a standing "only my annex" setting in `~/.pegasusrc`
for every run. If one is still there, both the Run panel and the Resources
page offer to remove it.

### GPUs

A step gets a GPU only if the workflow asks for one, per job or per
transformation, with a Pegasus `gpus` or HTCondor `request_gpus` profile.
The Run panel reads the generated workflow for such steps
(`studio-api/services/workflow_gpus.py`) and says when no connected machine
has a GPU to give them. Studio does not add GPUs to a run on its own.

## The key rule: the annex is named after you

The access point names every job's annex after its owner (a job transform in
`docker/htcondor/90-studio.conf` sets `TargetAnnexName` to the submitting
user), so your annex must be named with your Studio username. The page
fills the name in everywhere for you. If you work from a terminal instead,
always use `htcondor annex create $USER`; any other name is never matched.

## Connecting your nodes (Resources → Bring Your Own Compute)

The section shows your nodes' status (nodes, CPUs and GPUs busy and total,
requests waiting, up and done, your steps running and queued) and a
five-step checklist that ticks itself off as Studio sees each step happen.

1. **Run a workflow** set to **Bring Your Own Compute**. HTCondor brings your
   nodes in only while some of your steps are queued, and `htcondor annex
   create` refuses to run with nothing queued, so the next button stays
   disabled (and says why) until a run is going. A node with nothing to run
   leaves after 5 minutes.

2. **Create and download your setup file.** The button runs
   `htcondor annex create <you>` and keeps the tarball under `~/.studio/annex`;
   the page then offers it as a download. It holds a token that lets nodes
   join as you for 90 days, so keep it to yourself. If the request that file
   belongs to has since been closed, the page says so and asks you to make a
   new one.

3. **Copy it to your home directory on the cluster**, with whatever you
   normally use: the cluster's web portal, `scp` from your laptop, or Globus.
   Studio never copies it for you (no SSH from the pod, and most clusters
   require MFA).

4. **Log in to the cluster and paste one command.** Fill in the Slurm
   options form and copy the generated command. It:
   - writes your `#SBATCH` options to `~/.condor/annex_slurm_args` (and, if
     you gave a container module, `~/.condor/annex_config`) on the cluster,
     so every later setup reads them and you never edit `hpc.slurm`;
   - unpacks the setup file, runs `./annex-setup.sh` and submits
     `hpc.slurm` with `sbatch`. It is safe to paste again in the same folder.

   The form remembers your values in this browser only.

   - **Cluster**: presets for Expanse (SDSC), Anvil (Purdue), Delta (NCSA)
     and Bridges-2 (PSC), or **Other** to fill everything in yourself. Each
     preset has a **CPU only** and a **With GPUs** node type and picks the
     cluster's partial-node queue, so a first test does not spend a whole
     node. The page says which presets were tested end to end with Studio
     (Expanse, CPU) and which come from HTCondor's annex settings and the
     cluster's documentation. Perlmutter is not offered: it selects by QOS
     and constraint rather than partition. Presets live in
     `studio-web/lib/annex-presets.ts`.
   - **Account / allocation** (required): the Slurm account on the cluster,
     which may differ from your ACCESS allocation id (`TG-ABC123456` can be
     `abc123` there). The **?** next to it gives the command that lists your
     accounts on the chosen cluster and any login notes from ACCESS's guide
     (for example, Expanse needs MFA; Bridges-2 needs your password or SSH
     key registered with PSC first).
   - **Walltime** (defaults to one hour), **Partition**, **Nodes**,
     **CPUs per node**, **GPUs per node** (a count or a typed request such
     as `v100-32:1`), **Memory per node**, **Container module** (only for
     workflows that run in containers: jobs on your nodes do not see the
     cluster's modules), and **Other sbatch options**. The page warns about
     a missing account or partition and an MM:SS walltime, and leaves out
     any value with spaces or punctuation.

5. **Wait for the nodes to join.** The page refreshes on its own. Once the
   cluster's scheduler starts the job, the nodes appear in the status above
   and your waiting steps start on them.

## When you are done

Press **Release my nodes** (and confirm). Studio runs
`htcondor annex shutdown <you>` on the access point; the nodes leave within a
minute or so and the Slurm job ends with them. Idle nodes also leave on
their own after 5 minutes, and every annex ends when its Slurm walltime
expires. Leftover requests that no node is left to end (retired or held) can
be removed with **Close old requests**.

From a terminal, `htcondor annex status $USER` reports the same thing the
page shows.
