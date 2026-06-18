/**
 * Active-notebook context for the embedded chat. Populated only when the chat
 * runs inside JupyterLab (the labextension postMessages the focused notebook
 * path). In the studio sidebar it stays null, so chat behaves normally there.
 */

import { create } from "zustand";

interface NotebookContextStore {
  activeNotebookPath: string | null;
  include: boolean; // whether to attach it to the next message
  setActiveNotebookPath: (p: string | null) => void;
  setInclude: (v: boolean) => void;
}

export const useNotebookContextStore = create<NotebookContextStore>((set) => ({
  activeNotebookPath: null,
  include: true,
  setActiveNotebookPath: (p) => set({ activeNotebookPath: p }),
  setInclude: (v) => set({ include: v }),
}));
