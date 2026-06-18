/**
 * Zustand store for AI tool marketplace state.
 *
 * Tracks which tools are installed, running, and which terminal tabs are open.
 * Tabs (and their backend terminal session ids) are persisted to localStorage:
 * the backend holds the PTY for each session, so after a browser reload the
 * same tabs reattach to the same still-running shells.
 */

import { create } from "zustand";
import { persist } from "zustand/middleware";

export interface TerminalTab {
  id: string;
  toolId: string | null; // null = bash terminal
  label: string;
  active: boolean;
  sessionId?: string; // backend terminal session (server-held PTY)
}

interface ToolStore {
  // Terminal tab management
  tabs: TerminalTab[];
  activeTabId: string | null;

  addTab: (toolId: string | null, label: string) => string;
  removeTab: (id: string) => void;
  setActiveTab: (id: string) => void;
  setTabSession: (id: string, sessionId: string | undefined) => void;

  // Installing state (for UI loading indicators)
  installingTools: Set<string>;
  setInstalling: (toolId: string, installing: boolean) => void;
}

function newTabId(): string {
  return `tab-${Math.random().toString(36).slice(2, 10)}`;
}

export const useToolStore = create<ToolStore>()(
  persist(
    (set) => ({
      tabs: [],
      activeTabId: null,
      installingTools: new Set<string>(),

      addTab: (toolId, label) => {
        const id = newTabId();
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

      setTabSession: (id, sessionId) => {
        set((state) => ({
          tabs: state.tabs.map((t) => (t.id === id ? { ...t, sessionId } : t)),
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
    }),
    {
      name: "studio-terminal-tabs",
      // installingTools is transient UI state (and a Set doesn't serialize)
      partialize: (state) => ({
        tabs: state.tabs,
        activeTabId: state.activeTabId,
      }),
    }
  )
);
