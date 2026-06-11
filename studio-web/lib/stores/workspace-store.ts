/**
 * Zustand store for workspace / file browser state.
 */

import { create } from "zustand";

interface WorkspaceStore {
  currentPath: string;
  setCurrentPath: (path: string) => void;

  // Jupyter
  jupyterStatus: "stopped" | "starting" | "running";
  jupyterPort: number | null;
  setJupyterStatus: (status: "stopped" | "starting" | "running", port?: number | null) => void;
}

export const useWorkspaceStore = create<WorkspaceStore>((set) => ({
  currentPath: "",
  setCurrentPath: (path) => set({ currentPath: path }),

  jupyterStatus: "stopped",
  jupyterPort: null,
  setJupyterStatus: (status, port) =>
    set({ jupyterStatus: status, jupyterPort: port ?? null }),
}));
