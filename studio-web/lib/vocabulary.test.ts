import { describe, expect, it } from "vitest";

import { EXECUTION_SITES, siteChoices } from "./vocabulary";

describe("siteChoices", () => {
  it("falls back to the fixed pair on a backend without site discovery", () => {
    expect(siteChoices(undefined)).toBe(EXECUTION_SITES);
    expect(siteChoices([])).toBe(EXECUTION_SITES);
  });

  it("keeps the plain-language labels for local and a condor pool", () => {
    const choices = siteChoices([
      { name: "local", style: null, source: "default" },
      { name: "condorpool", style: "condor", source: "default" },
    ]);
    expect(choices.map((c) => [c.value, c.label])).toEqual([
      ["local", "This machine"],
      ["condorpool", "Shared cluster"],
    ]);
  });

  it("calls a Slurm site, or an unknown hosted one, an HPC cluster", () => {
    const choices = siteChoices([
      { name: "local", style: null, source: "default" },
      { name: "compute", style: null, source: "hosted" },
    ]);
    expect(choices[1]).toMatchObject({ value: "compute", label: "HPC cluster" });

    const slurm = siteChoices([
      { name: "hpc", style: "slurm", source: "deployment" },
    ]);
    expect(slurm[0].label).toBe("HPC cluster");
  });

  it("disambiguates two sites that would share a label", () => {
    const choices = siteChoices([
      { name: "condorpool", style: "condor", source: "default" },
      { name: "gpu-pool", style: "condor", source: "project" },
    ]);
    expect(choices.map((c) => c.label)).toEqual([
      "Shared cluster (condorpool)",
      "Shared cluster (gpu-pool)",
    ]);
  });

  it("shows an unrecognised site by its name", () => {
    const [choice] = siteChoices([
      { name: "mysite", style: null, source: "project" },
    ]);
    expect(choice).toMatchObject({ value: "mysite", label: "mysite" });
  });
});
