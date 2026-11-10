import { describe, expect, it } from "vitest";
import type { Part } from "@opencode-ai/sdk";
import { formatTokens, outputLimitInfo } from "./output-limit";

const done = { completed: 2 };

// Only the fields the rule reads; the cast keeps the fixtures readable.
const text = (t: string) => ({ type: "text", text: t }) as unknown as Part;
const reasoning = (t: string) => ({ type: "reasoning", text: t }) as unknown as Part;
const tool = () => ({ type: "tool", tool: "read" }) as unknown as Part;
const stepFinish = () => ({ type: "step-finish" }) as unknown as Part;

describe("outputLimitInfo", () => {
  it("flags the reply that reasoned until the cap and said nothing", () => {
    // The shape recorded live on 2026-11-10.
    const info = {
      role: "assistant",
      finish: "length",
      time: done,
      tokens: { output: 0, reasoning: 8192 },
    };
    expect(outputLimitInfo(info, [reasoning("thinking…"), stepFinish()])).toEqual({
      kind: "empty",
      reasoning: 8192,
      output: 0,
    });
  });

  it("calls a cut-off reply with text truncated, not empty", () => {
    const info = {
      role: "assistant",
      finish: "length",
      time: done,
      tokens: { output: 900, reasoning: 7292 },
    };
    expect(outputLimitInfo(info, [text("Here is the plan")])?.kind).toBe(
      "truncated"
    );
  });

  it("does not call a step that made a tool call empty", () => {
    const info = { role: "assistant", finish: "length", time: done };
    expect(outputLimitInfo(info, [tool()])?.kind).toBe("truncated");
  });

  it("ignores whitespace-only text", () => {
    const info = { role: "assistant", finish: "length", time: done };
    expect(outputLimitInfo(info, [text("  \n ")])?.kind).toBe("empty");
  });

  it("ignores every other finish reason", () => {
    for (const finish of ["stop", "tool-calls", undefined]) {
      expect(
        outputLimitInfo({ role: "assistant", finish, time: done }, [])
      ).toBeNull();
    }
  });

  it("leaves a step with an error to the error box", () => {
    const info = {
      role: "assistant",
      finish: "length",
      time: done,
      error: { name: "APIError" },
    };
    expect(outputLimitInfo(info, [])).toBeNull();
  });

  it("waits until the step has finished", () => {
    const info = { role: "assistant", finish: "length", time: {} };
    expect(outputLimitInfo(info, [])).toBeNull();
  });

  it("never flags a user message", () => {
    expect(
      outputLimitInfo({ role: "user", finish: "length", time: done }, [])
    ).toBeNull();
  });

  it("treats missing token counts as zero", () => {
    const info = { role: "assistant", finish: "length", time: done };
    expect(outputLimitInfo(info, [])).toEqual({
      kind: "empty",
      reasoning: 0,
      output: 0,
    });
  });
});

describe("formatTokens", () => {
  it("abbreviates thousands", () => {
    expect(formatTokens(8192)).toBe("8.2K");
    expect(formatTokens(32000)).toBe("32K");
    expect(formatTokens(128000)).toBe("128K");
    expect(formatTokens(512)).toBe("512");
  });
});
