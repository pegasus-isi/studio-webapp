/**
 * Zustand store for global panel visibility (chat sidebar, terminal bottom
 * pane, sidebar collapse). Only the sidebar collapse preference is persisted.
 */

import { create } from "zustand";
import { persist } from "zustand/middleware";

interface PanelStore {
  chatOpen: boolean;
  terminalOpen: boolean;
  sidebarCollapsed: boolean;

  toggleChat: () => void;
  toggleTerminal: () => void;
  toggleSidebar: () => void;
  setChatOpen: (open: boolean) => void;
  setTerminalOpen: (open: boolean) => void;
}

export const usePanelStore = create<PanelStore>()(
  persist(
    (set) => ({
      chatOpen: false,
      terminalOpen: false,
      sidebarCollapsed: false,

      toggleChat: () => set((s) => ({ chatOpen: !s.chatOpen })),
      toggleTerminal: () => set((s) => ({ terminalOpen: !s.terminalOpen })),
      toggleSidebar: () =>
        set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),
      setChatOpen: (open) => set({ chatOpen: open }),
      setTerminalOpen: (open) => set({ terminalOpen: open }),
    }),
    {
      name: "studio-panels",
      partialize: (s) => ({ sidebarCollapsed: s.sidebarCollapsed }),
    }
  )
);
