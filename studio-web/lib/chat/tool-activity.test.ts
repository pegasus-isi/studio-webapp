import { describe, expect, it } from "vitest";

import {
  deriveChatActivity,
  latestToolActivity,
  mergeToolActivity,
  toolActivityTarget,
} from "@/lib/chat/tool-activity";

describe("tool activity", () => {
  it("merges calls and results by id and preserves call order", () => {
    const entries = mergeToolActivity(
      [
        { id: "1", name: "read_file", arguments: { path: "a.py" } },
        { id: "2", name: "search", arguments: { query: "foo" } },
      ],
      [{ id: "1", name: "read_file", result: "contents" }]
    );

    expect(entries.map((e) => e.id)).toEqual(["1", "2"]);
    expect(entries[0]).toMatchObject({ status: "ok", result: "contents" });
    expect(entries[1].status).toBe("running");
  });

  it("appends orphan results", () => {
    const entries = mergeToolActivity([], [
      { id: "9", name: "run_command", result: "exit code: 0" },
    ]);
    expect(entries).toHaveLength(1);
    expect(entries[0].status).toBe("ok");
  });

  it("marks errors", () => {
    const entries = mergeToolActivity(
      [{ id: "1", name: "edit_blocks", arguments: {} }],
      [{ id: "1", name: "edit_blocks", result: "Error: nothing written" }]
    );
    expect(entries[0].status).toBe("error");
  });

  it("extracts compact targets", () => {
    expect(toolActivityTarget("read_file", { path: "a.py" })).toBe("a.py");
    expect(toolActivityTarget("run_command", { command: "ls -R" })).toBe("ls -R");
    expect(
      toolActivityTarget("edit_blocks", {
        edits: [{ path: "a.py" }, { path: "b.py" }, { path: "a.py" }],
      })
    ).toBe("a.py, b.py");
  });

  it("keeps only the latest five entries", () => {
    const entries = Array.from({ length: 8 }, (_, i) => ({
      id: String(i),
      name: "read_file",
      arguments: {},
      status: "ok" as const,
    }));
    expect(latestToolActivity(entries).map((e) => e.id)).toEqual([
      "3",
      "4",
      "5",
      "6",
      "7",
    ]);
  });
});

describe("chat activity", () => {
  it("shows thinking before the first assistant message", () => {
    expect(
      deriveChatActivity(true, [{ role: "user", content: "hi" }])
    ).toBe("thinking");
  });

  it("shows tools while a tool call is running", () => {
    expect(
      deriveChatActivity(true, [
        {
          role: "assistant",
          content: "",
          toolCalls: [{ id: "1" }],
          toolResults: [],
        },
      ])
    ).toBe("tools");
  });

  it("shows thinking after tools finish without text", () => {
    expect(
      deriveChatActivity(true, [
        {
          role: "assistant",
          content: "",
          toolCalls: [{ id: "1" }],
          toolResults: [{ id: "1" }],
        },
      ])
    ).toBe("thinking");
  });

  it("does not show an indicator while text is available", () => {
    expect(
      deriveChatActivity(true, [
        { role: "assistant", content: "Here is the answer" },
      ])
    ).toBeNull();
  });

  it("shows approval when a command is waiting", () => {
    expect(
      deriveChatActivity(true, [
        {
          role: "assistant",
          content: "",
          pendingApprovals: [{ id: "a1" }],
        },
      ])
    ).toBe("approval");
  });

  it("shows nothing when the turn is over", () => {
    expect(
      deriveChatActivity(false, [{ role: "assistant", content: "" }])
    ).toBeNull();
  });
});
