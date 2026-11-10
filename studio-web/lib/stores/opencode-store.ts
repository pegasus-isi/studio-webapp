/**
 * The one piece of chat state Studio still owns under the OpenCode engine
 * (#367): which OpenCode session each Studio conversation is.
 *
 * Everything else — the messages, their parts, compaction, what the model was
 * told — lives in OpenCode's own database and is read back from the server.
 * That is the point of the design: there is no second copy to keep in sync,
 * and a reload or a new device shows the same transcript because the transcript
 * was never here.
 *
 * Sessions are not deleted when a mapping is dropped. An OpenCode session is
 * the user's history, and clearing a Studio conversation should not be a
 * destructive act on the server.
 */

import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";

// Same never-throw storage as chat-store (FE-3): storage disabled or full must
// mean "the pairing doesn't survive a reload", not an uncaught error.
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

/**
 * Drop a conversation's pairing from outside React.
 *
 * The conversation controls (New / Clear / Delete) live in `chat-store`, so
 * they need a way to say "this conversation is starting over" without
 * reaching into this store's shape.
 */
export function forgetOpenCodeSession(convId: string): void {
  useOpenCodeStore.getState().clearSession(convId);
}

interface OpenCodeStore {
  /** Studio conversation id -> OpenCode session id. */
  sessionByConv: Record<string, string>;
  setSession: (convId: string, sessionID: string) => void;
  /** Forget the pairing (the session itself is left alone on the server). */
  clearSession: (convId: string) => void;

  /**
   * Studio conversation id -> the primary agent answering it.
   *
   * Per conversation and persisted, because it is a property of what the
   * conversation is *for*: a chat opened to think through an approach should
   * still be in plan mode tomorrow. Absent means the engine's default.
   */
  agentByConv: Record<string, string>;
  setAgent: (convId: string, agent: string) => void;

  /**
   * The model picked in the chat panel ("provider/model", "" = the configured
   * default), mirrored here so the feedback link can report it. Not persisted:
   * the picker itself resets on reload, and a stored value would then name a
   * model the user is no longer using.
   */
  currentModel: string;
  setCurrentModel: (model: string) => void;

  /**
   * OpenCode assistant message id -> the user's 👍 (1) / 👎 (-1) on it.
   *
   * The backend (`/api/feedback/message`) is the record; this copy exists only
   * so the selected thumb survives a reload, since there is no read endpoint
   * and the transcript itself comes from OpenCode, which knows nothing about
   * ratings. Keyed by message id alone because OpenCode's ids are unique
   * across sessions. Absent means unrated.
   */
  ratingByMessage: Record<string, 1 | -1>;
  /** 0 un-rates, dropping the key so the map holds only real votes. */
  setRating: (messageID: string, rating: 1 | -1 | 0) => void;
}

export const useOpenCodeStore = create<OpenCodeStore>()(
  persist(
    (set) => ({
      sessionByConv: {},
      agentByConv: {},
      currentModel: "",

      setCurrentModel: (model) => set({ currentModel: model }),

      ratingByMessage: {},

      setRating: (messageID, rating) =>
        set((s) => {
          const next = { ...s.ratingByMessage };
          if (rating === 0) delete next[messageID];
          else next[messageID] = rating;
          return { ratingByMessage: next };
        }),

      setAgent: (convId, agent) =>
        set((s) => ({ agentByConv: { ...s.agentByConv, [convId]: agent } })),

      setSession: (convId, sessionID) =>
        set((s) => ({
          sessionByConv: { ...s.sessionByConv, [convId]: sessionID },
        })),

      clearSession: (convId) =>
        set((s) => {
          const next = { ...s.sessionByConv };
          delete next[convId];
          return { sessionByConv: next };
        }),
    }),
    {
      name: "studio-opencode",
      version: 1,
      storage: createJSONStorage(() => safeLocalStorage),
      partialize: (s) => ({
        sessionByConv: s.sessionByConv,
        agentByConv: s.agentByConv,
        ratingByMessage: s.ratingByMessage,
      }),
    }
  )
);
