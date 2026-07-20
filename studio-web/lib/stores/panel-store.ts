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

interface PanelStore {
  chatOpen: boolean;
  terminalOpen: boolean;
  sidebarCollapsed: boolean;
  // One-shot text to drop into the chat input (e.g. a "/scaffold " prefix from
  // a dashboard capability card). ChatInput consumes it then clears it.
  chatDraft: string | null;

  toggleChat: () => void;
  toggleTerminal: () => void;
  toggleSidebar: () => void;
  setChatOpen: (open: boolean) => void;
  setTerminalOpen: (open: boolean) => void;
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
      chatDraft: null,

      toggleChat: () => set((s) => ({ chatOpen: !s.chatOpen })),
      toggleTerminal: () => set((s) => ({ terminalOpen: !s.terminalOpen })),
      toggleSidebar: () =>
        set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),
      setChatOpen: (open) => set({ chatOpen: open }),
      setTerminalOpen: (open) => set({ terminalOpen: open }),
      setChatDraft: (draft) => set({ chatDraft: draft }),
      openChatWith: (draft) => set({ chatOpen: true, chatDraft: draft }),
    }),
    {
      name: "studio-panels",
      partialize: (s) => ({
        sidebarCollapsed: s.sidebarCollapsed,
        chatOpen: s.chatOpen,
        terminalOpen: s.terminalOpen,
      }),
    }
  )
);
