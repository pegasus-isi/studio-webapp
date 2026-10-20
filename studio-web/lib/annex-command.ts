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
 * - the account is asked for, and its absence is said plainly;
 * - walltime defaults to DEFAULT_WALLTIME and is always in the command:
 *   leaving -t out never helps (most clusters refuse the job, Expanse
 *   among them), and the grey "e.g." placeholder it used to have read
 *   as a value that was set when it was not;
 * - `--ntasks-per-node=1` is always set: hpc.slurm runs one annex-node.sh
 *   per task (srun), and one HTCondor worker per node is what an annex is;
 * - CPUs per node and memory are fields, since a shared partition hands
 *   out part of a node and the worker gets only what Slurm gives it;
 * - anything else a site wants goes in "extra";
 * - GPUs per node go out with the flag the cluster expects (#570):
 *   HTCondor's own per-system fragments use `--gpus` on Expanse and
 *   Bridges-2 and `--gpus-per-node` on Anvil and Delta; anything else gets
 *   `--gpus-per-node`, which plain Slurm understands. A typed request
 *   (Bridges-2's `v100-32:1`) passes through. Pegasus adds Singularity's
 *   --nv to any step that requests GPUs (PM-1621), so containers see them;
 * - a container module (e.g. `singularitypro` on Expanse) is written into
 *   ~/.condor/annex_config on the cluster, which hpc.slurm sources before
 *   it starts the nodes: it loads the module and hands its folder to every
 *   job through HTCondor's STARTER_JOB_ENVIRONMENT. Jobs on annex nodes
 *   do not see the cluster's modules, and Pegasus starts containers itself
 *   from the job's PATH. Kept on the cluster, so each cluster has its own;
 *   a Studio-wide PATH (what #558 did) is wrong on every other cluster.
 *   Verified on Expanse 2026-10-20: eht-m87 run0009's fetch_data found
 *   /cm/local/apps/singularitypro/3.11/bin/singularity with no PATH in its
 *   submit file (#564).
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
  module: string;
  /** GPUs per node: a count, or type:count (v100-32:1). */
  gpus: string;
  /** How this cluster spells it; set by a preset, never typed. */
  gpuFlag: string;
  /** The preset last applied (lib/annex-presets.ts), or "" for none. */
  cluster: string;
  /** "cpu" or "gpu" for that cluster (#578), or "" for none. */
  nodeType: string;
};

/** One hour: long enough for a test or a small run, short on an allocation. */
export const DEFAULT_WALLTIME = "01:00:00";

export const EMPTY_FORM: ClusterForm = {
  account: "",
  partition: "",
  walltime: DEFAULT_WALLTIME,
  nodes: "",
  cpus: "",
  memory: "",
  extra: "",
  module: "",
  gpus: "",
  gpuFlag: "",
  cluster: "",
  nodeType: "",
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
  // A module name, optionally with a version: singularitypro, apptainer/1.3.
  module: /^[A-Za-z0-9._+-]+(\/[A-Za-z0-9._+-]+)?$/,
  cluster: /^[a-z0-9-]+$/,
  nodeType: /^(cpu|gpu)$/,
  gpus: /^([A-Za-z0-9_.-]+:)?[0-9]{1,2}$/,
  gpuFlag: /^--gpus(-per-node)?$/,
};

export function valid(f: ClusterForm, k: keyof ClusterForm): string {
  const v = (f[k] ?? "").trim();
  return v && SHAPE[k].test(v) ? v : "";
}

/** A value was typed but cannot be used, so it is left out of the command. */
export function problem(f: ClusterForm, k: keyof ClusterForm): boolean {
  return !!(f[k] ?? "").trim() && !valid(f, k);
}

/**
 * Things that are valid Slurm but probably not what was meant, said
 * before the job is refused or cut short. Both happened on Expanse:
 * `-t 01:00` is one *minute* (two fields are minutes:seconds), and
 * Expanse has no default partition ("Invalid partition name specified").
 */
export function cautions(f: ClusterForm): string[] {
  const out: string[] = [];
  const t = valid(f, "walltime");
  if (t && /^[0-9]+:[0-9]+$/.test(t)) {
    const [m, sec] = t.split(":");
    out.push(
      `Walltime ${t} is ${Number(m)} minute${Number(m) === 1 ? "" : "s"} ${Number(sec)} seconds to Slurm ` +
        `(two fields are minutes:seconds). For hours, use hh:mm:ss, e.g. ${m}:00:00.`
    );
  }
  const partition = valid(f, "partition");
  if (!partition) {
    out.push("No partition: some clusters, Expanse among them, have no default and refuse the job.");
  }
  const gpuPartition = /gpu/i.test(partition);
  const gpus = valid(f, "gpus");
  if (gpuPartition && !gpus) {
    out.push(`${partition} is a GPU partition, but no GPUs are requested: the node would come without one.`);
  }
  if (gpus && partition && !gpuPartition) {
    out.push(`GPUs are requested on ${partition}, which does not look like a GPU partition; most clusters refuse that.`);
  }
  return out;
}

/** Fields most clusters insist on and the form cannot guess. */
export function missingRequired(f: ClusterForm): "account"[] {
  return valid(f, "account") ? [] : ["account"];
}

/** The #SBATCH lines for ~/.condor/annex_slurm_args. Never empty: the task
 *  count is always there. */
export function sbatchLines(f: ClusterForm): string[] {
  const parts: string[] = [];
  const account = valid(f, "account");
  const partition = valid(f, "partition");
  const walltime = valid(f, "walltime") || DEFAULT_WALLTIME;
  const nodes = valid(f, "nodes");
  const cpus = valid(f, "cpus");
  const memory = valid(f, "memory");
  if (account) parts.push(`-A ${account}`);
  if (partition) parts.push(`-p ${partition}`);
  parts.push(`-t ${walltime}`);
  parts.push(`--nodes=${nodes || "1"}`);
  parts.push("--ntasks-per-node=1");
  if (cpus) parts.push(`--cpus-per-task=${cpus}`);
  if (memory) parts.push(`--mem=${memory.toUpperCase()}`);
  const gpus = valid(f, "gpus");
  if (gpus) parts.push(`${valid(f, "gpuFlag") || "--gpus-per-node"}=${gpus}`);
  const lines = [`#SBATCH ${parts.join(" ")}`];
  const extra = valid(f, "extra");
  if (extra) lines.push(`#SBATCH ${extra.replace(/\s+/g, " ")}`);
  return lines;
}

/**
 * ~/.condor/annex_config for a container module, or "" without one. The
 * runtime's folder is looked up on the node at start-up, so a module that
 * puts it somewhere else next year still works; if neither apptainer nor
 * singularity is found, nothing is exported rather than a PATH of ".".
 */
export function annexConfig(f: ClusterForm): string {
  const mod = valid(f, "module");
  if (!mod) return "";
  return [
    `module load ${mod}`,
    'R=$(command -v apptainer || command -v singularity) && ' +
      'export _CONDOR_STARTER_JOB_ENVIRONMENT="PATH=${R%/*}:/usr/local/bin:/usr/bin:/bin"',
  ].join("\n");
}

/** The whole paste for the cluster's login node. */
export function clusterCommand(name: string, f: ClusterForm): string {
  const config = annexConfig(f);
  return (
    (config ? `mkdir -p ~/.condor && cat > ~/.condor/annex_config <<'ANNEX_CONFIG'\n${config}\nANNEX_CONFIG\n` : "") +
    `mkdir -p ~/.condor && cat > ~/.condor/annex_slurm_args <<'ANNEX_SLURM_ARGS'\n` +
    `${sbatchLines(f).join("\n")}\n` +
    `ANNEX_SLURM_ARGS\n` +
    // rm -f condor.tar.gz: annex-setup.sh links it and fails with "File
    // exists" when run again in the same folder (#584). Only that link goes;
    // the folder stays, since a running annex's pilot.<jobid> lives in it.
    `tar xf annex-${name}.tar && cd annex-${name} && rm -f condor.tar.gz && ./annex-setup.sh && sbatch hpc.slurm`
  );
}
