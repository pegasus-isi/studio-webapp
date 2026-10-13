import { describe, expect, it } from "vitest";
import { newestFirst } from "./run-order";

const names = (runs: { run_name: string }[]) => runs.map((r) => r.run_name);

describe("newestFirst (#502)", () => {
  it("puts the most recently planned run first, whatever order it came in", () => {
    const oldestFirst = [
      { run_name: "run0001", planned_at: 100 },
      { run_name: "run0002", planned_at: 200 },
      { run_name: "run0003", planned_at: 300 },
    ];
    expect(names(newestFirst(oldestFirst))).toEqual(["run0003", "run0002", "run0001"]);
    // What the backend sends since 2c56feb: already newest first. The old
    // reverse() turned this into oldest first.
    expect(names(newestFirst([...oldestFirst].reverse()))).toEqual([
      "run0003",
      "run0002",
      "run0001",
    ]);
  });

  it("breaks ties by name, numerically", () => {
    const tied = [
      { run_name: "run0009", planned_at: 5 },
      { run_name: "run0010", planned_at: 5 },
    ];
    expect(names(newestFirst(tied))).toEqual(["run0010", "run0009"]);
  });

  it("treats a run without a planning time as oldest and leaves the input alone", () => {
    const input = [{ run_name: "run0001" }, { run_name: "run0002", planned_at: 1 }];
    expect(names(newestFirst(input))).toEqual(["run0002", "run0001"]);
    expect(names(input)).toEqual(["run0001", "run0002"]);
  });
});
