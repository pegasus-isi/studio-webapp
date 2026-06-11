/**
 * Zustand store for built-in chat state.
 */

import { create } from "zustand";

export interface ChatMsg {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  toolCalls?: { id: string; name: string; arguments: unknown }[];
  toolResults?: { id: string; name: string; result: string }[];
  agentId?: string;
  createdAt?: string;
}

interface ChatStore {
  messages: ChatMsg[];
  isStreaming: boolean;
  requestId: string | null;
  agentId: string;
  provider: string | null;
  model: string | null;

  addMessage: (msg: ChatMsg) => void;
  appendToLast: (text: string) => void;
  addToolCall: (call: { id: string; name: string; arguments: unknown }) => void;
  addToolResult: (result: { id: string; name: string; result: string }) => void;
  setStreaming: (streaming: boolean, requestId?: string | null) => void;
  setAgent: (agentId: string) => void;
  setProvider: (provider: string | null) => void;
  setModel: (model: string | null) => void;
  clearMessages: () => void;
  loadHistory: (msgs: ChatMsg[]) => void;
}

let _msgCounter = 0;

export const useChatStore = create<ChatStore>((set) => ({
  messages: [],
  isStreaming: false,
  requestId: null,
  agentId: "general",
  provider: null,
  model: null,

  addMessage: (msg) => set((s) => ({ messages: [...s.messages, msg] })),

  appendToLast: (text) =>
    set((s) => {
      const msgs = [...s.messages];
      const last = msgs[msgs.length - 1];
      if (last && last.role === "assistant") {
        msgs[msgs.length - 1] = { ...last, content: last.content + text };
      } else {
        msgs.push({
          id: `msg-${++_msgCounter}`,
          role: "assistant",
          content: text,
        });
      }
      return { messages: msgs };
    }),

  addToolCall: (call) =>
    set((s) => {
      const msgs = [...s.messages];
      const last = msgs[msgs.length - 1];
      if (last && last.role === "assistant") {
        const calls = [...(last.toolCalls || []), call];
        msgs[msgs.length - 1] = { ...last, toolCalls: calls };
      }
      return { messages: msgs };
    }),

  addToolResult: (result) =>
    set((s) => {
      const msgs = [...s.messages];
      const last = msgs[msgs.length - 1];
      if (last && last.role === "assistant") {
        const results = [...(last.toolResults || []), result];
        msgs[msgs.length - 1] = { ...last, toolResults: results };
      }
      return { messages: msgs };
    }),

  setStreaming: (streaming, requestId) =>
    set({ isStreaming: streaming, requestId: requestId ?? null }),

  setAgent: (agentId) => set({ agentId }),
  setProvider: (provider) => set({ provider }),
  setModel: (model) => set({ model }),
  clearMessages: () => set({ messages: [] }),
  loadHistory: (msgs) => set({ messages: msgs }),
}));
