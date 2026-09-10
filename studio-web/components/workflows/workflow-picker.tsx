"use client";

/**
 * Which analysis you are looking at, always on screen.
 *
 * This started life inside the project detail header, which meant it only
 * appeared once you had already picked something — useless for picking, and
 * absent on the empty state. It also disappeared with the detail pane, so
 * collapsing the list left nothing at all naming the current analysis.
 *
 * At page level it does both jobs: it names the selection when there is one,
 * and it is a way to change or make a selection when the list is collapsed or
 * you never opened it.
 */

import type { WorkflowProject, WorkflowRun } from "@/lib/api/client";
import { useWorkflowStore } from "@/lib/stores/workflow-store";

export function WorkflowPicker({
  projects,
  standaloneRuns,
  isLoading,
}: {
  projects: WorkflowProject[];
  standaloneRuns: WorkflowRun[];
  isLoading: boolean;
}) {
  const selectedProjectId = useWorkflowStore((s) => s.selectedProjectId);
  const selectedRunId = useWorkflowStore((s) => s.selectedRunId);
  const selectProject = useWorkflowStore((s) => s.selectProject);
  const selectWorkflow = useWorkflowStore((s) => s.selectWorkflow);

  // One <select> over two different kinds of thing, so the value carries which
  // kind it is. `selectProject` and `selectWorkflow` each clear the other, so
  // the store cannot end up with both set.
  const value = selectedProjectId
    ? `p:${selectedProjectId}`
    : selectedRunId
      ? `r:${selectedRunId}`
      : "";

  const onChange = (next: string) => {
    if (next.startsWith("p:")) selectProject(next.slice(2));
    else if (next.startsWith("r:")) selectWorkflow(next.slice(2));
    else selectProject(null);
  };

  const empty = projects.length === 0 && standaloneRuns.length === 0;

  return (
    <div className="flex min-w-0 items-center gap-2">
      <label className="min-w-0">
        <span className="sr-only">Analysis</span>
        <select
          value={value}
          disabled={empty}
          onChange={(e) => onChange(e.target.value)}
          className="max-w-[320px] truncate rounded-lg border border-line bg-surface px-2 py-1 text-sm font-semibold text-fg outline-none focus:border-pegasus-400 disabled:text-fgsubtle"
        >
          {/* A placeholder rather than auto-selecting the first: landing on
              someone's first analysis without asking is a surprise, and the
              list is right there. */}
          <option value="">
            {isLoading
              ? "Loading…"
              : empty
                ? "No analyses yet"
                : "Choose an analysis…"}
          </option>
          {projects.length > 0 && (
            <optgroup label="Analyses">
              {projects.map((p) => (
                <option key={p.project_id} value={`p:${p.project_id}`}>
                  {p.name}
                </option>
              ))}
            </optgroup>
          )}
          {standaloneRuns.length > 0 && (
            <optgroup label="Runs outside an analysis">
              {standaloneRuns.map((r) => (
                <option key={r.run_id} value={`r:${r.run_id}`}>
                  {r.name}
                  {r.run_name ? ` / ${r.run_name}` : ""}
                </option>
              ))}
            </optgroup>
          )}
        </select>
      </label>
    </div>
  );
}
