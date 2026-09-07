"use client";

import { useEffect } from "react";
import { useWorkflows, useWorkflowProjects } from "@/lib/hooks/use-workflows";
import { WorkflowList } from "@/components/workflows/workflow-list";
import { ResizablePane } from "@/components/layout/resizable-pane";
import { WorkflowDetail } from "@/components/workflows/workflow-detail";
import { WorkflowProjectDetail } from "@/components/workflows/workflow-project-detail";
import { useWorkflowStore } from "@/lib/stores/workflow-store";
import { WorkflowPicker } from "@/components/workflows/workflow-picker";

export default function WorkflowsPage() {
  const { data: runsData, isLoading: runsLoading, error: runsError } = useWorkflows();
  const { data: projectsData, isLoading: projectsLoading } = useWorkflowProjects();
  const selectedRunId = useWorkflowStore((s) => s.selectedRunId);
  const selectedProjectId = useWorkflowStore((s) => s.selectedProjectId);
  const selectWorkflow = useWorkflowStore((s) => s.selectWorkflow);

  // Deep-link support: `/workflows?run=<id>` (e.g. from a notification) opens
  // that run on arrival. Read from the URL once on mount so later manual
  // selections aren't overridden.
  useEffect(() => {
    const run = new URLSearchParams(window.location.search).get("run");
    if (run) selectWorkflow(run);
  }, [selectWorkflow]);

  const isLoading = runsLoading || projectsLoading;
  const projects = projectsData?.projects ?? [];
  const workflows = runsData?.workflows ?? [];

  // Count: projects + standalone runs (runs not already inside a project)
  const projectRunIds = new Set(
    projects.flatMap((p) => p.runs.map((r) => r.run_id))
  );
  const standaloneRuns = workflows.filter((w) => !projectRunIds.has(w.run_id));
  const totalCount = projects.length + standaloneRuns.length;

  return (
    <div className="flex h-full flex-col">
      {/* Which analysis, above both panes. In the detail header it only
          appeared once something was selected — no use for selecting, and
          gone entirely when the list is collapsed. */}
      <div className="flex items-center gap-3 border-b border-line bg-surface px-4 py-2">
        <WorkflowPicker
          projects={projects}
          standaloneRuns={standaloneRuns}
          isLoading={isLoading}
        />
        <span className="truncate text-xs text-fgsubtle">
          {isLoading
            ? "Scanning your workspace…"
            : `${totalCount} in this workspace`}
        </span>
      </div>

      <div className="flex min-h-0 flex-1">
      {/* Workflow list — resizable and collapsible. A fixed width was wrong in
          both directions: too narrow for long workflow names, too wide once
          you're watching a run and want the space for the run itself. */}
      <ResizablePane storageKey="workflow-list">
        <div className="border-b border-line px-4 py-3">
          <h1 className="text-lg font-semibold text-fg">Workflows</h1>
          <p className="text-sm text-fgmuted">
            {isLoading
              ? "Scanning..."
              : `${totalCount} workflow${totalCount !== 1 ? "s" : ""} found`}
          </p>
        </div>

        {runsError && (
          <div className="m-4 rounded-md bg-red-50 dark:bg-rose-500/10 p-3 text-sm text-red-700 dark:text-rose-400">
            Failed to load workflows: {(runsError as Error).message}
          </div>
        )}

        <WorkflowList
          projects={projects}
          workflows={standaloneRuns}
        />
      </ResizablePane>

      {/* Detail panel. min-w-0 so a wide list shrinks this rather than
          pushing its content out of the viewport — flex items default to
          min-width:auto, which refuses to go below their content's width. */}
      <div className="min-w-0 flex-1">
        {selectedProjectId ? (
          // key forces a fresh mount per project so per-workflow UI state
          // (Output panel, planned run dir, selected run) doesn't leak from
          // the previously selected workflow.
          <WorkflowProjectDetail
            key={selectedProjectId}
            projectId={selectedProjectId}
          />
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
    </div>
  );
}
