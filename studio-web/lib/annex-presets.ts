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
  values: Pick<ClusterForm, "partition" | "cpus" | "memory" | "module" | "extra">;
  /** One line on what the preset chose and what else there is. */
  note: string;
  /** How sure we are: tested end to end with Studio, or from the sources. */
  basis: "tested" | "documented";
};

export const PRESETS: ClusterPreset[] = [
  {
    id: "expanse",
    label: "Expanse (SDSC)",
    values: { partition: "shared", cpus: "8", memory: "16G", module: "singularitypro", extra: "" },
    note: "shared gives part of a node; compute is a whole 128-core node. Expanse has no default partition.",
    basis: "tested",
  },
  {
    id: "anvil",
    label: "Anvil (Purdue)",
    values: { partition: "shared", cpus: "8", memory: "16G", module: "apptainer", extra: "" },
    note: "shared gives part of a node; wholenode and wide are whole 128-core nodes.",
    basis: "documented",
  },
  {
    id: "delta",
    label: "Delta (NCSA)",
    values: { partition: "cpu", cpus: "8", memory: "16G", module: "", extra: "--constraint=scratch" },
    note: "Apptainer is available without a module. The scratch constraint is what HTCondor's own Delta setup asks for.",
    basis: "documented",
  },
  {
    id: "bridges2",
    label: "Bridges-2 (PSC)",
    values: { partition: "RM-shared", cpus: "8", memory: "16G", module: "", extra: "" },
    note: "RM-shared gives part of a node; RM is a whole 128-core node. Singularity is in /usr/bin, so no module.",
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
