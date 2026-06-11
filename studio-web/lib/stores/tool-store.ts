/**
 * Zustand store for AI tool marketplace state.
 *
 * Tracks which tools are installed, running, and which terminal tabs are open.
 */

import { create } from "zustand";

export interface TerminalTab {
  id: string;
  toolId: string | null; // null = bash terminal
  label: string;
  active: boolean;
}

interface ToolStore {
  // Terminal tab management
  tabs: TerminalTab[];
  activeTabId: string | null;

  addTab: (toolId: string | null, label: string) => string;
  removeTab: (id: string) => void;
  setActiveTab: (id: string) => void;

  // Installing state (for UI loading indicators)
  installingTools: Set<string>;
  setInstalling: (toolId: string, installing: boolean) => void;
}

let _tabCounter = 0;

export const useToolStore = create<ToolStore>((set) => ({
  tabs: [],
  activeTabId: null,
  installingTools: new Set(),

  addTab: (toolId, label) => {
    const id = `tab-${++_tabCounter}`;
    set((state) => ({
      tabs: [
        ...state.tabs.map((t) => ({ ...t, active: false })),
        { id, toolId, label, active: true },
      ],
      activeTabId: id,
    }));
    return id;
  },

  removeTab: (id) => {
    set((state) => {
      const newTabs = state.tabs.filter((t) => t.id !== id);
      const wasActive = state.activeTabId === id;
      return {
        tabs: newTabs,
        activeTabId: wasActive
          ? newTabs[newTabs.length - 1]?.id ?? null
          : state.activeTabId,
      };
    });
  },

  setActiveTab: (id) => {
    set((state) => ({
      tabs: state.tabs.map((t) => ({ ...t, active: t.id === id })),
      activeTabId: id,
    }));
  },

  setInstalling: (toolId, installing) => {
    set((state) => {
      const next = new Set(state.installingTools);
      if (installing) next.add(toolId);
      else next.delete(toolId);
      return { installingTools: next };
    });
  },
}));
