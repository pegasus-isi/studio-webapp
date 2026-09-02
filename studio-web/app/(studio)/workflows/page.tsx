"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { useWorkflows, useWorkflowProjects } from "@/lib/hooks/use-workflows";
import { WorkflowList } from "@/components/workflows/workflow-list";
import { WorkflowDetail } from "@/components/workflows/workflow-detail";
import { WorkflowProjectDetail } from "@/components/workflows/workflow-project-detail";
import { useWorkflowStore } from "@/lib/stores/workflow-store";

const LIST_MIN = 180;
const LIST_MAX = 520;
const LIST_DEFAULT = 288;

export default function WorkflowsPage() {
  const { data: runsData, isLoading: runsLoading, error: runsError } = useWorkflows();
  const { data: projectsData, isLoading: projectsLoading } = useWorkflowProjects();
  const selectedRunId = useWorkflowStore((s) => s.selectedRunId);
  const selectedProjectId = useWorkflowStore((s) => s.selectedProjectId);
  const selectWorkflow = useWorkflowStore((s) => s.selectWorkflow);

  // Deep-link support: `/workflows?run=<id>`
  useEffect(() => {
    const run = new URLSearchParams(window.location.search).get("run");
    if (run) selectWorkflow(run);
  }, [selectWorkflow]);

  // Resizable + collapsible sidebar
  const [listWidth, setListWidth] = useState(LIST_DEFAULT);
  const [isDragging, setIsDragging] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
  const listWidthRef = useRef(LIST_DEFAULT);

  const onDragStart = useCallback((e: React.MouseEvent) => {
    const startX = e.clientX;
    const startW = listWidthRef.current;
    e.preventDefault();
    setIsDragging(true);
    const onMove = (ev: MouseEvent) => {
      const next = Math.max(LIST_MIN, Math.min(LIST_MAX, startW + ev.clientX - startX));
      listWidthRef.current = next;
      setListWidth(next);
    };
    const onUp = () => {
      setIsDragging(false);
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
    };
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  }, []);

  const isLoading = runsLoading || projectsLoading;
  const projects = projectsData?.projects ?? [];
  const workflows = runsData?.workflows ?? [];

  const projectRunIds = new Set(projects.flatMap((p) => p.runs.map((r) => r.run_id)));
  const standaloneRuns = workflows.filter((w) => !projectRunIds.has(w.run_id));
  const totalCount = projects.length + standaloneRuns.length;

  return (
    <div className="flex h-full overflow-hidden">

      {/* ── Workflow list sidebar ── */}
      {collapsed ? (
        /* Collapsed strip — just a button to expand */
        <div className="flex w-9 shrink-0 flex-col items-center border-r border-line bg-surface pt-2">
          <button
            onClick={() => setCollapsed(false)}
            className="rounded-lg p-1.5 text-fgsubtle hover:bg-muted hover:text-fg transition-colors"
            title="Expand sidebar"
          >
            <ChevronRight size={15} />
          </button>
        </div>
      ) : (
        <>
          <div style={{ width: listWidth }} className="flex min-w-0 shrink-0 flex-col overflow-hidden bg-surface">
            <div className="shrink-0 flex items-center justify-between border-b border-line px-4 py-3.5">
              <div>
                <h1 className="text-sm font-semibold text-fg">Workflows</h1>
                <p className="mt-0.5 text-xs text-fgmuted">
                  {isLoading ? "Scanning…" : `${totalCount} workflow${totalCount !== 1 ? "s" : ""} found`}
                </p>
              </div>
              <button
                onClick={() => setCollapsed(true)}
                className="rounded-lg p-1 text-fgsubtle hover:bg-muted hover:text-fg transition-colors"
                title="Collapse sidebar"
              >
                <ChevronLeft size={15} />
              </button>
            </div>

            {runsError && (
              <div className="mx-3 mt-3 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-600 dark:border-red-900/40 dark:bg-red-900/10 dark:text-red-400">
                Failed to load: {(runsError as Error).message}
              </div>
            )}

            <div className="flex-1 overflow-hidden">
              <WorkflowList projects={projects} workflows={standaloneRuns} />
            </div>
          </div>

          {/* ── Drag handle ── */}
          <div
            onMouseDown={onDragStart}
            className="group relative flex w-1.5 shrink-0 cursor-col-resize items-center justify-center bg-line/40 transition-colors hover:bg-accent/40"
          >
            <div className="h-10 w-px rounded-full bg-line/60 transition-colors group-hover:bg-accent/60" />
          </div>

          {isDragging && <div className="fixed inset-0 z-50 cursor-col-resize" />}
        </>
      )}

      {/* ── Detail panel ── */}
      <div className="flex-1 overflow-hidden">
        {selectedProjectId ? (
          <WorkflowProjectDetail key={selectedProjectId} projectId={selectedProjectId} />
        ) : selectedRunId ? (
          <WorkflowDetail runId={selectedRunId} />
        ) : (
          <div className="flex h-full flex-col items-center justify-center gap-2 text-center">
            <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-muted">
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" className="text-fgsubtle">
                <circle cx="12" cy="12" r="10"/><polyline points="12 8 12 12 14 14"/>
              </svg>
            </div>
            <p className="text-sm font-medium text-fgmuted">Select a workflow</p>
            <p className="text-xs text-fgsubtle">Choose a project or run from the list</p>
          </div>
        )}
      </div>
    </div>
  );
}
