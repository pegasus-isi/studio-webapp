# Add Resources from Campus Clusters or Your Own Allocations

> Source of the instructions the Resources page shows on the hosted
> deployment (`studio-web/components/resources/annex-panel.tsx`, #532).
> Drafted by Mats Rynge, 2026-10-15. Keep the two in step.

If your workflows need more compute than Pegasus AI Studio provides, you can
add resources from any Slurm-based HPC cluster you have an account on (for
example, a campus cluster or an ACCESS allocation). HTCondor calls a set of
resources you lease this way an *annex*. Only your jobs run on your annex.

This page covers what's specific to Pegasus AI Studio. For full details, see
HTCondor's [Bringing Your Own Resources](https://htcondor.readthedocs.io/en/latest/users-manual/bringing-your-own-resources.html) guide.

## The key rule: name your annex `$USER`

All jobs on Pegasus AI Studio are automatically set to run on an annex named
after your username. This means:

- **Skip Step 1** of the HTCondor guide ("Submit the Job"). Your jobs already
  target your annex; submit workflows as you normally would.
- **Always name your annex `$USER`.** If you use any other name, your jobs
  will never be matched to the resources.
- **Submit your workflow first.** Annex nodes that find nothing to run shut
  themselves down after a few minutes.

## Steps

1. **Create the annex** on Pegasus AI Studio:

```
   htcondor annex create $USER
```

   This produces a tarfile, `annex-$USER.tar`, in your current directory.

2. **Copy the tarfile to the cluster:**

```
   scp annex-$USER.tar <login-node-of-your-cluster>:
```

3. **On the cluster, set up and submit the annex job:**

```
   tar xf annex-$USER.tar
   cd annex-$USER
   ./annex-setup.sh
```

   Edit the generated `hpc.slurm` file to set your partition, account,
   walltime, and node count, then submit it:

```
   sbatch hpc.slurm
```

4. **Check that the annex is up** (back on Pegasus AI Studio):

```
   htcondor annex status $USER
```

   Once Slurm starts your job, the nodes and CPUs will appear in the output and
   your queued jobs will begin running on them.

5. **Shut the annex down when you're done** to avoid using up your allocation:

```
   htcondor annex shutdown $USER
```

   Idle annex nodes also shut themselves down automatically after a few
   minutes, and every annex ends when its Slurm walltime expires.

> **Tip:** If you add annexes often, put your `#SBATCH` options in
> `~/.condor/annex_slurm_args` on the cluster so you don't have to edit
> `hpc.slurm` each time. See the HTCondor guide for details.

