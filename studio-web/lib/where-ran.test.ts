import { describe, expect, it } from "vitest";
import { placeOf, whereRan } from "@/lib/where-ran";

describe("placeOf", () => {
  it("puts Pegasus's local jobs on the access point", () => {
    expect(placeOf("ap.pegasus-ai.org", "local").kind).toBe("access-point");
  });
  it("treats a bare pod name as Studio's pool", () => {
    expect(placeOf("ep-67bb7f5dd-bk6kw", "compute").label).toBe("Studio pool");
  });
  it("groups other hosts by domain", () => {
    expect(placeOf("exp-1-19.expanse.sdsc.edu", "compute").label).toBe("expanse.sdsc.edu");
  });
});

describe("whereRan", () => {
  it("summarises the Expanse test run: 31 steps on two nodes, bookkeeping last", () => {
    const jobs = [
      ...Array.from({ length: 29 }, () => ({ host: "exp-1-19.expanse.sdsc.edu", site: "compute" })),
      ...Array.from({ length: 2 }, () => ({ host: "exp-1-17.expanse.sdsc.edu", site: "compute" })),
      ...Array.from({ length: 24 }, () => ({ host: "ap.pegasus-ai.org", site: "local" })),
      { host: null, site: "compute" },
    ];
    const g = whereRan(jobs);
    expect(g.map((x) => [x.label, x.steps])).toEqual([
      ["expanse.sdsc.edu", 31],
      ["Access point", 24],
    ]);
    expect(g[0].hosts).toEqual(["exp-1-19.expanse.sdsc.edu", "exp-1-17.expanse.sdsc.edu"]);
  });
  it("ignores jobs that have not run anywhere yet", () => {
    expect(whereRan([{ host: undefined }, { host: "" }])).toEqual([]);
  });
});
