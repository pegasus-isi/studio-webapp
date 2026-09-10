"use client";

import type { WorkflowRun, WorkflowProject } from "@/lib/api/client";
import { useWorkflowStore } from "@/lib/stores/workflow-store";
import { usePanelStore } from "@/lib/stores/panel-store";
import { cn, statusColor } from "@/lib/utils";
import { AppLink } from "@/components/layout/app-link";

interface Props {
  projects: WorkflowProject[];
  workflows: WorkflowRun[];
}

export function WorkflowList({ projects, workflows }: Props) {
  const selectedRunId = useWorkflowStore((s) => s.selectedRunId);
  const selectedProjectId = useWorkflowStore((s) => s.selectedProjectId);
  const selectWorkflow = useWorkflowStore((s) => s.selectWorkflow);
  const selectProject = useWorkflowStore((s) => s.selectProject);
  const openChatWith = usePanelStore((s) => s.openChatWith);

  if (projects.length === 0 && workflows.length === 0) {
    // An empty state is the one screen guaranteed to be read by someone who
    // has never done this before, and it used to answer them with a terminal
    // command (#241, Phase 6). Offer the two things that actually work from
    // here instead.
    return (
      <div className="p-4">
        <p className="text-sm font-medium text-fg">No workflows yet</p>
        <p className="mt-1 text-sm text-fgmuted">
          Start from a ready-made example, or describe what you want and let the
          assistant build it.
        </p>
        <div className="mt-3 flex flex-col gap-2">
          <AppLink
            href="/marketplace"
            className="rounded-md bg-pegasus-600 px-3 py-2 text-center text-sm font-medium text-white hover:bg-pegasus-700"
          >
            Browse examples
          </AppLink>
          <button
            onClick={() =>
              openChatWith("Build me a workflow that ")
            }
            className="rounded-md border border-line px-3 py-2 text-sm font-medium text-fgmuted hover:border-pegasus-400 hover:text-fg"
          >
            Describe one to the assistant
          </button>
        </div>
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
                      {proj.has_container_def && <span>apptainer</span>}
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
                      <span className="font-medium text-fg" title={wf.run_id}>
                        {wf.name}
                        {wf.run_name && (
                          <span className="ml-1.5 font-mono text-xs font-normal text-fgmuted">
                            {wf.run_name}
                          </span>
                        )}
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
                            className="h-1.5 rounded-full bg-pegasus-600 transition-all"
                            style={{ width: `${progress}%` }}
                          />
                        </div>
                      </div>
                    )}

                    {(wf.exec_site || (wf.backend && wf.backend !== "local")) && (
                      <div className="mt-1 flex items-center gap-2 text-xs text-fgsubtle">
                        {wf.exec_site && <span>Site: {wf.exec_site}</span>}
                        {wf.backend && wf.backend !== "local" && (
                          <span className="rounded-full border border-line px-1.5 py-px">
                            {wf.backend}
                          </span>
                        )}
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
