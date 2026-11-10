"use client";

import { useEffect, useMemo, useState } from "react";
import {
  useWorkflowProjects,
  useProjectParams,
  useGenerateWorkflow,
  usePlanWorkflow,
  useSubmitWorkflowProject,
  useWorkflowJobs,
  useAnalyzeWorkflow,
  useDeleteWorkflow,
  useDeleteWorkflowProject,
  useRenameWorkflowProject,
} from "@/lib/hooks/use-workflows";
import type { WorkflowProjectRun } from "@/lib/api/client";
import { newestFirst } from "@/lib/workflows/run-order";
import * as api from "@/lib/api/client";
import type { JobFilesSource } from "@/components/workflows/job-files-panel";
import { AppLink } from "@/components/layout/app-link";
import { DagViewer } from "@/components/workflows/dag-viewer";
import { useWorkflowStore } from "@/lib/stores/workflow-store";
import { RunFlowPanel } from "@/components/workflows/run-flow-panel";
import { OutputsPanel } from "@/components/workflows/outputs-panel";
import { FailureCards } from "@/components/workflows/failure-cards";
import {
  ParamField,
  buildArgs,
  missingRequired,
  type ParamValues,
} from "@/components/workflows/param-field";
import { useActionToast } from "@/lib/hooks/use-action-toast";
import { useUIPreferences, useUpdateUIPreferences } from "@/lib/hooks/use-ui-prefs";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { RenameProjectDialog } from "@/components/workflows/rename-project-dialog";
import { apiUrl } from "@/lib/urls";
import { usePaneSize } from "@/lib/hooks/use-pane-size";
import { cn, formatDuration, formatExitCode, statusColor } from "@/lib/utils";
import { TONE_TEXT } from "@/lib/tone";
import { WhereItRan } from "@/components/workflows/where-it-ran";

// Generate, Build and Plan are steps toward Run, which is the one filled
// action. They used to be grey, sky and yellow, and yellow read as a
// warning (#548).
const SECONDARY_BTN =
  "rounded-md border border-line bg-surface px-4 py-2 text-sm text-fg hover:border-pegasus-300 hover:bg-base disabled:opacity-50";

interface Props {
  projectId: string;
}

// Recognize common pegasus-plan / pegasus-run failures in the raw output and
// return a short, actionable hint shown above the log. null = no hint.
function outputHint(output: string | null): string | null {
  if (!output) return null;
  const o = output;
  if (/already running/i.test(o) && /monitord\.pid/i.test(o)) {
    return "This run is already running — or a previous run left a stale lock. If it has finished, just click Run again to start a fresh run; otherwise remove the run's monitord.pid file and retry.";
  }
  if (/Illegal key .* for element workflow/i.test(o)) {
    return "pegasus-plan was handed a catalog file instead of the abstract workflow. Regenerate the workflow (Generate Workflow) and try again.";
  }
  if (/PegasusLite: exitcode|\.lof:.*No such file/i.test(o)) {
    return "A job failed inside the PegasusLite wrapper before your code ran. Open the run below and click Run Analyzer for the failure detail.";
  }
  if (/exit(code)?\s*[:=]?\s*[1-9]/i.test(o) && /fail/i.test(o)) {
    return "A job exited non-zero. Open the run below and click Run Analyzer to see which job failed and why.";
  }
  if (/not found|No such file or directory/i.test(o)) {
    return "A required file or command wasn't found. Check the paths/inputs and that the workflow was generated, then try again.";
  }
  return null;
}

