/**
 * Cluster presets for the annex Slurm form (#566).
 *
 * The values come from HTCondor's own annex table (htcondor_cli/
 * annex_validate.py SYSTEM_TABLE and the per-system *.fragment files in
 * /usr/libexec/condor/annex, as shipped in the Studio image, HTCondor
 * 25.14.1) and the clusters' documentation. Each picks the cluster's
 * partial-node ("cores_or_ram") queue so a first test does not spend a
 * whole node, with 8 CPUs and 16G. The account and walltime stay the
 * user's: no preset can know them.
 *
 * Perlmutter is not here: it selects by QOS and constraint rather than
 * partition, and its Apptainer is a CVMFS path, not a module, so the form
 * does not fit it.
 */

import type { ClusterForm } from "@/lib/annex-command";

export type ClusterPreset = {
  id: string;
  label: string;
  values: Pick<ClusterForm, "partition" | "cpus" | "memory" | "module" | "extra" | "gpus" | "gpuFlag">;
  /** One line on what the preset chose and what else there is. */
  note: string;
  /** How sure we are: tested end to end with Studio, or from the sources. */
  basis: "tested" | "documented";
};

// GPU flags as HTCondor's own per-system fragments spell them
// (SYSTEM_SPECIFIC_GPU_FLAG in /usr/libexec/condor/annex/*.fragment).
const EXPANSE = { module: "singularitypro", extra: "", gpuFlag: "--gpus" };
const ANVIL = { module: "apptainer", extra: "", gpuFlag: "--gpus-per-node" };
const DELTA = { module: "", extra: "--constraint=scratch", gpuFlag: "--gpus-per-node" };
const BRIDGES2 = { module: "", extra: "", gpuFlag: "--gpus" };

export const PRESETS: ClusterPreset[] = [
  {
    id: "expanse",
    label: "Expanse (SDSC)",
    values: { ...EXPANSE, partition: "shared", cpus: "8", memory: "16G", gpus: "" },
    note: "shared gives part of a node; compute is a whole 128-core node. Expanse has no default partition.",
    basis: "tested",
  },
  {
    id: "expanse-gpu",
    label: "Expanse (SDSC), GPU",
    values: { ...EXPANSE, partition: "gpu-shared", cpus: "10", memory: "92G", gpus: "1" },
    note: "gpu-shared gives part of a 4×V100 node (40 cores); gpu is a whole one. Needs Expanse GPU time on the allocation (expanse-client user -r expanse_gpu).",
    basis: "documented",
  },
  {
    id: "anvil",
    label: "Anvil (Purdue)",
    values: { ...ANVIL, partition: "shared", cpus: "8", memory: "16G", gpus: "" },
    note: "shared gives part of a node; wholenode and wide are whole 128-core nodes.",
    basis: "documented",
  },
  {
    id: "anvil-gpu",
    label: "Anvil (Purdue), GPU",
    values: { ...ANVIL, partition: "gpu", cpus: "16", memory: "64G", gpus: "1" },
    note: "gpu gives part of a 4×A100 node (128 cores, 512 GB).",
    basis: "documented",
  },
  {
    id: "delta",
    label: "Delta (NCSA)",
    values: { ...DELTA, partition: "cpu", cpus: "8", memory: "16G", gpus: "" },
    note: "Apptainer is available without a module. The scratch constraint is what HTCondor's own Delta setup asks for.",
    basis: "documented",
  },
  {
    id: "delta-gpu",
    label: "Delta (NCSA), GPU",
    values: { ...DELTA, partition: "gpuA100x4", cpus: "16", memory: "64G", gpus: "1" },
    note: "gpuA100x4 gives part of a 4×A100 node (64 cores, 256 GB); gpuA40x4 and gpuA100x8 also exist.",
    basis: "documented",
  },
  {
    id: "bridges2",
    label: "Bridges-2 (PSC)",
    values: { ...BRIDGES2, partition: "RM-shared", cpus: "8", memory: "", gpus: "" },
    note: "RM-shared gives part of a node and sets memory from the CPU count (HTCondor's table says not to give one); RM is a whole 128-core node. Singularity is in /usr/bin, so no module.",
    basis: "documented",
  },
  {
    id: "bridges2-gpu",
    label: "Bridges-2 (PSC), GPU",
    values: { ...BRIDGES2, partition: "GPU-shared", cpus: "", memory: "", gpus: "v100-32:1" },
    note: "GPU-shared sets CPUs and memory from the GPU count, so leave both empty, and wants a GPU type (v100-16, v100-32).",
    basis: "documented",
  },
];

export function presetById(id: string): ClusterPreset | undefined {
  return PRESETS.find((p) => p.id === id);
}

/** The form with a preset's values applied; account, walltime, nodes untouched. */
export function applyPreset(f: ClusterForm, id: string): ClusterForm {
  const p = presetById(id);
  if (!p) return { ...f, cluster: "" };
  return { ...f, ...p.values, cluster: id };
}
