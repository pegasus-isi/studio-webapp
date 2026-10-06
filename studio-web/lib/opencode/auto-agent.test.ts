import { describe, expect, it } from "vitest";
import {
  ARCHITECT,
  DATA_ENGINEER,
  DEBUGGER,
  routeAgent,
} from "@/lib/opencode/auto-agent";

const ALL = [ARCHITECT, DEBUGGER, DATA_ENGINEER, "build", "plan"];
const route = (text: string, extra: Partial<Parameters<typeof routeAgent>[0]> = {}) =>
  routeAgent({ text, available: ALL, ...extra }).agent;

describe("routeAgent", () => {
  it("sends a workflow request to the architect even when it talks about failure and data", () => {
    // The request that prompted Auto mode: creation language, plus "fail",
    // "fetch" and "CSV". It is a design request.
    expect(
      route(
        "I'd like a Pegasus workflow that fetches daily USGS streamflow CSV for five gauges. " +
          "If one gauge fails, note it; if fewer than two come back the run should fail."
      )
    ).toBe(ARCHITECT);
  });

  it("sends failures to the debugger", () => {
    expect(route("my fetch job is held, what does the hold reason mean?")).toBe(DEBUGGER);
    expect(route("why did run0002 fail?")).toBe(DEBUGGER);
  });

  it("sends data questions to the data engineer", () => {
    expect(route("which USGS endpoint returns daily values as JSON?")).toBe(DATA_ENGINEER);
  });

  it("treats a broken run in context as a debugging question unless asked to build", () => {
    const block = "### Run: run0002\n\n- Status: failed\n- Jobs: 9 total, 7 completed, 2 failed, 0 running";
    expect(route("what should I do next?", { scopeBlock: block })).toBe(DEBUGGER);
    expect(route("ok, now what?", { scopeBlock: block })).toBe(DEBUGGER);
    expect(route("add a plotting job", { scopeBlock: block })).toBe(ARCHITECT);
  });

  it("does not treat a healthy run as broken", () => {
    const block = "### Run: run0001\n\n- Status: succeeded\n- Jobs: 9 total, 9 completed, 0 failed, 0 running";
    expect(route("what should I do next?", { scopeBlock: block })).toBe(ARCHITECT);
  });

  it("keeps the previous agent for a follow-up with no signal", () => {
    expect(route("yes, go ahead", { previous: DATA_ENGINEER })).toBe(DATA_ENGINEER);
    expect(route("yes, go ahead")).toBe(ARCHITECT);
  });

  it("never picks an agent the engine does not have", () => {
    expect(routeAgent({ text: "why did it fail?", available: [ARCHITECT] }).agent).toBe(ARCHITECT);
    expect(routeAgent({ text: "hello", available: ["build"] }).agent).toBeUndefined();
    expect(routeAgent({ text: "ok", previous: "gone", available: [ARCHITECT] }).agent).toBe(ARCHITECT);
  });
});
