import { describe, expect, it } from "vitest";

import { EXECUTION_SITES, siteChoices } from "./vocabulary";

describe("siteChoices", () => {
  it("falls back to the fixed pair on a backend without site discovery", () => {
    expect(siteChoices(undefined)).toBe(EXECUTION_SITES);
    expect(siteChoices([])).toBe(EXECUTION_SITES);
  });

  it("keeps the plain-language labels for local and Studio's own pool", () => {
    const choices = siteChoices([
      { name: "local", style: null, source: "default" },
      { name: "condorpool", style: "condor", source: "default" },
    ]);
    expect(choices.map((c) => [c.value, c.label])).toEqual([
      ["local", "This machine"],
      ["condorpool", "Studio’s own pool"],
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

  it("names another condor pool by its site, not as Studio's", () => {
    const choices = siteChoices([
      { name: "condorpool", style: "condor", source: "default" },
      { name: "gpu-pool", style: "condor", source: "project" },
    ]);
    expect(choices.map((c) => c.label)).toEqual([
      "Studio’s own pool",
      "gpu-pool",
    ]);
  });

  it("disambiguates two sites that would share a label", () => {
    const choices = siteChoices([
      { name: "compute", style: "slurm", source: "hosted" },
      { name: "hpc", style: "slurm", source: "project" },
    ]);
    expect(choices.map((c) => c.label)).toEqual([
      "HPC cluster (compute)",
      "HPC cluster (hpc)",
    ]);
  });

  it("labels a hosted catalog's site by its cluster, whatever is known of its scheduler", () => {
    const before = siteChoices([
      { name: "compute", style: null, source: "hosted", label: "Studio’s own pool" },
    ]);
    const after = siteChoices([
      { name: "compute", style: "condor", source: "hosted", label: "Studio’s own pool" },
    ]);
    expect(before[0].label).toBe("Studio’s own pool");
    expect(after[0].label).toBe("Studio’s own pool");
    expect(after[0].hint).toBe(EXECUTION_SITES[1].hint);

    const unity = siteChoices([
      { name: "compute", style: "slurm", source: "hosted", label: "Unity (UMass)" },
    ]);
    expect(unity[0]).toMatchObject({ value: "compute", label: "Unity (UMass)" });
    expect(unity[0].hint).toMatch(/batch scheduler/);
  });

  it("carries the backend's advanced flag through, and only that", () => {
    const choices = siteChoices([
      { name: "local", style: null, source: "default", advanced: false },
      { name: "condorpool", style: "condor", source: "project", advanced: true },
      { name: "compute", style: "condor", source: "hosted", label: "Studio’s own pool" },
    ]);
    expect(choices.map((c) => c.advanced ?? false)).toEqual([false, true, false]);
    // Demoted, not relabelled: it is still the pool it always was.
    expect(choices[1].label).toBe("Studio’s own pool (condorpool)");
    expect(choices[2].label).toBe("Studio’s own pool (compute)");
  });

  it("marks a cluster that still needs its allocation, and nothing else", () => {
    const choices = siteChoices([
      { name: "local", style: null, source: "default" },
      {
        name: "compute", style: "slurm", source: "hosted", label: "NERSC Perlmutter",
        configured: false, missing: ["Project / allocation to charge"],
      },
      { name: "hpc", style: "slurm", source: "deployment", configured: true },
    ]);
    expect(choices.map((c) => c.missing)).toEqual([
      undefined,
      ["Project / allocation to charge"],
      undefined,
    ]);
  });

  it("shows an unrecognised site by its name", () => {
    const [choice] = siteChoices([
      { name: "mysite", style: null, source: "project" },
    ]);
    expect(choice).toMatchObject({ value: "mysite", label: "mysite" });
  });
});
