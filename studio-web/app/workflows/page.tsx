"use client";

import { useWorkflows, useWorkflowProjects } from "@/lib/hooks/use-workflows";
import { WorkflowList } from "@/components/workflows/workflow-list";
import { WorkflowDetail } from "@/components/workflows/workflow-detail";
import { WorkflowProjectDetail } from "@/components/workflows/workflow-project-detail";
import { useWorkflowStore } from "@/lib/stores/workflow-store";

export default function WorkflowsPage() {
  const { data: runsData, isLoading: runsLoading, error: runsError } = useWorkflows();
  const { data: projectsData, isLoading: projectsLoading } = useWorkflowProjects();
  const selectedRunId = useWorkflowStore((s) => s.selectedRunId);
  const selectedProjectId = useWorkflowStore((s) => s.selectedProjectId);

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
    <div className="flex h-full">
      {/* Workflow list */}
      <div className="w-96 border-r border-line bg-surface">
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
      </div>

      {/* Detail panel */}
      <div className="flex-1">
        {selectedProjectId ? (
          <WorkflowProjectDetail projectId={selectedProjectId} />
        ) : selectedRunId ? (
          <WorkflowDetail runId={selectedRunId} />
        ) : (
          <div className="flex h-full items-center justify-center text-fgsubtle">
            Select a workflow to view details
          </div>
        )}
      </div>
    </div>
  );
}
