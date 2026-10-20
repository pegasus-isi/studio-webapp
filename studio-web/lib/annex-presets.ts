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

export type NodeType = "cpu" | "gpu";

type Variant = {
  values: Pick<ClusterForm, "partition" | "cpus" | "memory" | "gpus">;
  /** One line on what this variant chose and what else there is. */
  note: string;
  /** How sure we are: tested end to end with Studio, or from the sources. */
  basis: "tested" | "documented";
};

export type Cluster = {
  id: string;
  label: string;
  /** Filled whichever node type is chosen. GPU flags as HTCondor's own
   *  per-system fragments spell them (SYSTEM_SPECIFIC_GPU_FLAG). */
  common: Pick<ClusterForm, "module" | "extra" | "gpuFlag">;
  cpu: Variant;
  gpu?: Variant;
  /** How to find the Slurm account to put in -A, run on the cluster (#582).
   *  From ACCESS's HTCondor Annex guide (ACCESS_ANNEX_GUIDE) where it names
   *  one; otherwise plain Slurm's, which every Slurm cluster answers. */
  accountCommand: string;
  /** What logging in to the cluster takes, per ACCESS's guide, if notable. */
  loginNote?: string;
};

/** ACCESS's own HTCondor Annex guide: SSH keys, OnDemand, per-system notes. */
export const ACCESS_ANNEX_GUIDE =
  "https://access-ci.atlassian.net/wiki/spaces/ACCESSdocumentation/pages/564887666/HTCondor+Annex";

/** Any Slurm cluster: the accounts this user may charge. */
export const SLURM_ACCOUNT_COMMAND = "sacctmgr show assoc user=$USER format=account%30";

/** The view a caller needs of one cluster + node type. */
export type ClusterPreset = Variant & { cluster: Cluster; nodeType: NodeType };

/**
 * One entry per cluster, with a CPU and a GPU variant (#578). They were
 * separate entries ("Expanse", "Expanse, GPU", …), which doubled the menu
 * and mixed where the nodes come from with what kind of node they are.
 */
export const CLUSTERS: Cluster[] = [
  {
    id: "expanse",
    label: "Expanse (SDSC)",
    accountCommand: "module load sdsc; expanse-client user -p",
    loginNote: "SSH to Expanse needs MFA; see SDSC's user guide.",
    common: { module: "singularitypro", extra: "", gpuFlag: "--gpus" },
    cpu: {
      values: { partition: "shared", cpus: "8", memory: "16G", gpus: "" },
      note: "shared gives part of a node; compute is a whole 128-core node (the queue ACCESS's annex guide tested). Expanse has no default partition.",
      basis: "tested",
    },
    gpu: {
      values: { partition: "gpu-shared", cpus: "10", memory: "92G", gpus: "1" },
      note: "gpu-shared gives part of a 4×V100 node (40 cores); gpu is a whole one. Needs Expanse GPU time on the allocation (expanse-client user -r expanse_gpu).",
      basis: "documented",
    },
  },
  {
    id: "anvil",
    label: "Anvil (Purdue)",
    accountCommand: "mybalance",
    common: { module: "apptainer", extra: "", gpuFlag: "--gpus-per-node" },
    cpu: {
      values: { partition: "shared", cpus: "8", memory: "16G", gpus: "" },
      note: "shared gives part of a node; wholenode and wide are whole 128-core nodes. ACCESS's annex guide tested a queue it calls standard, which HTCondor's own table does not list.",
      basis: "documented",
    },
    gpu: {
      values: { partition: "gpu", cpus: "16", memory: "64G", gpus: "1" },
      note: "gpu gives part of a 4×A100 node (128 cores, 512 GB).",
      basis: "documented",
    },
  },
  {
    id: "delta",
    label: "Delta (NCSA)",
    // ACCESS's guide names no Delta command, and Delta's own docs are behind
    // a login; plain Slurm's answers there as everywhere.
    accountCommand: SLURM_ACCOUNT_COMMAND,
    common: { module: "", extra: "--constraint=scratch", gpuFlag: "--gpus-per-node" },
    cpu: {
      values: { partition: "cpu", cpus: "8", memory: "16G", gpus: "" },
      note: "Apptainer is available without a module. The scratch constraint is what HTCondor's own Delta setup asks for.",
      basis: "documented",
    },
    gpu: {
      values: { partition: "gpuA100x4", cpus: "16", memory: "64G", gpus: "1" },
      note: "gpuA100x4 gives part of a 4×A100 node (64 cores, 256 GB); gpuA40x4 and gpuA100x8 also exist.",
      basis: "documented",
    },
  },
  {
    id: "bridges2",
    label: "Bridges-2 (PSC)",
    accountCommand: "projects",
    loginNote: "Your password or SSH key has to be registered with PSC first; see the Bridges-2 user guide.",
    common: { module: "", extra: "", gpuFlag: "--gpus" },
    cpu: {
      values: { partition: "RM-shared", cpus: "8", memory: "", gpus: "" },
      note: "RM-shared gives part of a node and sets memory from the CPU count (HTCondor's table says not to give one); RM is a whole 128-core node (the queue ACCESS's annex guide tested). Singularity is in /usr/bin, so no module.",
      basis: "documented",
    },
    gpu: {
      values: { partition: "GPU-shared", cpus: "", memory: "", gpus: "v100-32:1" },
      note: "GPU-shared sets CPUs and memory from the GPU count, so leave both empty, and wants a GPU type (v100-16, v100-32).",
      basis: "documented",
    },
  },
];

export function clusterById(id: string): Cluster | undefined {
  return CLUSTERS.find((c) => c.id === id);
}

/** Saved forms from before #578 hold ids like "expanse-gpu". */
function split(id: string, nodeType?: NodeType): [string, NodeType] {
  if (id.endsWith("-gpu")) return [id.slice(0, -4), "gpu"];
  return [id, nodeType ?? "cpu"];
}

export function presetFor(id: string, nodeType?: NodeType): ClusterPreset | undefined {
  const [cid, type] = split(id, nodeType);
  const cluster = clusterById(cid);
  if (!cluster) return undefined;
  const variant = type === "gpu" && cluster.gpu ? cluster.gpu : cluster.cpu;
  return { ...variant, cluster, nodeType: type === "gpu" && cluster.gpu ? "gpu" : "cpu" };
}

/**
 * The form with a cluster and node type applied. A new cluster starts on
 * CPU only: GPU time is the scarcer and dearer allocation. Account,
 * walltime and nodes are never touched; "" or an unknown id clears the
 * preset and leaves the values.
 */
export function applyPreset(f: ClusterForm, id: string, nodeType: NodeType = "cpu"): ClusterForm {
  const p = presetFor(id, nodeType);
  if (!p) return { ...f, cluster: "", nodeType: "" };
  return { ...f, ...p.cluster.common, ...p.values, cluster: p.cluster.id, nodeType: p.nodeType };
}
