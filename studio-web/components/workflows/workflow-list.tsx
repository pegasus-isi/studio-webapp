"use client";

import type { WorkflowRun, WorkflowProject } from "@/lib/api/client";
import { useWorkflowStore } from "@/lib/stores/workflow-store";
import { cn, statusColor } from "@/lib/utils";

interface Props {
  projects: WorkflowProject[];
  workflows: WorkflowRun[];
}

export function WorkflowList({ projects, workflows }: Props) {
  const selectedRunId = useWorkflowStore((s) => s.selectedRunId);
  const selectedProjectId = useWorkflowStore((s) => s.selectedProjectId);
  const selectWorkflow = useWorkflowStore((s) => s.selectWorkflow);
  const selectProject = useWorkflowStore((s) => s.selectProject);

  if (projects.length === 0 && workflows.length === 0) {
    return (
      <div className="p-4 text-sm text-fgmuted">
        No workflows discovered. Use the AI assistant to scaffold a workflow in{" "}
        <code className="rounded bg-muted px-1">~/work/workflows/</code>{" "}
        or run{" "}
        <code className="rounded bg-muted px-1">pegasus-plan --submit</code>{" "}
        in the terminal.
      </div>
    );
  }

  return (
    <div className="overflow-y-auto">
      {/* Projects section */}
      {projects.length > 0 && (
        <div>
          <div className="px-4 py-2 text-xs font-semibold uppercase tracking-wider text-fgsubtle">
            Projects
          </div>
          <ul className="divide-y divide-line">
            {projects.map((proj) => {
              const active = selectedProjectId === proj.project_id;

              // Compute progress from runs
              const totalJobs = proj.runs.reduce(
                (sum, r) => sum + (("total_jobs" in r ? (r as unknown as WorkflowRun).total_jobs : 0)),
                0
              );

              return (
                <li key={proj.project_id}>
                  <button
                    onClick={() => selectProject(proj.project_id)}
                    className={cn(
                      "w-full px-4 py-3 text-left transition-colors",
                      active ? "bg-pegasus-50 dark:bg-pegasus-400/10" : "hover:bg-base"
                    )}
                  >
                    <div className="flex items-center justify-between">
                      <span className="font-medium text-fg">
                        {proj.name}
                      </span>
                      <span
                        className={cn(
                          "rounded-full px-2 py-0.5 text-xs font-medium",
                          statusColor(proj.status)
                        )}
                      >
                        {proj.status}
                      </span>
                    </div>

                    <div className="mt-1 flex items-center gap-2 text-xs text-fgsubtle">
                      {proj.has_generator && <span>generator</span>}
                      {proj.has_workflow_yml && <span>yml</span>}
                      {proj.has_dockerfile && <span>docker</span>}
                      {proj.runs.length > 0 && (
                        <span className="ml-auto">
                          {proj.runs.length} run{proj.runs.length !== 1 ? "s" : ""}
                        </span>
                      )}
                    </div>
                  </button>
                </li>
              );
            })}
          </ul>
        </div>
      )}

      {/* Standalone runs section */}
      {workflows.length > 0 && (
        <div>
          <div className="px-4 py-2 text-xs font-semibold uppercase tracking-wider text-fgsubtle">
            {projects.length > 0 ? "Other Runs" : "Runs"}
          </div>
          <ul className="divide-y divide-line">
            {workflows.map((wf) => {
              const active = selectedRunId === wf.run_id;
              const progress =
                wf.total_jobs > 0
                  ? Math.round((wf.completed_jobs / wf.total_jobs) * 100)
                  : 0;

              return (
                <li key={wf.run_id}>
                  <button
                    onClick={() => selectWorkflow(wf.run_id)}
                    className={cn(
                      "w-full px-4 py-3 text-left transition-colors",
                      active ? "bg-pegasus-50 dark:bg-pegasus-400/10" : "hover:bg-base"
                    )}
                  >
                    <div className="flex items-center justify-between">
                      <span className="font-medium text-fg">
                        {wf.name}
                      </span>
                      <span
                        className={cn(
                          "rounded-full px-2 py-0.5 text-xs font-medium",
                          statusColor(wf.status)
                        )}
                      >
                        {wf.status}
                      </span>
                    </div>

                    {wf.total_jobs > 0 && (
                      <div className="mt-2">
                        <div className="flex justify-between text-xs text-fgmuted">
                          <span>
                            {wf.completed_jobs}/{wf.total_jobs} jobs
                          </span>
                          {wf.failed_jobs > 0 && (
                            <span className="text-red-600 dark:text-rose-400">
                              {wf.failed_jobs} failed
                            </span>
                          )}
                        </div>
                        <div className="mt-1 h-1.5 w-full rounded-full bg-muted">
                          <div
                            className="h-1.5 rounded-full bg-pegasus-500 transition-all"
                            style={{ width: `${progress}%` }}
                          />
                        </div>
                      </div>
                    )}

                    {wf.exec_site && (
                      <div className="mt-1 text-xs text-fgsubtle">
                        Site: {wf.exec_site}
                      </div>
                    )}
                  </button>
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </div>
  );
}