export function WorkflowProjectDetail({ projectId }: Props) {
  const { data, refetch: refetchProjects } = useWorkflowProjects();
  const project = data?.projects.find((p) => p.project_id === projectId);
  // Collapsing the run setup is the point of the disclosure, but a collapsed
  // pane must not hide the fact that it is still incomplete.
  const [configCollapsed, setConfigCollapsed] = useState(false);
  const { data: params } = useProjectParams(projectId);

  const generate = useGenerateWorkflow();
  const plan = usePlanWorkflow();
  const submit = useSubmitWorkflowProject();
  const deleteRun = useDeleteWorkflow();
  const deleteProject = useDeleteWorkflowProject();
  const renameProject = useRenameWorkflowProject();
  const selectProject = useWorkflowStore((s) => s.selectProject);
  const runAction = useActionToast();
  // Phase 1 rollout flag (#241). Off by default, so this page is byte-for-byte
  // what it was until a user opts in from Settings.
  const { prefs } = useUIPreferences();
  const updatePrefs = useUpdateUIPreferences();

  const [output, setOutput] = useState<string | null>(null);
  const [outputCollapsed, setOutputCollapsed] = useState(false);
  // outputHint() is tuned for pegasus-plan/run logs; a Docker build log trips
  // its "exit"/"fail" patterns with the wrong "Run Analyzer" advice, so track
  // whether the current output is a build and suppress the hint for it.
  const [outputIsBuild, setOutputIsBuild] = useState(false);
  const [building, setBuilding] = useState(false);
  const [builtImage, setBuiltImage] = useState<string | null>(null);
  // Run dir produced by the last Plan, so Run can submit it via pegasus-run
  // instead of re-planning.
  const [plannedRunDir, setPlannedRunDir] = useState<string | null>(null);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  // The run pending a delete confirmation, or null when no dialog is open.
  const [runToDelete, setRunToDelete] = useState<{
    run_id: string;
    label: string;
  } | null>(null);
  const [confirmDeleteProject, setConfirmDeleteProject] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [renameError, setRenameError] = useState<string | null>(null);
  const [values, setValues] = useState<ParamValues>({});
  const [dag, setDag] = useState<{
    url: string;
    title: string;
    live: boolean;
  } | null>(null);

  const genParams = useMemo(
    () => params?.generator.params ?? [],
    [params]
  );
  const planParams = useMemo(() => params?.plan.params ?? [], [params]);
  const requiredMissing = useMemo(
    () => missingRequired([...genParams, ...planParams], values),
    [genParams, planParams, values]
  );
  const mutexGroups = params?.generator.mutex_required ?? [];

  // Initialize form values from discovered defaults once params load.
  useEffect(() => {
    const all = [...genParams, ...planParams];
    if (all.length === 0) return;
    setValues((prev) => {
      const next = { ...prev };
      for (const p of all) {
        if (!(p.dest in next)) {
          next[p.dest] = p.is_flag
            ? Boolean(p.default)
            : p.default == null
            ? ""
            : String(p.default);
        }
      }
      return next;
    });
  }, [genParams, planParams]);

  if (!project) {
    return (
      <div className="flex h-full items-center justify-center text-fgsubtle">
        Project not found
      </div>
    );
  }

  // A run still holding the folder's paths blocks a rename (the server
  // refuses too); disabling the button says so before the dialog opens.
  const projectBusy = project.runs.some(
    (r) => r.status === "running" || r.status === "planning"
  );

  const setValue = (dest: string, v: string | boolean) =>
    setValues((prev) => ({ ...prev, [dest]: v }));

  const planOpts = () => ({
    site: String(values["site"] ?? params?.sites?.default ?? "condorpool"),
    output_site: String(values["output_site"] ?? "local"),
  });

  const showOutput = (text: string) => {
    setOutput(text);
    setOutputCollapsed(false);
    setOutputIsBuild(false);
  };

  const handleGenerate = async () => {
    setOutput(null);
    // Regenerating invalidates any prior plan.
    setPlannedRunDir(null);
    const result = await runAction("Generate", () =>
      generate.mutateAsync({
        projectId,
        args: buildArgs(genParams, values),
        site: String(values["site"] ?? "") || undefined,
      })
    );
    if (result) showOutput(result.output);
  };

  const handlePlan = async () => {
    setOutput(null);
    const result = await runAction("Plan", () =>
      plan.mutateAsync({ projectId, opts: planOpts() })
    );
    if (result) {
      setPlannedRunDir(result.run_dir || null);
      showOutput(result.output);
    }
  };

  const handleRun = async () => {
    setOutput(null);
    const result = await runAction("Run", () =>
      submit.mutateAsync({
        projectId,
        opts: { ...planOpts(), run_dir: plannedRunDir ?? undefined },
      })
    );
    if (result) showOutput(result.output ?? "Submitted");
  };

  // Build the project's container image, streaming the build log into the
  // output panel as it happens (SSE over a fetch POST).
  //
  // Build only — no push. The workflow references the .sif by absolute path
  // (image_site="local") and Pegasus stages it like any other input, so a
  // registry round-trip buys nothing here and made "Build Image" fail on
  // registry problems that had no bearing on the image.
  const handleBuild = async () => {
    setBuilding(true);
    setBuiltImage(null);
    setOutputCollapsed(false);
    setOutputIsBuild(true);
    const lines: string[] = [];
    const append = (line: string) => {
      lines.push(line);
      setOutput(lines.join("\n"));
    };
    try {
      const res = await fetch(
        apiUrl(`workflows/projects/${encodeURIComponent(projectId)}/build`),
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ tag: "latest", push: false }),
        }
      );
      if (!res.ok || !res.body) {
        throw new Error(`${res.status}: ${await res.text().catch(() => "")}`);
      }
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const chunks = buf.split("\n\n");
        buf = chunks.pop() ?? "";
        for (const chunk of chunks) {
          const line = chunk.trim();
          if (!line.startsWith("data:")) continue; // ": ping" heartbeats
          const payload = line.slice(5).trim();
          if (payload === "[DONE]") continue;
          try {
            const ev = JSON.parse(payload);
            if (ev.phase) append(`── ${ev.phase} ──`);
            if (ev.line) append(ev.line);
            if (ev.status === "success") {
              setBuiltImage(ev.image);
              append(
                ev.pushed
                  ? `✓ built ${ev.image} and pushed ${ev.ref}`
                  : `✓ built ${ev.image}`
              );
            } else if (ev.status === "error") {
              append(`✗ ${ev.error}`);
            }
          } catch {
            // ignore malformed event
          }
        }
      }
    } catch (e) {
      append(`Build stream failed: ${String(e)}`);
    } finally {
      setBuilding(false);
    }
  };

  const handleDeleteProject = async () => {
    const result = await runAction("Delete workflow", () =>
      deleteProject.mutateAsync(projectId)
    );
    setConfirmDeleteProject(false);
    // Clear the selection: the project it pointed at no longer exists, and
    // leaving it selected renders "Project not found".
    if (result) selectProject(null);
  };

  const handleRenameProject = async (name: string) => {
    setRenameError(null);
    try {
      const res = await renameProject.mutateAsync({ projectId, name });
      setRenaming(false);
      // The old id no longer exists; follow the folder to its new name.
      selectProject(res.project_id);
    } catch (e) {
      setRenameError((e as Error).message);
    }
  };

  const handleDeleteRun = async () => {
    if (!runToDelete) return;
    const { run_id } = runToDelete;
    const result = await runAction("Delete run", () =>
      deleteRun.mutateAsync(run_id)
    );
    if (result && selectedRunId === run_id) setSelectedRunId(null);
    setRunToDelete(null);
  };

  const isBusy =
    generate.isPending || plan.isPending || submit.isPending || building;

  // The four Pegasus verbs and their supporting panels. In simple mode these
  // move wholesale into the Advanced disclosure rather than being rewritten,
  // so the Pegasus-literate path stays exactly as it is today.
  const advancedSections = (
    <>
      {/* Files section */}
      <div>
        <h3 className="mb-3 text-sm font-semibold text-fg">Project Files</h3>
        <div className="grid grid-cols-2 gap-2">
          <FileCheck
            label="workflow_generator.py"
            exists={project.has_generator}
          />
          <FileCheck label="workflow.yml" exists={project.has_workflow_yml} />
          <FileCheck label="Apptainer def" exists={project.has_container_def} />
        </div>
      </div>
      {/* Parameters are NOT repeated here (#314).
          "Set up your run" above already renders every generate parameter —
          the primary ones inline and the plumbing under "More options" — so
          this section used to show all of them a second time, with different
          headings, on the same page. Two editors for one value is worse than
          an inconvenient one. What is left here is the machinery that has no
          equivalent up there: the individual Pegasus steps, the plan/submit
          flags the run flow does not expose, and the raw command output. */}
      {planParams.length > 0 && (
        <div>
          <h3 className="mb-3 text-sm font-semibold text-fg">
            Plan and submit options
          </h3>
          <div className="rounded-md border border-line p-4">
            {mutexGroups.map((grp, i) => (
              <p
                key={i}
                className="mb-2 text-xs text-amber-600 dark:text-amber-400"
              >
                Provide exactly one of:{" "}
                {grp
                  .map((d) => genParams.find((p) => p.dest === d)?.flag ?? d)
                  .join(" or ")}
              </p>
            ))}
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              {planParams.map((p) => (
                <ParamField
                  key={p.dest}
                  param={p}
                  value={values[p.dest]}
                  onChange={(v) => setValue(p.dest, v)}
                />
              ))}
            </div>
          </div>
        </div>
      )}

      {/* Action buttons */}
      <div>
        <h3 className="mb-3 text-sm font-semibold text-fg">
          Actions
        </h3>
        <div className="flex flex-wrap gap-2">
          {project.has_generator && (
            <button
              onClick={handleGenerate}
              disabled={isBusy}
              className={SECONDARY_BTN}
            >
              {generate.isPending ? "Generating..." : "Generate Workflow"}
            </button>
          )}

          {project.has_container_def && (
            <button
              onClick={handleBuild}
              disabled={isBusy}
              title="Build the project's Apptainer definition into a .sif beside it (and publish it if a registry is set in Application Settings → Pegasus Options)"
              className={SECONDARY_BTN}
            >
              {building ? "Building..." : "Build Image"}
            </button>
          )}

          {project.has_workflow_yml && (
            <>
              <button
                onClick={handlePlan}
                disabled={isBusy}
                className={SECONDARY_BTN}
              >
                {plan.isPending ? "Planning..." : "Plan"}
              </button>
              <button
                onClick={handleRun}
                disabled={isBusy}
                title="Submit the planned workflow (pegasus-run) without re-planning"
                className="rounded-md bg-pegasus-600 px-4 py-2 text-sm text-white hover:bg-pegasus-700 disabled:opacity-50"
              >
                {submit.isPending ? "Running..." : "Run"}
              </button>
              <button
                onClick={() =>
                  setDag({
                    url: apiUrl(
                      `workflows/projects/${projectId}/graph.json`
                    ),
                    title: `${project.name} — workflow DAG`,
                    live: false,
                  })
                }
                className="rounded-md border border-pegasus-300 bg-surface px-4 py-2 text-sm text-pegasus-700 hover:bg-pegasus-50 dark:border-pegasus-700 dark:text-pegasus-300 dark:hover:bg-pegasus-400/10"
              >
                Visualize DAG
              </button>
            </>
          )}
        </div>
        {builtImage && (
          <p className="mt-2 text-xs text-fgmuted">
            Latest image:{" "}
            <span className="font-mono text-fg">{builtImage}</span>
          </p>
        )}
      </div>

      {/* Command output */}
      {output && (
        <div>
          <div className="mb-2 flex items-center justify-between">
            <h3 className="text-sm font-semibold text-fg">Output</h3>
            <div className="flex items-center gap-1">
              <button
                onClick={() => setOutputCollapsed((c) => !c)}
                className="rounded px-2 py-0.5 text-xs text-fgmuted hover:bg-base hover:text-fg"
                title={outputCollapsed ? "Expand output" : "Collapse output"}
              >
                {outputCollapsed ? "Expand" : "Collapse"}
              </button>
              <button
                onClick={() => setOutput(null)}
                className="rounded px-2 py-0.5 text-xs text-fgmuted hover:bg-base hover:text-fg"
                title="Close output"
                aria-label="Close output"
              >
                {"✕"}
              </button>
            </div>
          </div>
          {!outputCollapsed && !outputIsBuild && outputHint(output) && (
            <div className="mb-2 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-800 dark:border-amber-500/40 dark:bg-amber-500/10 dark:text-amber-300">
              {outputHint(output)}
            </div>
          )}
          {!outputCollapsed && (
            <pre className="max-h-64 w-full overflow-auto whitespace-pre-wrap [overflow-wrap:anywhere] break-all rounded-md bg-gray-900 p-4 text-xs text-green-400">
              {output}
            </pre>
          )}
        </div>
      )}
    </>
  );

  return (
    <div className="h-full overflow-auto">
      {/* Header */}
      <div className="border-b border-line bg-surface px-6 py-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          {/* A heading, not a picker. Choosing which analysis moved up to the
              page header (components/workflows/workflow-picker.tsx) — here it
              was only reachable once you had already chosen, and it vanished
              with this pane. */}
          <div className="min-w-0">
            <h2 className="truncate text-lg font-semibold text-fg">
              {project.name}
            </h2>
            <p className="truncate text-sm text-fgmuted">
              {project.project_dir}
            </p>
          </div>
          <span className="flex items-center gap-3">
            {/* Every workflow gets a way into its own folder: the files are
                what a user falls back to for anything the UI doesn't surface,
                and hunting for the path in JupyterLab's tree is the friction
                this removes. */}
            {project.jupyter_path && (
              <AppLink
                href={`/notebooks?path=${encodeURIComponent(project.jupyter_path)}`}
                title="Open this workflow's folder in JupyterLab"
                className="rounded-md border border-line px-3 py-1.5 text-sm font-medium text-fgmuted hover:border-pegasus-400 hover:text-pegasus-700 dark:hover:text-pegasus-300"
              >
                Open in JupyterLab
              </AppLink>
            )}
            <button
              onClick={() => {
                setRenameError(null);
                setRenaming(true);
              }}
              disabled={projectBusy}
              title={
                projectBusy
                  ? "Can't rename while a run is in progress"
                  : "Rename this workflow's folder"
              }
              className="rounded-md border border-line px-3 py-1.5 text-sm font-medium text-fgmuted hover:border-pegasus-400 hover:text-pegasus-700 disabled:cursor-not-allowed disabled:opacity-50 dark:hover:text-pegasus-300"
            >
              Rename
            </button>
            <button
              onClick={() => setConfirmDeleteProject(true)}
              title="Delete this workflow and everything in it"
              className="rounded-md border border-line px-3 py-1.5 text-sm font-medium text-fgmuted hover:border-red-400 hover:text-red-600 dark:hover:text-red-400"
            >
              Delete
            </button>
            <span
              className={cn(
                "rounded-full px-3 py-1 text-sm font-medium",
                statusColor(project.status)
              )}
            >
              {project.status}
            </span>
          </span>
        </div>
      </div>

      <div className="p-6 space-y-6">
        {/* One run-setup surface, whatever the interface preference (#314).
            It used to be either/or: the simplified interface got "Set up your
            run" *plus* an Advanced block that rendered the same parameters
            again, and turning the interface off replaced the run flow with
            that raw block. So a parameter had two editors in one mode and a
            different one in the other. Now the run flow always owns the
            parameters, and Advanced is only the machinery that has no
            equivalent in it — the individual steps and the raw output. */}
        <details
          open={!configCollapsed}
          onToggle={(e) => {
            const open = (e.currentTarget as HTMLDetailsElement).open;
            if (open === configCollapsed) setConfigCollapsed(!open);
          }}
          className="rounded-xl border border-line bg-surface"
        >
          <summary className="cursor-pointer list-none px-5 py-3">
            <span className="flex items-center gap-2">
              <span
                aria-hidden="true"
                className={cn(
                  "text-fgsubtle transition-transform",
                  configCollapsed && "-rotate-90"
                )}
              >
                ▾
              </span>
              <span className="text-base font-semibold text-fg">
                Set up your run
              </span>
              {configCollapsed && requiredMissing.length > 0 && (
                <span className="rounded-full bg-amber-100 px-2 py-0.5 text-xs font-semibold text-amber-800 dark:bg-amber-500/15 dark:text-amber-300">
                  {requiredMissing.length} still needed
                </span>
              )}
            </span>
          </summary>
          <div className="border-t border-line p-5 pt-4">
            <RunFlowPanel
              embedded
              projectId={projectId}
              params={params}
              values={values}
              onChange={setValue}
              onSubmitted={() => refetchProjects()}
            />
          </div>
        </details>

        <GraphPane
          projectId={projectId}
          projectName={project.name}
          runs={project.runs}
        />

        {/* Everything Pegasus-shaped lives behind one disclosure. Open state is
            a per-user preference, so a workflow engineer who wants it doesn't
            reopen it on every visit — and turning the simplified interface off
            opens it by default rather than replacing anything. */}
        <details
          open={prefs.advanced_open || !prefs.simple_run}
          onToggle={(e) => {
            const open = (e.currentTarget as HTMLDetailsElement).open;
            if (open !== prefs.advanced_open) {
              updatePrefs.mutate({ advanced_open: open });
            }
          }}
          className="rounded-xl border border-line bg-surface"
        >
          <summary className="cursor-pointer px-5 py-3 text-sm font-medium text-fgmuted hover:text-fg">
            Advanced — run the steps individually
          </summary>
          <div className="space-y-6 border-t border-line p-5">
            {advancedSections}
          </div>
        </details>

        {/* Submitted runs */}
        {project.runs.length > 0 && (
          <div>
            <h3 className="mb-3 text-sm font-semibold text-fg">
              Runs <span className="tabular-nums">({project.runs.length})</span>
            </h3>
            <ul className="divide-y divide-line rounded-md border border-line">
              {project.runs.map((run) => (
                <li key={run.run_id}>
                  {/* Row is a flex container, NOT a <button>: the DAG and
                      Delete actions are real sibling <button>s so the markup is
                      valid and both are keyboard-reachable (FE-9). */}
                  <div
                    className={cn(
                      "flex items-center justify-between gap-2 px-4 py-3 transition-colors hover:bg-base",
                      selectedRunId === run.run_id && "bg-pegasus-50 dark:bg-pegasus-400/10"
                    )}
                  >
                    <button
                      type="button"
                      onClick={() =>
                        setSelectedRunId(
                          selectedRunId === run.run_id ? null : run.run_id
                        )
                      }
                      aria-expanded={selectedRunId === run.run_id}
                      title={run.run_id}
                      className={cn(
                        "flex-1 truncate text-left text-sm text-fg",
                        // A run's own name is a label; only the id fallback is typed-exact.
                        !run.run_name && "font-mono"
                      )}
                    >
                      {run.run_name || `${run.run_id.slice(0, 12)}...`}
                    </button>
                    <span className="flex items-center gap-2">
                      {run.jupyter_path && (
                        <AppLink
                          href={`/notebooks?path=${encodeURIComponent(run.jupyter_path)}`}
                          title="Open this run's folder in JupyterLab"
                          className="rounded border border-line px-2 py-0.5 text-xs text-fgmuted hover:border-pegasus-400 hover:text-pegasus-700 dark:hover:text-pegasus-300"
                        >
                          Files
                        </AppLink>
                      )}
                      <button
                        type="button"
                        onClick={() =>
                          setDag({
                            url: apiUrl(`workflows/${run.run_id}/graph.json`),
                            title: `${run.run_name || run.run_id.slice(0, 12)} — DAG`,
                            live: true,
                          })
                        }
                        title="Visualize the planned DAG"
                        className="rounded border border-line px-2 py-0.5 text-xs text-fgmuted hover:border-pegasus-400 hover:text-pegasus-700 dark:hover:text-pegasus-300"
                      >
                        DAG
                      </button>
                      <span
                        className={cn(
                          "rounded-full px-2 py-0.5 text-xs font-medium",
                          statusColor(run.status)
                        )}
                      >
                        {run.status}
                      </span>
                      <button
                        type="button"
                        onClick={() =>
                          setRunToDelete({
                            run_id: run.run_id,
                            label: run.run_name || run.run_id.slice(0, 12),
                          })
                        }
                        title="Delete this run"
                        aria-label="Delete this run"
                        className="rounded border border-line px-2 py-0.5 text-xs text-fgmuted hover:border-red-400 hover:text-red-600 dark:hover:text-red-400"
                      >
                        Delete
                      </button>
                    </span>
                  </div>
                  {selectedRunId === run.run_id && (
                    <RunJobsPanel
                      runId={run.run_id}
                      runStatus={run.status}
                    />
                  )}
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>

      {dag && (
        <DagViewer
          graphUrl={dag.url}
          title={dag.title}
          live={dag.live}
          onClose={() => setDag(null)}
        />
      )}

      {renaming && (
        <RenameProjectDialog
          currentName={projectId}
          busy={renameProject.isPending}
          error={renameError}
          onRename={handleRenameProject}
          onCancel={() => setRenaming(false)}
        />
      )}

      {confirmDeleteProject && (
        <ConfirmDialog
          title={`Delete "${project.name}"?`}
          message={
            `This permanently deletes the workflow "${project.name}", its ` +
            `generator and any files you added to it, together with ` +
            `${project.runs.length} run${project.runs.length === 1 ? "" : "s"} ` +
            `and their results. Runs still going will be stopped first. ` +
            `Nothing here can be recovered afterwards.`
          }
          confirmLabel="Delete workflow"
          destructive
          busy={deleteProject.isPending}
          onConfirm={handleDeleteProject}
          onCancel={() => setConfirmDeleteProject(false)}
        />
      )}

      {runToDelete && (
        <ConfirmDialog
          title="Delete this run?"
          message={`This permanently deletes run "${runToDelete.label}" and its files from your workspace. If it is still running it will be cancelled first. This cannot be undone.`}
          confirmLabel="Delete run"
          destructive
          busy={deleteRun.isPending}
          onConfirm={handleDeleteRun}
          onCancel={() => setRunToDelete(null)}
        />
      )}
    </div>
  );
}

/**
 * The graph, always on screen (#314).
 *
 * It used to live behind a "DAG" button that opened a full-page modal, so the
 * shape of the thing you are running was something you had to go and ask for
 * and then dismiss. It defaults to the most recent run — that is the one you
 * came to look at — and falls back to the abstract graph before anything has
 * been submitted, so the pane is never empty.
 */
function GraphPane({
  projectId,
  projectName,
  runs,
}: {
  projectId: string;
  projectName: string;
  runs: WorkflowProjectRun[];
}) {
  // Newest first: "most recent" is what someone opening this page means by
  // "the run". Sorted here, not reversed: the API's order changed once
  // already, and reversing turned the oldest run into "(latest)" (#502).
  const ordered = useMemo(() => newestFirst(runs), [runs]);
  const [runId, setRunId] = useState<string | null>(null);
  // Follow the newest run as it appears, until the user picks one themselves.
  const [pinned, setPinned] = useState(false);
  const newest = ordered[0]?.run_id ?? null;
  const selected = pinned && runId ? runId : newest;
  const run = ordered.find((r) => r.run_id === selected);

  // Drag the bottom edge to resize; the graph is the thing you stare at, and
  // 420px was a guess that suited neither a laptop nor a big monitor. Height
  // persists per browser, like the other panes.
  const {
    size: height,
    collapsed,
    toggleCollapsed,
    dragging,
    startDrag,
    onHandleKeyDown,
    paneRef,
  } = usePaneSize({
    storageKey: "graph-pane",
    defaultSize: 420,
    minSize: 220,
    maxSize: 1200,
    axis: "y",
  });
  // Click a job to see its files. Per run, every location a file can be in
  // (results, scratch, project); before any run, only project inputs exist.
  const filesRunId = run?.run_id;
  const runStatus = run?.status;
  const jobFiles = useMemo<JobFilesSource>(
    () =>
      filesRunId
        ? {
            key: `run:${filesRunId}`,
            list: (jobId) => api.getRunJobFiles(filesRunId, jobId),
            files: (lfns) => api.getRunFilesInfo(filesRunId, lfns),
            url: (f, download = false) =>
              f.location && f.path
                ? api.runFileContentUrl(filesRunId, f.location, f.path, download)
                : null,
            live: runStatus === "running" || runStatus === "planning",
          }
        : {
            key: `project:${projectId}`,
            list: (jobId) => api.getProjectJobFiles(projectId, jobId),
            files: (lfns) => api.getProjectFilesInfo(projectId, lfns),
            url: (f, download = false) =>
              f.location === "project" && f.path
                ? api.projectFileContentUrl(projectId, f.path, download)
                : null,
            live: false,
          },
    [filesRunId, runStatus, projectId]
  );
  // Full screen for "I need the whole picture": the same viewer already has a
  // modal mode, so this is a prop rather than a second implementation.
  const [full, setFull] = useState(false);

  const url = run
    ? apiUrl(`workflows/${run.run_id}/graph.json`)
    : apiUrl(`workflows/projects/${projectId}/graph.json`);
  const title = run
    ? `${run.run_name || run.run_id.slice(0, 12)}`
    : `${projectName} — before it runs`;

  const picker =
    ordered.length > 0 ? (
      <label className="flex items-center gap-1.5 text-xs text-fgmuted">
        <span className="sr-only">Show the graph for</span>
        <select
          value={selected ?? ""}
          onChange={(e) => {
            setRunId(e.target.value || null);
            setPinned(true);
          }}
          className="h-7 max-w-[180px] rounded-md border border-line bg-surface px-1.5 text-xs text-fg outline-none focus:border-pegasus-400"
        >
          {ordered.map((r, i) => (
            <option key={r.run_id} value={r.run_id}>
              {r.run_name || r.run_id.slice(0, 12)}
              {i === 0 ? " (latest)" : ""} — {r.status}
            </option>
          ))}
          <option value="">Before it runs</option>
        </select>
      </label>
    ) : null;

  return (
    <div
      ref={paneRef}
      className="relative overflow-hidden rounded-xl border border-line bg-surface"
    >
      <div className="flex items-center gap-2 border-b border-line px-4 py-2">
        <button
          onClick={toggleCollapsed}
          aria-expanded={!collapsed}
          className="flex items-center gap-1.5 text-sm font-semibold text-fg"
        >
          <span
            aria-hidden="true"
            className={cn(
              "inline-block text-fgsubtle transition-transform",
              collapsed && "-rotate-90"
            )}
          >
            ▾
          </span>
          Graph
        </button>
        <span className="truncate text-xs text-fgsubtle">{title}</span>
        {!collapsed && (
          <button
            onClick={() => setFull(true)}
            title="Show the graph full screen"
            className="ml-auto rounded-md border border-line px-2 py-0.5 text-xs font-medium text-fgmuted hover:border-pegasus-400 hover:text-pegasus-700 dark:hover:text-pegasus-300"
          >
            Full screen
          </button>
        )}
      </div>
      {!collapsed && (
        <>
          <div style={{ height }}>
            <DagViewer
              // Remount on a different graph so the poll and the fit restart
              // cleanly rather than animating between two unrelated shapes.
              key={url}
              graphUrl={url}
              title={title}
              live={!!run}
              inline
              headerExtra={picker}
              jobFiles={jobFiles}
            />
          </div>

          {/* Drag handle on the bottom edge. Taller than it looks, because a
              1px target is a fine line to hit and this is not a precision
              task. */}
          <div
            role="separator"
            aria-orientation="horizontal"
            aria-label="Resize the graph"
            tabIndex={0}
            onMouseDown={(e) => {
              e.preventDefault();
              startDrag();
            }}
            onKeyDown={onHandleKeyDown}
            className={cn(
              "h-2 w-full cursor-row-resize",
              "hover:bg-pegasus-400/40 focus:bg-pegasus-400/60 focus:outline-none",
              dragging && "bg-pegasus-400/60"
            )}
          />
        </>
      )}

      {full && (
        <DagViewer
          graphUrl={url}
          title={title}
          live={!!run}
          headerExtra={picker}
          onClose={() => setFull(false)}
          jobFiles={jobFiles}
        />
      )}
    </div>
  );
}

function FileCheck({ label, exists }: { label: string; exists: boolean }) {
  return (
    <div className="flex items-center gap-2 rounded-md border border-line px-3 py-2 text-sm">
      <span className={exists ? TONE_TEXT.ok : TONE_TEXT.off}>
        {exists ? "\u2713" : "\u2717"}
      </span>
      <span className={exists ? "text-fg" : "text-fgsubtle"}>
        {label}
      </span>
    </div>
  );
}

function RunJobsPanel({
  runId,
  runStatus,
}: {
  runId: string;
  runStatus: string;
}) {
  const { data, isLoading } = useWorkflowJobs(runId);
  const analyze = useAnalyzeWorkflow();
  const runAction = useActionToast();
  const [analysis, setAnalysis] = useState<string | null>(null);
  // Results first — it is what the user ran the analysis to get. Without this
  // tab the outputs view was unreachable from the project page, i.e. from the
  // primary flow, which made the whole of Phase 4 invisible to exactly the
  // user it was for (#241).
  //
  // Unless the run failed: then there are no results to collect and the
  // diagnosis is what they need, so open on Steps where the failure cards are.
  const [tab, setTab] = useState<"results" | "steps">(
    runStatus === "failed" ? "steps" : "results"
  );

  const handleAnalyze = async () => {
    const result = await runAction("Analyze", () => analyze.mutateAsync(runId));
    if (result) setAnalysis(result.analysis);
  };

  return (
    <div className="border-t border-line bg-base">
      <div className="flex gap-1 border-b border-line px-4" role="tablist">
        {(["results", "steps"] as const).map((t) => (
          <button
            key={t}
            role="tab"
            aria-selected={tab === t}
            onClick={() => setTab(t)}
            className={cn(
              "-mb-px border-b-2 px-3 py-2 text-xs font-medium transition-colors",
              tab === t
                ? "border-pegasus-500 text-pegasus-700 dark:text-pegasus-300"
                : "border-transparent text-fgmuted hover:text-fg"
            )}
          >
            {t === "results" ? "Results" : "Steps"}
          </button>
        ))}
      </div>

      {tab === "results" && (
        <OutputsPanel runId={runId} runFinished={runStatus !== "running"} />
      )}

      <div className={cn("px-4 py-3", tab !== "steps" && "hidden")}>
      {/* What went wrong comes before the job table (#241, Phase 5): the
          table is how you look something up, not how you find out you have a
          problem. */}
      <div className="mb-3">
        <FailureCards runId={runId} runFinished={runStatus !== "running"} />
      </div>
      <div className="mb-2 flex items-center justify-between">
        <span className="text-xs font-medium text-fgmuted">Jobs</span>
        <button
          onClick={handleAnalyze}
          disabled={analyze.isPending}
          className="rounded bg-pegasus-600 px-2 py-1 text-xs text-white hover:bg-pegasus-700 disabled:opacity-50"
        >
          {analyze.isPending ? "Analyzing..." : "Run Analyzer"}
        </button>
      </div>

      {isLoading ? (
        <p className="text-xs text-fgmuted">Loading jobs...</p>
      ) : data?.jobs && data.jobs.length > 0 ? (
        <>
        <WhereItRan jobs={data.jobs} compact />
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-line text-left text-xs font-medium text-fgmuted">
              <th className="pb-1 pr-3">Job</th>
              <th className="pb-1 pr-3">Status</th>
              <th className="pb-1 pr-3">Ran on</th>
              <th className="pb-1 pr-3 text-right">Duration</th>
              <th className="pb-1 text-right">Exit</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {data.jobs.map((job: Record<string, unknown>, i: number) => (
              <tr key={i}>
                <td className="py-1 pr-3 font-mono">
                  {String(job.job_id ?? "")}
                </td>
                <td className="py-1 pr-3">
                  <span
                    className={cn(
                      "rounded-full px-1.5 py-0.5 text-xs font-medium",
                      statusColor(String(job.status ?? ""))
                    )}
                  >
                    {String(job.status ?? "")}
                  </span>
                </td>
                <td className="max-w-[12rem] truncate py-1 pr-3 font-mono text-fgmuted" title={job.host ? String(job.host) : undefined}>
                  {job.host ? String(job.host) : "\u2014"}
                </td>
                <td className="py-1 pr-3 text-right">
                  {job.duration != null ? formatDuration(Number(job.duration)) : "\u2014"}
                </td>
                <td className="py-1 text-right">
                  {formatExitCode(job.exitcode)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        </>
      ) : (
        <p className="text-xs text-fgmuted">No job data available.</p>
      )}

      {analysis && (
        <pre className="mt-2 max-h-48 w-full overflow-auto whitespace-pre-wrap [overflow-wrap:anywhere] break-all rounded bg-gray-900 p-3 text-xs text-green-400">
          {analysis}
        </pre>
      )}
      </div>
    </div>
  );
}
