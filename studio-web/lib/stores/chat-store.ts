/**
 * The chat panel's conversation list: which conversations exist, what they
 * are called, and which one is open.
 *
 * Only the list lives here. Each conversation's transcript is its OpenCode
 * session's (`opencode-store` holds the pairing), read back from the server,
 * so there is nothing of the transcript to keep in sync or to outgrow
 * localStorage. The builtin engine that used to keep its messages here was
 * removed in #664.
 */

import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";

// localStorage wrapper that never throws (FE-3): storage disabled or full must
// mean "the list doesn't survive a reload", not an uncaught error. SSR-safe:
// localStorage is only touched inside the methods, which persist doesn't call
// on the server.
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

export interface ConversationMeta {
  id: string;
  title: string;
  createdAt: number;
}

interface ChatStore {
  conversations: ConversationMeta[];
  activeId: string;

  newConversation: () => void;
  switchConversation: (id: string) => void;
  renameConversation: (id: string, title: string) => void;
  /** Name a still-untitled conversation after its first user message. */
  titleFromMessage: (id: string, text: string) => void;
  deleteConversation: (id: string) => void;
}

// Stable unique id for conversations. crypto.randomUUID is available in secure
// contexts (the studio is served over https via the gateway); the Math.random
// fallback keeps non-secure/dev origins working.
export const genId = (): string => {
  const c = typeof crypto !== "undefined" ? crypto : undefined;
  if (c?.randomUUID) return c.randomUUID();
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
};
const newId = () => `c-${genId()}`;
const deriveTitle = (text: string) =>
  text.trim().replace(/\s+/g, " ").slice(0, 40) || "New chat";

const MAX_PERSIST_CONVERSATIONS = 50;

// Title an untitled conversation from its first user message. Returns the
// same array when nothing changes so callers can skip a store update.
function _titled(
  conversations: ConversationMeta[],
  id: string,
  text: string
): ConversationMeta[] {
  const conv = conversations.find((c) => c.id === id);
  if (!conv || (conv.title && conv.title !== "New chat")) return conversations;
  const title = deriveTitle(text);
  if (title === conv.title) return conversations;
  return conversations.map((c) => (c.id === id ? { ...c, title } : c));
}

const _firstId = newId();

export const useChatStore = create<ChatStore>()(
  persist(
    (set) => ({
      conversations: [{ id: _firstId, title: "New chat", createdAt: 0 }],
      activeId: _firstId,

      newConversation: () =>
        set((s) => {
          const id = newId();
          return {
            conversations: [
              { id, title: "New chat", createdAt: Date.now() },
              ...s.conversations,
            ],
            activeId: id,
          };
        }),

      switchConversation: (id) => set({ activeId: id }),

      renameConversation: (id, title) =>
        set((s) => ({
          conversations: s.conversations.map((c) =>
            c.id === id ? { ...c, title: title || c.title } : c
          ),
        })),

      titleFromMessage: (id, text) =>
        set((s) => {
          const conversations = _titled(s.conversations, id, text);
          return conversations === s.conversations ? s : { conversations };
        }),

      deleteConversation: (id) =>
        set((s) => {
          const conversations = s.conversations.filter((c) => c.id !== id);
          if (conversations.length === 0) {
            const nid = newId();
            return {
              conversations: [
                { id: nid, title: "New chat", createdAt: Date.now() },
              ],
              activeId: nid,
            };
          }
          const activeId = s.activeId === id ? conversations[0].id : s.activeId;
          return { conversations, activeId };
        }),
    }),
    {
      name: "studio-chat",
      // 2 (#664): the builtin engine's `messagesById` and its agent/provider/
      // model picks are gone. Dropping them in the migration, rather than
      // letting zustand merge them back in, is what frees the localStorage
      // they took up; the conversation list itself carries over untouched, so
      // nobody loses an OpenCode conversation.
      version: 2,
      storage: createJSONStorage(() => safeLocalStorage),
      migrate: (persisted) => {
        const old = (persisted ?? {}) as Partial<ChatStore>;
        return {
          conversations: Array.isArray(old.conversations)
            ? old.conversations
            : [],
          activeId: typeof old.activeId === "string" ? old.activeId : "",
        } as unknown as ChatStore;
      },
      partialize: (s) => {
        const conversations = s.conversations.slice(0, MAX_PERSIST_CONVERSATIONS);
        const kept = new Set(conversations.map((c) => c.id));
        return {
          conversations,
          activeId: kept.has(s.activeId) ? s.activeId : conversations[0]?.id,
        };
      },
      // A migrated or truncated list can leave no conversation, or an active id
      // that names none; the panel needs one open conversation to render.
      onRehydrateStorage: () => (state) => {
        if (!state) return;
        if (state.conversations.length === 0) {
          const id = newId();
          state.conversations = [{ id, title: "New chat", createdAt: Date.now() }];
          state.activeId = id;
        } else if (!state.conversations.some((c) => c.id === state.activeId)) {
          state.activeId = state.conversations[0].id;
        }
      },
    }
  )
);
