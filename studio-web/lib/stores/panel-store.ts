/**
 * Zustand store for global panel visibility (chat sidebar, terminal bottom pane).
 */

import { create } from "zustand";

interface PanelStore {
  chatOpen: boolean;
  terminalOpen: boolean;

  toggleChat: () => void;
  toggleTerminal: () => void;
  setChatOpen: (open: boolean) => void;
  setTerminalOpen: (open: boolean) => void;
}

export const usePanelStore = create<PanelStore>((set) => ({
  chatOpen: false,
  terminalOpen: false,

  toggleChat: () => set((s) => ({ chatOpen: !s.chatOpen })),
  toggleTerminal: () => set((s) => ({ terminalOpen: !s.terminalOpen })),
  setChatOpen: (open) => set({ chatOpen: open }),
  setTerminalOpen: (open) => set({ terminalOpen: open }),
}));
