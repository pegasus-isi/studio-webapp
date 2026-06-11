/**
 * Zustand store for workflow monitoring state.
 */

import { create } from "zustand";

interface WorkflowStore {
  selectedRunId: string | null;
  selectedProjectId: string | null;
  selectWorkflow: (id: string | null) => void;
  selectProject: (id: string | null) => void;

  // SSE event source reference for cleanup
  eventSource: EventSource | null;
  setEventSource: (es: EventSource | null) => void;
}

export const useWorkflowStore = create<WorkflowStore>((set, get) => ({
  selectedRunId: null,
  selectedProjectId: null,
  eventSource: null,

  selectWorkflow: (id) => {
    // Close previous event source
    const prev = get().eventSource;
    if (prev) prev.close();
    set({ selectedRunId: id, selectedProjectId: null, eventSource: null });
  },

  selectProject: (id) => {
    // Close previous event source
    const prev = get().eventSource;
    if (prev) prev.close();
    set({ selectedProjectId: id, selectedRunId: null, eventSource: null });
  },

  setEventSource: (es) => set({ eventSource: es }),
}));
