/**
 * Zustand store for built-in chat — conversation-aware.
 *
 * Multiple conversations live client-side (localStorage): each has its own
 * message list. `messages` always mirrors the active conversation, so the
 * existing message API (addMessage/appendToLast/…) is unchanged for callers.
 */

import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";

// localStorage wrapper that never throws (FE-3). A QuotaExceededError on write
// (or storage being disabled) is swallowed rather than bubbling up as an
// uncaught error; the partialize cap keeps us well under quota in normal use,
// so this is the last-resort guard. SSR-safe: localStorage is only touched
// inside the methods, which persist doesn't call on the server.
const safeLocalStorage = {
  getItem: (name: string): string | null => {
    try {
      return globalThis.localStorage?.getItem(name) ?? null;
    } catch {
      return null;
    }
  },
  setItem: (name: string, value: string): void => {
    try {
      globalThis.localStorage?.setItem(name, value);
    } catch {
      /* quota exceeded or storage disabled — drop this write */
    }
  },
  removeItem: (name: string): void => {
    try {
      globalThis.localStorage?.removeItem(name);
    } catch {
      /* ignore */
    }
  },
};

export interface ChatMsg {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  toolCalls?: { id: string; name: string; arguments: unknown }[];
  toolResults?: { id: string; name: string; result: string }[];
  // Shell commands awaiting the user's Approve/Deny (SEC-4). Removed once the
  // user decides; the tool_call/tool_result that follow render normally.
  pendingApprovals?: { id: string; name: string; summary: string }[];
  // Transient error for this turn (connection/stream failure). Kept separate
  // from `content` so it's shown to the user but never re-sent to the model.
  error?: string;
  agentId?: string;
  createdAt?: string;
  // DB row id (assistant turns), sent at end-of-stream — needed to attach
  // 👍/👎 feedback. serverId absent = feedback can't be recorded yet.
  serverId?: number;
  feedback?: 1 | -1 | 0;
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
  // Streaming actions accept an optional target conversation id so a response
  // that started in conversation A keeps landing in A even if the user switches
  // to B mid-stream. Defaults to the active conversation.
  appendToLast: (text: string, convId?: string) => void;
  addToolCall: (
    call: { id: string; name: string; arguments: unknown },
    convId?: string
  ) => void;
  addToolResult: (
    result: { id: string; name: string; result: string },
    convId?: string
  ) => void;
  addPermissionRequest: (
    req: { id: string; name: string; summary: string },
    convId?: string
  ) => void;
  clearPermissionRequest: (callId: string, convId?: string) => void;
  setLastMeta: (meta: NonNullable<ChatMsg["meta"]>, convId?: string) => void;
  setLastServerId: (serverId: number, convId?: string) => void;
  setMessageFeedback: (
    messageId: string,
    feedback: 1 | -1 | 0,
    convId?: string
  ) => void;
  setLastError: (error: string, convId?: string) => void;
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

// Stable unique id for messages and conversations. crypto.randomUUID is
// available in secure contexts (the studio is served over https via the
// gateway); the Math.random fallback keeps non-secure/dev origins working.
export const genId = (): string => {
  const c = typeof crypto !== "undefined" ? crypto : undefined;
  if (c?.randomUUID) return c.randomUUID();
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
};
const newId = () => `c-${genId()}`;
const deriveTitle = (text: string) =>
  text.trim().replace(/\s+/g, " ").slice(0, 40) || "New chat";

// Persist bounds (FE-3): the full messagesById used to be written to
// localStorage with no cap, so tool-result blobs eventually tripped
// QuotaExceededError and chat history silently stopped surviving reloads.
const MAX_PERSIST_CONVERSATIONS = 50;
const MAX_PERSIST_MESSAGES_PER_CONV = 200;

// Shared "update the current assistant turn" mutation (FE-2). Every streaming
// frame (text, tool_call, tool_result, meta, message_id, permission) lands on
// the last assistant bubble — creating it when the turn opens with a non-text
// frame (the normal tool-first agent shape). Without this, a tool_call before
// the first token was dropped and an early message_id disabled feedback.
type ChatMsgUpdater = (m: ChatMsg) => ChatMsg;

function _mutateLastAssistant(
  s: ChatStore,
  convId: string | undefined,
  update: ChatMsgUpdater
): Partial<ChatStore> {
  const cid = convId ?? s.activeId;
  const arr = [...(s.messagesById[cid] || [])];
  const last = arr[arr.length - 1];
  if (last && last.role === "assistant") {
    arr[arr.length - 1] = update({ ...last });
  } else {
    arr.push(update({ id: genId(), role: "assistant", content: "" }));
  }
  return {
    messagesById: { ...s.messagesById, [cid]: arr },
    ...(cid === s.activeId ? { messages: arr } : {}),
  };
}

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

