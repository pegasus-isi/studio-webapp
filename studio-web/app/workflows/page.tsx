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
      <div className="w-96 border-r border-gray-200 bg-white">
        <div className="border-b border-gray-200 px-4 py-3">
          <h1 className="text-lg font-semibold text-gray-900">Workflows</h1>
          <p className="text-sm text-gray-500">
            {isLoading
              ? "Scanning..."
              : `${totalCount} workflow${totalCount !== 1 ? "s" : ""} found`}
          </p>
        </div>

        {runsError && (
          <div className="m-4 rounded-md bg-red-50 p-3 text-sm text-red-700">
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
          <div className="flex h-full items-center justify-center text-gray-400">
            Select a workflow to view details
          </div>
        )}
      </div>
    </div>
  );
}
