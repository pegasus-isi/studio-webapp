/**
 * Zustand store for global panel visibility (chat sidebar, terminal bottom
 * pane, sidebar collapse).
 *
 * Panel visibility (chat/terminal open, sidebar collapsed) is persisted so it
 * survives navigation. Left-nav links are plain <a> full-page loads (the app
 * runs under a dynamic per-user prefix and avoids Next's build-time basePath),
 * which would otherwise reset the in-memory store and snap an open chat shut on
 * every tab switch. The transient `chatDraft` is intentionally not persisted —
 * it is a one-shot the ChatInput consumes on open.
 */

import { create } from "zustand";
import { persist } from "zustand/middleware";

// Terminal panel height bounds. The store clamps to an absolute range; the
// panel component additionally clamps against the live viewport.
export const TERMINAL_HEIGHT_DEFAULT = 300;
export const TERMINAL_HEIGHT_MIN = 160;
const TERMINAL_HEIGHT_MAX = 2000;

interface PanelStore {
  chatOpen: boolean;
  terminalOpen: boolean;
  sidebarCollapsed: boolean;
  // User-dragged height of the bottom terminal panel, in px. Persisted so a
  // TUI-friendly tall panel survives the full-page navigations described
  // above.
  terminalHeight: number;
  // One-shot text to drop into the chat input (e.g. a "/scaffold " prefix from
  // a dashboard capability card). ChatInput consumes it then clears it.
  chatDraft: string | null;

  toggleChat: () => void;
  toggleTerminal: () => void;
  toggleSidebar: () => void;
  setChatOpen: (open: boolean) => void;
  setTerminalOpen: (open: boolean) => void;
  setTerminalHeight: (height: number) => void;
  setChatDraft: (draft: string | null) => void;
  // Open the chat panel and seed the input with `draft`.
  openChatWith: (draft: string) => void;
}

export const usePanelStore = create<PanelStore>()(
  persist(
    (set) => ({
      chatOpen: false,
      terminalOpen: false,
      sidebarCollapsed: false,
      terminalHeight: TERMINAL_HEIGHT_DEFAULT,
      chatDraft: null,

      toggleChat: () => set((s) => ({ chatOpen: !s.chatOpen })),
      toggleTerminal: () => set((s) => ({ terminalOpen: !s.terminalOpen })),
      toggleSidebar: () =>
        set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),
      setChatOpen: (open) => set({ chatOpen: open }),
      setTerminalOpen: (open) => set({ terminalOpen: open }),
      setTerminalHeight: (height) =>
        set({
          terminalHeight: Math.round(
            Math.min(TERMINAL_HEIGHT_MAX, Math.max(TERMINAL_HEIGHT_MIN, height))
          ),
        }),
      setChatDraft: (draft) => set({ chatDraft: draft }),
      openChatWith: (draft) => set({ chatOpen: true, chatDraft: draft }),
    }),
    {
      name: "studio-panels",
      partialize: (s) => ({
        sidebarCollapsed: s.sidebarCollapsed,
        chatOpen: s.chatOpen,
        terminalOpen: s.terminalOpen,
        // NOTE: this is a whitelist — new persisted fields must be added
        // here or they silently reset on every page load.
        terminalHeight: s.terminalHeight,
      }),
    }
  )
);