      appendToLast: (text, convId) =>
        set((s) =>
          _mutateLastAssistant(s, convId, (m) => ({
            ...m,
            content: m.content + text,
          }))
        ),

      addToolCall: (call, convId) =>
        set((s) =>
          _mutateLastAssistant(s, convId, (m) => ({
            ...m,
            toolCalls: [...(m.toolCalls || []), call],
          }))
        ),

      addToolResult: (result, convId) =>
        set((s) =>
          _mutateLastAssistant(s, convId, (m) => ({
            ...m,
            toolResults: [...(m.toolResults || []), result],
          }))
        ),

      addPermissionRequest: (req, convId) =>
        set((s) =>
          _mutateLastAssistant(s, convId, (m) => ({
            ...m,
            pendingApprovals: [...(m.pendingApprovals || []), req],
          }))
        ),

      clearPermissionRequest: (callId, convId) =>
        set((s) => {
          const cid = convId ?? s.activeId;
          const arr = (s.messagesById[cid] || []).map((m) =>
            m.pendingApprovals?.some((p) => p.id === callId)
              ? {
                  ...m,
                  pendingApprovals: m.pendingApprovals.filter(
                    (p) => p.id !== callId
                  ),
                }
              : m
          );
          return {
            messagesById: { ...s.messagesById, [cid]: arr },
            ...(cid === s.activeId ? { messages: arr } : {}),
          };
        }),

      setLastMeta: (meta, convId) =>
        set((s) => _mutateLastAssistant(s, convId, (m) => ({ ...m, meta }))),

      setLastServerId: (serverId, convId) =>
        set((s) =>
          _mutateLastAssistant(s, convId, (m) => ({ ...m, serverId }))
        ),

      setMessageFeedback: (messageId, feedback, convId) =>
        set((s) => {
          const cid = convId ?? s.activeId;
          const arr = (s.messagesById[cid] || []).map((m) =>
            m.id === messageId ? { ...m, feedback } : m
          );
          return {
            messagesById: { ...s.messagesById, [cid]: arr },
            ...(cid === s.activeId ? { messages: arr } : {}),
          };
        }),

      setLastError: (error, convId) =>
        set((s) => _mutateLastAssistant(s, convId, (m) => ({ ...m, error }))),

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
      version: 1,
      storage: createJSONStorage(() => safeLocalStorage),
      partialize: (s) => {
        // Bound what we persist (FE-3): cap conversations and messages/conv so
        // tool-result blobs can't grow the payload past localStorage's ~5 MB
        // and trip QuotaExceededError (which silently loses ALL history).
        const keptConvs = s.conversations.slice(0, MAX_PERSIST_CONVERSATIONS);
        const keptIds = new Set(keptConvs.map((c) => c.id));
        const messagesById: Record<string, ChatMsg[]> = {};
        for (const id of keptIds) {
          const msgs = s.messagesById[id] || [];
          messagesById[id] =
            msgs.length > MAX_PERSIST_MESSAGES_PER_CONV
              ? msgs.slice(-MAX_PERSIST_MESSAGES_PER_CONV)
              : msgs;
        }
        return {
          conversations: keptConvs,
          activeId: keptIds.has(s.activeId) ? s.activeId : keptConvs[0]?.id,
          messagesById,
          agentId: s.agentId,
          provider: s.provider,
          model: s.model,
        };
      },
      // messages mirrors messagesById[activeId] — recompute after rehydrate.
      onRehydrateStorage: () => (state) => {
        if (state) {
          // A reload kills the SSE stream, so any persisted approval prompt is
          // dead (its decision endpoint would no-op). Drop the cards so the
          // user isn't shown buttons that can't resolve anything.
          for (const cid of Object.keys(state.messagesById)) {
            state.messagesById[cid] = state.messagesById[cid].map((m) =>
              m.pendingApprovals ? { ...m, pendingApprovals: undefined } : m
            );
          }
          state.messages = state.messagesById[state.activeId] || [];
        }
      },
    }
  )
);
