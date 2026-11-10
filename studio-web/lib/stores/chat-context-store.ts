/**
 * Workflow/run scope for the chat — what the context chip above the input
 * shows and what rides along, as the Active Context block, on every turn.
 *
 * Scope is per conversation: switching conversations restores what each one
 * was about. Two ways a scope gets set:
 *
 *  - auto-follow: opening a workflow or run in the Workflows page follows it
 *    into the chat, so "why did it fail?" works with zero setup;
 *  - an explicit pick from the chip's dropdown, which *pins* the conversation
 *    (auto-follow stops overriding it) until the user picks "Follow my
 *    selection" again.
 *
 * Only {type, id} is sent to the backend — the label is display-only and the
 * server re-resolves the scope fresh each turn.
 */

import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";

export interface ChatScope {
  type: "workflow" | "run";
  id: string;
  /** Display-only; the backend resolves the id itself. */
  label: string;
}

// Same never-throw storage posture as chat-store (FE-3): storage being full
// or disabled must degrade to "scope doesn't survive reloads", not an error.
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

interface ChatContextStore {
  scopeByConv: Record<string, ChatScope | null>;
  /** true once the user picked explicitly — auto-follow stops overriding. */
  pinnedByConv: Record<string, boolean>;

  /** Explicit pick from the chip: sets and pins (null pins "no context"). */
  setScope: (convId: string, scope: ChatScope | null) => void;
  /** Auto-follow from the Workflows page: no-op for pinned conversations. */
  followSelection: (convId: string, scope: ChatScope) => void;
  /** Back to auto-follow: unpin and clear so the next selection lands. */
  resetToFollow: (convId: string) => void;
}

export const useChatContextStore = create<ChatContextStore>()(
  persist(
    (set) => ({
      scopeByConv: {},
      pinnedByConv: {},

      setScope: (convId, scope) =>
        set((s) => ({
          scopeByConv: { ...s.scopeByConv, [convId]: scope },
          pinnedByConv: { ...s.pinnedByConv, [convId]: true },
        })),

      followSelection: (convId, scope) =>
        set((s) => {
          if (s.pinnedByConv[convId]) return s;
          const current = s.scopeByConv[convId];
          if (current && current.type === scope.type && current.id === scope.id) {
            // Same scope — only refresh the label (ids resolve to names late).
            if (current.label === scope.label) return s;
          }
          return {
            ...s,
            scopeByConv: { ...s.scopeByConv, [convId]: scope },
          };
        }),

      resetToFollow: (convId) =>
        set((s) => ({
          scopeByConv: { ...s.scopeByConv, [convId]: null },
          pinnedByConv: { ...s.pinnedByConv, [convId]: false },
        })),
    }),
    {
      name: "studio-chat-context",
      version: 1,
      storage: createJSONStorage(() => safeLocalStorage),
    }
  )
);
