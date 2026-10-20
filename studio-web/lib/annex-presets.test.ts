import { describe, expect, it } from "vitest";
import { EMPTY_FORM, cautions, clusterCommand, sbatchLines } from "@/lib/annex-command";
import { CLUSTERS, SLURM_ACCOUNT_COMMAND, applyPreset, presetFor } from "@/lib/annex-presets";

describe("cluster presets", () => {
  it("Expanse, CPU only reproduces the options and module the live test ran with", () => {
    const f = applyPreset({ ...EMPTY_FORM, account: "TG-CIS240483" }, "expanse");
    expect(sbatchLines(f)[0]).toBe(
      "#SBATCH -A TG-CIS240483 -p shared -t 01:00:00 --nodes=1 --ntasks-per-node=1 --cpus-per-task=8 --mem=16G"
    );
    expect(clusterCommand("kthare10", f)).toContain("module load singularitypro");
    expect(cautions(f)).toEqual([]);
    expect(f.nodeType).toBe("cpu");
  });

  it("a new cluster starts on CPU only", () => {
    expect(applyPreset(EMPTY_FORM, "delta").nodeType).toBe("cpu");
  });

  it("keeps the account, walltime and nodes the user typed", () => {
    const f = applyPreset({ ...EMPTY_FORM, account: "abc", walltime: "02:00:00", nodes: "2" }, "anvil", "gpu");
    expect([f.account, f.walltime, f.nodes, f.partition, f.module]).toEqual(["abc", "02:00:00", "2", "gpu", "apptainer"]);
  });

  it("switching node type swaps partition, size and GPUs but keeps the cluster's module", () => {
    const cpu = applyPreset(EMPTY_FORM, "expanse", "cpu");
    const gpu = applyPreset(cpu, "expanse", "gpu");
    expect([gpu.partition, gpu.cpus, gpu.memory, gpu.gpus, gpu.module]).toEqual(["gpu-shared", "10", "92G", "1", "singularitypro"]);
    expect(applyPreset(gpu, "expanse", "cpu").gpus).toBe("");
  });

  it("GPU variants ask for GPUs with the cluster's own flag", () => {
    const line = (id: string) => sbatchLines(applyPreset(EMPTY_FORM, id, "gpu"))[0];
    expect(line("expanse")).toContain("--gpus=1");
    expect(line("delta")).toContain("--gpus-per-node=1");
    expect(line("bridges2")).toContain("--gpus=v100-32:1");
    expect(line("bridges2")).not.toContain("--cpus-per-task");
    expect(line("bridges2")).not.toContain("--mem");
  });

  it("follows HTCondor's rule that RM-shared takes no memory", () => {
    expect(sbatchLines(applyPreset(EMPTY_FORM, "bridges2"))[0]).not.toContain("--mem");
  });

  it("reads a form saved before the split (expanse-gpu)", () => {
    const p = presetFor("expanse-gpu");
    expect([p?.cluster.id, p?.nodeType]).toEqual(["expanse", "gpu"]);
  });

  it("every cluster and node type sets a partition and produces valid options", () => {
    for (const c of CLUSTERS) {
      for (const t of ["cpu", "gpu"] as const) {
        const f = applyPreset(EMPTY_FORM, c.id, t);
        expect(cautions(f)).toEqual([]);
        if (c.common.extra) expect(sbatchLines(f)[1]).toBe(`#SBATCH ${c.common.extra}`);
      }
    }
  });

  it("an unknown id clears the preset and leaves the values", () => {
    const f = applyPreset({ ...EMPTY_FORM, partition: "x", cluster: "expanse" }, "other");
    expect([f.cluster, f.nodeType, f.partition]).toEqual(["", "", "x"]);
  });

  it("every cluster says how to find the account, per ACCESS's guide where it names one", () => {
    const by = Object.fromEntries(CLUSTERS.map((c) => [c.id, c.accountCommand]));
    expect(by).toEqual({
      expanse: "module load sdsc; expanse-client user -p",
      anvil: "mybalance",
      delta: SLURM_ACCOUNT_COMMAND,
      bridges2: "projects",
    });
  });

  it("carries ACCESS's login notes where it gives one", () => {
    const by = Object.fromEntries(CLUSTERS.map((c) => [c.id, !!c.loginNote]));
    expect(by).toEqual({ expanse: true, anvil: false, delta: false, bridges2: true });
  });
});
