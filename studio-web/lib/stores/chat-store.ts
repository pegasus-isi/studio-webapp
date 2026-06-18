/**
 * Zustand store for built-in chat — conversation-aware.
 *
 * Multiple conversations live client-side (localStorage): each has its own
 * message list. `messages` always mirrors the active conversation, so the
 * existing message API (addMessage/appendToLast/…) is unchanged for callers.
 */

import { create } from "zustand";
import { persist } from "zustand/middleware";

export interface ChatMsg {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  toolCalls?: { id: string; name: string; arguments: unknown }[];
  toolResults?: { id: string; name: string; result: string }[];
  agentId?: string;
  createdAt?: string;
  meta?: {
    tokens?: number;
    inputTokens?: number;
    outputTokens?: number;
    toolCalls?: number;
    durationS?: number;
  };
}

export interface ConversationMeta {
  id: string;
  title: string;
  createdAt: number;
}

interface ChatStore {
  conversations: ConversationMeta[];
  activeId: string;
  messagesById: Record<string, ChatMsg[]>;
  messages: ChatMsg[]; // mirror of messagesById[activeId]
  isStreaming: boolean;
  requestId: string | null;
  agentId: string;
  provider: string | null;
  model: string | null;

  addMessage: (msg: ChatMsg) => void;
  appendToLast: (text: string) => void;
  addToolCall: (call: { id: string; name: string; arguments: unknown }) => void;
  addToolResult: (result: { id: string; name: string; result: string }) => void;
  setLastMeta: (meta: NonNullable<ChatMsg["meta"]>) => void;
  setStreaming: (streaming: boolean, requestId?: string | null) => void;
  setAgent: (agentId: string) => void;
  setProvider: (provider: string | null) => void;
  setModel: (model: string | null) => void;
  clearMessages: () => void;
  loadHistory: (msgs: ChatMsg[]) => void;

  newConversation: () => void;
  switchConversation: (id: string) => void;
  renameConversation: (id: string, title: string) => void;
  deleteConversation: (id: string) => void;
}

let _msgCounter = 0;
const newId = () => `c-${Math.random().toString(36).slice(2, 10)}`;
const deriveTitle = (text: string) =>
  text.trim().replace(/\s+/g, " ").slice(0, 40) || "New chat";

const _firstId = newId();

export const useChatStore = create<ChatStore>()(
  persist(
    (set) => ({
      conversations: [{ id: _firstId, title: "New chat", createdAt: 0 }],
      activeId: _firstId,
      messagesById: { [_firstId]: [] },
      messages: [],
      isStreaming: false,
      requestId: null,
      agentId: "general",
      provider: null,
      model: null,

      addMessage: (msg) =>
        set((s) => {
          const arr = [...(s.messagesById[s.activeId] || []), msg];
          // Auto-title the conversation from the first user message.
          let conversations = s.conversations;
          if (msg.role === "user") {
            const conv = s.conversations.find((c) => c.id === s.activeId);
            if (conv && (!conv.title || conv.title === "New chat")) {
              conversations = s.conversations.map((c) =>
                c.id === s.activeId
                  ? { ...c, title: deriveTitle(msg.content) }
                  : c
              );
            }
          }
          return {
            messagesById: { ...s.messagesById, [s.activeId]: arr },
            messages: arr,
            conversations,
          };
        }),

      appendToLast: (text) =>
        set((s) => {
          const arr = [...(s.messagesById[s.activeId] || [])];
          const last = arr[arr.length - 1];
          if (last && last.role === "assistant") {
            arr[arr.length - 1] = { ...last, content: last.content + text };
          } else {
            arr.push({
              id: `msg-${++_msgCounter}`,
              role: "assistant",
              content: text,
            });
          }
          return {
            messagesById: { ...s.messagesById, [s.activeId]: arr },
            messages: arr,
          };
        }),

      addToolCall: (call) =>
        set((s) => {
          const arr = [...(s.messagesById[s.activeId] || [])];
          const last = arr[arr.length - 1];
          if (last && last.role === "assistant") {
            arr[arr.length - 1] = {
              ...last,
              toolCalls: [...(last.toolCalls || []), call],
            };
          }
          return {
            messagesById: { ...s.messagesById, [s.activeId]: arr },
            messages: arr,
          };
        }),

      addToolResult: (result) =>
        set((s) => {
          const arr = [...(s.messagesById[s.activeId] || [])];
          const last = arr[arr.length - 1];
          if (last && last.role === "assistant") {
            arr[arr.length - 1] = {
              ...last,
              toolResults: [...(last.toolResults || []), result],
            };
          }
          return {
            messagesById: { ...s.messagesById, [s.activeId]: arr },
            messages: arr,
          };
        }),

      setLastMeta: (meta) =>
        set((s) => {
          const arr = [...(s.messagesById[s.activeId] || [])];
          const last = arr[arr.length - 1];
          if (last && last.role === "assistant") {
            arr[arr.length - 1] = { ...last, meta };
          }
          return {
            messagesById: { ...s.messagesById, [s.activeId]: arr },
            messages: arr,
          };
        }),

      setStreaming: (streaming, requestId) =>
        set({ isStreaming: streaming, requestId: requestId ?? null }),

      setAgent: (agentId) => set({ agentId }),
      setProvider: (provider) => set({ provider }),
      setModel: (model) => set({ model }),

      clearMessages: () =>
        set((s) => ({
          messagesById: { ...s.messagesById, [s.activeId]: [] },
          messages: [],
        })),

      loadHistory: (msgs) =>
        set((s) => ({
          messagesById: { ...s.messagesById, [s.activeId]: msgs },
          messages: msgs,
        })),

      newConversation: () =>
        set((s) => {
          const id = newId();
          return {
            conversations: [
              { id, title: "New chat", createdAt: Date.now() },
              ...s.conversations,
            ],
            activeId: id,
            messagesById: { ...s.messagesById, [id]: [] },
            messages: [],
          };
        }),

      switchConversation: (id) =>
        set((s) => ({ activeId: id, messages: s.messagesById[id] || [] })),

      renameConversation: (id, title) =>
        set((s) => ({
          conversations: s.conversations.map((c) =>
            c.id === id ? { ...c, title: title || c.title } : c
          ),
        })),

      deleteConversation: (id) =>
        set((s) => {
          const conversations = s.conversations.filter((c) => c.id !== id);
          const messagesById = { ...s.messagesById };
          delete messagesById[id];
          if (conversations.length === 0) {
            const nid = newId();
            return {
              conversations: [
                { id: nid, title: "New chat", createdAt: Date.now() },
              ],
              messagesById: { [nid]: [] },
              activeId: nid,
              messages: [],
            };
          }
          const activeId = s.activeId === id ? conversations[0].id : s.activeId;
          return {
            conversations,
            messagesById,
            activeId,
            messages: messagesById[activeId] || [],
          };
        }),
    }),
    {
      name: "studio-chat",
      partialize: (s) => ({
        conversations: s.conversations,
        activeId: s.activeId,
        messagesById: s.messagesById,
        agentId: s.agentId,
        provider: s.provider,
        model: s.model,
      }),
      // messages mirrors messagesById[activeId] — recompute after rehydrate.
      onRehydrateStorage: () => (state) => {
        if (state) {
          state.messages = state.messagesById[state.activeId] || [];
        }
      },
    }
  )
);
