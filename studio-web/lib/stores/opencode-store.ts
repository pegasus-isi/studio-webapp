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

interface OpenCodeStore {
  /** Studio conversation id -> OpenCode session id. */
  sessionByConv: Record<string, string>;
  setSession: (convId: string, sessionID: string) => void;
  /** Forget the pairing (the session itself is left alone on the server). */
  clearSession: (convId: string) => void;
}

export const useOpenCodeStore = create<OpenCodeStore>()(
  persist(
    (set) => ({
      sessionByConv: {},

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
    }
  )
);
