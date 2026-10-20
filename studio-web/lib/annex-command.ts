/**
 * The cluster side of an annex: the Slurm options form and the one command
 * the user pastes on the cluster (#534, reworked after the first live test
 * on SDSC Expanse, #558).
 *
 * annex-setup.sh writes hpc.slurm and appends ~/.condor/annex_slurm_args to
 * it, so the command writes that file first and needs no hpc.slurm edit.
 * Expanse refused the job three times before it ran: no time limit, no
 * task count (its bank_limit plugin), then no memory on the shared
 * partition. So:
 *
 * - account and walltime are asked for, and their absence is said plainly;
 * - `--ntasks-per-node=1` is always set: hpc.slurm runs one annex-node.sh
 *   per task (srun), and one HTCondor worker per node is what an annex is;
 * - CPUs per node and memory are fields, since a shared partition hands
 *   out part of a node and the worker gets only what Slurm gives it;
 * - anything else a site wants goes in "extra".
 *
 * Every value is the user's own and lands in their own shell, so the
 * shapes below are not a security boundary; they keep a stray newline or
 * quote (typed, or left in storage) from breaking the heredoc.
 */

export type ClusterForm = {
  account: string;
  partition: string;
  walltime: string;
  nodes: string;
  cpus: string;
  memory: string;
  extra: string;
};

export const EMPTY_FORM: ClusterForm = {
  account: "",
  partition: "",
  walltime: "",
  nodes: "",
  cpus: "",
  memory: "",
  extra: "",
};

const SHAPE: Record<keyof ClusterForm, RegExp> = {
  account: /^[A-Za-z0-9._-]+$/,
  partition: /^[A-Za-z0-9._,-]+$/,
  walltime: /^[0-9]{1,3}(-[0-9]{1,2})?(:[0-9]{1,2}){0,2}$/,
  nodes: /^[0-9]{1,5}$/,
  cpus: /^[0-9]{1,4}$/,
  memory: /^[0-9]{1,7}[KMGT]?$/i,
  // Options only: dashes, words, =, commas, colons, dots, slashes, spaces.
  extra: /^(\s*--?[A-Za-z][A-Za-z0-9-]*(=[A-Za-z0-9._,:/+-]+)?)+\s*$/,
};

export function valid(f: ClusterForm, k: keyof ClusterForm): string {
  const v = (f[k] ?? "").trim();
  return v && SHAPE[k].test(v) ? v : "";
}

/** A value was typed but cannot be used, so it is left out of the command. */
export function problem(f: ClusterForm, k: keyof ClusterForm): boolean {
  return !!(f[k] ?? "").trim() && !valid(f, k);
}

/** Fields most clusters insist on and the form cannot guess. */
export function missingRequired(f: ClusterForm): ("account" | "walltime")[] {
  return (["account", "walltime"] as const).filter((k) => !valid(f, k));
}

/** The #SBATCH lines for ~/.condor/annex_slurm_args. Never empty: the task
 *  count is always there. */
export function sbatchLines(f: ClusterForm): string[] {
  const parts: string[] = [];
  const account = valid(f, "account");
  const partition = valid(f, "partition");
  const walltime = valid(f, "walltime");
  const nodes = valid(f, "nodes");
  const cpus = valid(f, "cpus");
  const memory = valid(f, "memory");
  if (account) parts.push(`-A ${account}`);
  if (partition) parts.push(`-p ${partition}`);
  if (walltime) parts.push(`-t ${walltime}`);
  parts.push(`--nodes=${nodes || "1"}`);
  parts.push("--ntasks-per-node=1");
  if (cpus) parts.push(`--cpus-per-task=${cpus}`);
  if (memory) parts.push(`--mem=${memory.toUpperCase()}`);
  const lines = [`#SBATCH ${parts.join(" ")}`];
  const extra = valid(f, "extra");
  if (extra) lines.push(`#SBATCH ${extra.replace(/\s+/g, " ")}`);
  return lines;
}

/** The whole paste for the cluster's login node. */
export function clusterCommand(name: string, f: ClusterForm): string {
  return (
    `mkdir -p ~/.condor && cat > ~/.condor/annex_slurm_args <<'ANNEX_SLURM_ARGS'\n` +
    `${sbatchLines(f).join("\n")}\n` +
    `ANNEX_SLURM_ARGS\n` +
    `tar xf annex-${name}.tar && cd annex-${name} && ./annex-setup.sh && sbatch hpc.slurm`
  );
}
