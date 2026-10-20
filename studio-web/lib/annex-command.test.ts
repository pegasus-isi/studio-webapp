import { describe, expect, it } from "vitest";
import { EMPTY_FORM, clusterCommand, missingRequired, problem, sbatchLines } from "@/lib/annex-command";

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

describe("clusterCommand", () => {
  it("is always ready to run: no 'edit hpc.slurm' fallback", () => {
    const cmd = clusterCommand("kthare10", EMPTY_FORM);
    expect(cmd).toContain("./annex-setup.sh && sbatch hpc.slurm");
    expect(cmd).not.toContain("edit hpc.slurm");
    expect(cmd.startsWith("mkdir -p ~/.condor && cat > ~/.condor/annex_slurm_args <<'ANNEX_SLURM_ARGS'")).toBe(true);
  });
});
