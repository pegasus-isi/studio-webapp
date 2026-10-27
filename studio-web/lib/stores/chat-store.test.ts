import { beforeEach, describe, expect, it } from "vitest";

import { useChatStore } from "@/lib/stores/chat-store";

describe("chat store notices", () => {
  beforeEach(() => {
    useChatStore.setState({
      conversations: [{ id: "c-test", title: "Test", createdAt: 0 }],
      activeId: "c-test",
      messagesById: { "c-test": [] },
      messages: [],
      isStreaming: false,
      requestId: null,
    });
  });

  it("attaches a notice to the last assistant message", () => {
    const store = useChatStore.getState();

    store.addMessage({ id: "u1", role: "user", content: "hi" });
    store.addNotice({
      kind: "tool_limit",
      message: "Tool limit reached: stopped after 2 tool calls.",
    });

    const last = useChatStore.getState().messages.at(-1);
    expect(last?.role).toBe("assistant");
    expect(last?.notices).toEqual([
      {
        kind: "tool_limit",
        message: "Tool limit reached: stopped after 2 tool calls.",
      },
    ]);
  });
});
