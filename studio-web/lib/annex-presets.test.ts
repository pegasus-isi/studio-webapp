import { describe, expect, it } from "vitest";
import { EMPTY_FORM, cautions, clusterCommand, sbatchLines } from "@/lib/annex-command";
import { PRESETS, applyPreset } from "@/lib/annex-presets";

describe("cluster presets", () => {
  it("Expanse reproduces the options and module the live test ran with", () => {
    const f = applyPreset({ ...EMPTY_FORM, account: "TG-CIS240483" }, "expanse");
    expect(sbatchLines(f)[0]).toBe(
      "#SBATCH -A TG-CIS240483 -p shared -t 01:00:00 --nodes=1 --ntasks-per-node=1 --cpus-per-task=8 --mem=16G"
    );
    expect(clusterCommand("kthare10", f)).toContain("module load singularitypro");
    expect(cautions(f)).toEqual([]);
  });

  it("keeps the account, walltime and nodes the user typed", () => {
    const f = applyPreset({ ...EMPTY_FORM, account: "abc", walltime: "02:00:00", nodes: "2" }, "anvil");
    expect([f.account, f.walltime, f.nodes, f.partition, f.module]).toEqual(["abc", "02:00:00", "2", "shared", "apptainer"]);
  });

  it("every preset sets a partition and produces valid options", () => {
    for (const p of PRESETS) {
      const f = applyPreset(EMPTY_FORM, p.id);
      expect(cautions(f)).toEqual([]);
      expect(sbatchLines(f).join("\n")).toContain(`-p ${p.values.partition}`);
      if (p.values.extra) expect(sbatchLines(f)[1]).toBe(`#SBATCH ${p.values.extra}`);
    }
  });

  it("an unknown id clears the preset and leaves the values", () => {
    const f = applyPreset({ ...EMPTY_FORM, partition: "x", cluster: "expanse" }, "other");
    expect(f.cluster).toBe("");
    expect(f.partition).toBe("x");
  });
});
