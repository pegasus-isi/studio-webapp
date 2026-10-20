import { describe, expect, it } from "vitest";
import { EMPTY_FORM, annexConfig, cautions, clusterCommand, missingRequired, problem, sbatchLines } from "@/lib/annex-command";

describe("gpu cautions", () => {
  it("warns about a GPU partition with no GPUs, and GPUs on a CPU partition", () => {
    expect(cautions({ ...EMPTY_FORM, partition: "gpu-shared" })[0]).toContain("no GPUs are requested");
    expect(cautions({ ...EMPTY_FORM, partition: "shared", gpus: "1" })[0]).toContain("does not look like a GPU partition");
    expect(cautions({ ...EMPTY_FORM, partition: "gpu-shared", gpus: "1" })).toEqual([]);
  });
  it("defaults to plain Slurm's --gpus-per-node without a preset", () => {
    expect(sbatchLines({ ...EMPTY_FORM, partition: "gpu", gpus: "2" })[0]).toContain("--gpus-per-node=2");
  });
});

describe("cautions", () => {
  it("says that 01:00 is one minute, as it was on Expanse", () => {
    const c = cautions({ ...EMPTY_FORM, walltime: "01:00", partition: "shared" });
    expect(c).toHaveLength(1);
    expect(c[0]).toContain("1 minute 0 seconds");
    expect(c[0]).toContain("01:00:00");
  });
  it("warns about a missing partition and is quiet when both are fine", () => {
    expect(cautions(EMPTY_FORM)[0]).toContain("No partition");
    expect(cautions({ ...EMPTY_FORM, partition: "shared" })).toEqual([]);
  });
});

describe("sbatchLines", () => {
  it("always asks for one task per node, the one thing every annex needs", () => {
    expect(sbatchLines(EMPTY_FORM)).toEqual(["#SBATCH -t 01:00:00 --nodes=1 --ntasks-per-node=1"]);
  });

  it("produces the options Expanse accepted in the first live test", () => {
    const f = { ...EMPTY_FORM, account: "nca128", partition: "shared", walltime: "00:30:00", cpus: "8", memory: "16g" };
    expect(sbatchLines(f)).toEqual([
      "#SBATCH -A nca128 -p shared -t 00:30:00 --nodes=1 --ntasks-per-node=1 --cpus-per-task=8 --mem=16G",
    ]);
  });

  it("adds extra options on their own line", () => {
    const f = { ...EMPTY_FORM, extra: "--qos=normal  --constraint=lustre" };
    expect(sbatchLines(f)[1]).toBe("#SBATCH --qos=normal --constraint=lustre");
  });

  it("leaves out a value that would break the heredoc", () => {
    const f = { ...EMPTY_FORM, account: "abc\nANNEX_SLURM_ARGS", extra: "--x=1; rm -rf ~" };
    expect(problem(f, "account")).toBe(true);
    expect(problem(f, "extra")).toBe(true);
    expect(sbatchLines(f).join("\n")).not.toContain("ANNEX_SLURM_ARGS");
    expect(sbatchLines(f)).toHaveLength(1);
  });
});

describe("missingRequired", () => {
  it("names the account; walltime has a default", () => {
    expect(missingRequired(EMPTY_FORM)).toEqual(["account"]);
    expect(missingRequired({ ...EMPTY_FORM, account: "nca128" })).toEqual([]);
  });
});

describe("walltime", () => {
  it("is always in the command, falling back to the default when cleared or invalid", () => {
    for (const walltime of ["", "soon"]) {
      expect(sbatchLines({ ...EMPTY_FORM, account: "TG-CIS240483", walltime })[0]).toBe(
        "#SBATCH -A TG-CIS240483 -t 01:00:00 --nodes=1 --ntasks-per-node=1"
      );
    }
    expect(sbatchLines({ ...EMPTY_FORM, walltime: "04:00:00" })[0]).toContain("-t 04:00:00");
  });
});

describe("annexConfig", () => {
  it("loads the module and hands its runtime folder to every job, as verified on Expanse", () => {
    const cfg = annexConfig({ ...EMPTY_FORM, module: "singularitypro" });
    expect(cfg.split("\n")[0]).toBe("module load singularitypro");
    expect(cfg).toContain('_CONDOR_STARTER_JOB_ENVIRONMENT="PATH=${R%/*}:/usr/local/bin:/usr/bin:/bin"');
    expect(cfg).toContain("command -v apptainer || command -v singularity");
  });
  it("is empty without a module, and refuses one that is not a module name", () => {
    expect(annexConfig(EMPTY_FORM)).toBe("");
    expect(annexConfig({ ...EMPTY_FORM, module: "x; rm -rf ~" })).toBe("");
    expect(annexConfig({ ...EMPTY_FORM, module: "apptainer/1.3.4" })).toContain("module load apptainer/1.3.4");
  });
  it("is written before the Slurm options when set", () => {
    const cmd = clusterCommand("k", { ...EMPTY_FORM, module: "singularitypro" });
    expect(cmd.indexOf("annex_config")).toBeLessThan(cmd.indexOf("annex_slurm_args"));
    expect(clusterCommand("k", EMPTY_FORM)).not.toContain("annex_config");
  });
});

describe("clusterCommand", () => {
  it("is always ready to run: no 'edit hpc.slurm' fallback", () => {
    const cmd = clusterCommand("kthare10", EMPTY_FORM);
    expect(cmd).toContain("./annex-setup.sh && sbatch hpc.slurm");
    // Rerunning in an existing folder must not trip over setup's own link.
    expect(cmd).toContain("cd annex-kthare10 && rm -f condor.tar.gz && ./annex-setup.sh");
    expect(cmd).not.toContain("rm -rf");
    expect(cmd).not.toContain("edit hpc.slurm");
    expect(cmd.startsWith("mkdir -p ~/.condor && cat > ~/.condor/annex_slurm_args <<'ANNEX_SLURM_ARGS'")).toBe(true);
  });
});
