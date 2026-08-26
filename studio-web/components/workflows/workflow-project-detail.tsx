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
} from "@/lib/hooks/use-workflows";
import type { WorkflowParam } from "@/lib/api/client";
import { DagViewer } from "@/components/workflows/dag-viewer";
import { useActionToast } from "@/lib/hooks/use-action-toast";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { apiUrl } from "@/lib/urls";
import { cn, statusColor } from "@/lib/utils";

interface Props {
  projectId: string;
}

type ParamValues = Record<string, string | boolean>;

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

function buildArgs(params: WorkflowParam[], values: ParamValues): string[] {
  const args: string[] = [];
  for (const p of params) {
    const v = values[p.dest];
    if (p.is_flag) {
      if (v === true) args.push(p.flag);
    } else {
      const s = (v ?? "").toString().trim();
      if (s !== "") args.push(p.flag, s);
    }
  }
  return args;
}

export function WorkflowProjectDetail({ projectId }: Props) {
  const { data } = useWorkflowProjects();
  const project = data?.projects.find((p) => p.project_id === projectId);
  const { data: params } = useProjectParams(projectId);

  const generate = useGenerateWorkflow();
  const plan = usePlanWorkflow();
  const submit = useSubmitWorkflowProject();
  const deleteRun = useDeleteWorkflow();
  const runAction = useActionToast();

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

  const setValue = (dest: string, v: string | boolean) =>
    setValues((prev) => ({ ...prev, [dest]: v }));

  const planOpts = () => ({
    site: String(values["site"] ?? "condorpool"),
    output_site: String(values["output_site"] ?? "local"),
  });

  const showOutput = (text: string) => {
    setOutput(text);
    setOutputCollapsed(false);
    setOutputIsBuild(false);
  };

  // Failures persist in the output panel instead of only flashing a toast:
  // with a remote backend an action can spend a minute pushing the project
  // before failing, and a 4-second toast after that reads as a dead button.
  const failureText = (label: string, e: unknown) =>
    `${label} failed: ${e instanceof Error ? e.message : String(e)}`;

  const handleGenerate = async () => {
    setOutput(null);
    // Regenerating invalidates any prior plan.
    setPlannedRunDir(null);
    try {
      const result = await generate.mutateAsync({
        projectId,
        args: buildArgs(genParams, values),
      });
      showOutput(result.output);
    } catch (e) {
      showOutput(failureText("Generate", e));
    }
  };

  const handlePlan = async () => {
    setOutput(null);
    try {
      const result = await plan.mutateAsync({ projectId, opts: planOpts() });
      setPlannedRunDir(result.run_dir || null);
      showOutput(result.output);
    } catch (e) {
      showOutput(failureText("Plan", e));
    }
  };

  const handleRun = async () => {
    setOutput(null);
    try {
      const result = await submit.mutateAsync({
        projectId,
        opts: { ...planOpts(), run_dir: plannedRunDir ?? undefined },
      });
      showOutput(result.output ?? "Submitted");
    } catch (e) {
      showOutput(failureText("Run", e));
    }
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

  return (
    <div className="h-full overflow-auto">
      {/* Header */}
      <div className="border-b border-line bg-surface px-6 py-4">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-lg font-semibold text-fg">
              {project.name}
            </h2>
            <p className="text-sm text-fgmuted">{project.project_dir}</p>
          </div>
          <span
            className={cn(
              "rounded-full px-3 py-1 text-sm font-medium",
              statusColor(project.status)
            )}
          >
            {project.status}
          </span>
        </div>
      </div>

      <div className="p-6 space-y-6">
        {/* Files section */}
        <div>
          <h3 className="mb-3 text-sm font-semibold text-fg">
            Project Files
          </h3>
          <div className="grid grid-cols-2 gap-2">
            <FileCheck
              label="workflow_generator.py"
              exists={project.has_generator}
            />
            <FileCheck
              label="workflow.yml"
              exists={project.has_workflow_yml}
            />
            <FileCheck
              label="Apptainer def"
              exists={project.has_container_def}
            />
          </div>
        </div>

        {/* Parameters (discovered per-workflow from the generator's argparse) */}
        {(genParams.length > 0 || planParams.length > 0) && (
          <div>
            <h3 className="mb-3 text-sm font-semibold text-fg">
              Parameters
            </h3>

            {genParams.length > 0 && (
              <div className="mb-4 rounded-md border border-line p-4">
                <p className="mb-3 text-xs font-medium uppercase tracking-wide text-fgsubtle">
                  Generate
                </p>
                {mutexGroups.map((grp, i) => (
                  <p key={i} className="mb-2 text-xs text-amber-600 dark:text-amber-400">
                    Provide exactly one of:{" "}
                    {grp
                      .map((d) => genParams.find((p) => p.dest === d)?.flag ?? d)
                      .join(" or ")}
                  </p>
                ))}
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                  {genParams.map((p) => (
                    <ParamField
                      key={p.dest}
                      param={p}
                      value={values[p.dest]}
                      onChange={(v) => setValue(p.dest, v)}
                    />
                  ))}
                </div>
              </div>
            )}

            {planParams.length > 0 && (
              <div className="rounded-md border border-line p-4">
                <p className="mb-3 text-xs font-medium uppercase tracking-wide text-fgsubtle">
                  Plan / Submit
                </p>
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
            )}
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
                className="rounded-md bg-gray-600 px-4 py-2 text-sm text-white hover:bg-gray-700 disabled:opacity-50"
              >
                {generate.isPending ? "Generating..." : "Generate Workflow"}
              </button>
            )}

            {project.has_container_def && (
              <button
                onClick={handleBuild}
                disabled={isBusy}
                title="Build the project's Apptainer definition into a .sif beside it (and publish it if a registry is set in Settings → Pegasus Options)"
                className="rounded-md bg-sky-700 px-4 py-2 text-sm text-white hover:bg-sky-800 disabled:opacity-50"
              >
                {building ? "Building..." : "Build Image"}
              </button>
            )}

            {project.has_workflow_yml && (
              <>
                <button
                  onClick={handlePlan}
                  disabled={isBusy}
                  className="rounded-md bg-yellow-600 px-4 py-2 text-sm text-white hover:bg-yellow-700 disabled:opacity-50"
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

        {/* Submitted runs */}
        {project.runs.length > 0 && (
          <div>
            <h3 className="mb-3 text-sm font-semibold text-fg">
              Runs ({project.runs.length})
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
                      className="flex-1 truncate text-left font-mono text-sm text-fg"
                    >
                      {run.run_name || `${run.run_id.slice(0, 12)}...`}
                    </button>
                    <span className="flex items-center gap-2">
                      {run.backend && run.backend !== "local" && (
                        <span className="rounded-full border border-line px-1.5 py-px text-xs text-fgsubtle">
                          {run.backend}
                        </span>
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
                    <RunJobsPanel runId={run.run_id} />
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

function ParamField({
  param,
  value,
  onChange,
}: {
  param: WorkflowParam;
  value: string | boolean | undefined;
  onChange: (v: string | boolean) => void;
}) {
  const label = param.flag.replace(/^-+/, "").replace(/-/g, " ");

  if (param.is_flag) {
    return (
      <label className="flex items-center gap-2 text-sm text-fg">
        <input
          type="checkbox"
          checked={Boolean(value)}
          onChange={(e) => onChange(e.target.checked)}
        />
        <span title={param.help}>{label}</span>
      </label>
    );
  }

  return (
    <div className="flex flex-col gap-1">
      <label className="text-xs font-medium text-fgmuted">
        {label}
        {param.required && <span className="ml-1 text-red-500">*</span>}
      </label>
      {param.choices ? (
        <select
          value={String(value ?? "")}
          onChange={(e) => onChange(e.target.value)}
          className="rounded-md border border-line px-2 py-1 text-sm"
        >
          {param.choices.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
      ) : (
        <input
          type="text"
          value={String(value ?? "")}
          // Prefix so a grayed default reads as guidance, not a filled value.
          placeholder={
            param.default == null
              ? param.required
                ? "required"
                : "optional"
              : `default: ${param.default}`
          }
          onChange={(e) => onChange(e.target.value)}
          className="rounded-md border border-line px-2 py-1 text-sm font-mono"
        />
      )}
      {param.help && (
        <span className="text-xs text-fgsubtle">{param.help}</span>
      )}
    </div>
  );
}

function FileCheck({ label, exists }: { label: string; exists: boolean }) {
  return (
    <div className="flex items-center gap-2 rounded-md border border-line px-3 py-2 text-sm">
      <span className={exists ? "text-green-600" : "text-gray-300"}>
        {exists ? "\u2713" : "\u2717"}
      </span>
      <span className={exists ? "text-fg" : "text-fgsubtle"}>
        {label}
      </span>
    </div>
  );
}

function RunJobsPanel({ runId }: { runId: string }) {
  const { data, isLoading } = useWorkflowJobs(runId);
  const analyze = useAnalyzeWorkflow();
  const runAction = useActionToast();
  const [analysis, setAnalysis] = useState<string | null>(null);

  const handleAnalyze = async () => {
    const result = await runAction("Analyze", () => analyze.mutateAsync(runId));
    if (result) setAnalysis(result.analysis);
  };

  return (
    <div className="border-t border-line px-4 py-3 bg-base">
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
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-line text-left text-xs font-medium text-fgmuted">
              <th className="pb-1 pr-3">Job</th>
              <th className="pb-1 pr-3">Status</th>
              <th className="pb-1 pr-3">Duration</th>
              <th className="pb-1">Exit</th>
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
                <td className="py-1 pr-3 font-mono">
                  {job.duration != null ? `${job.duration}s` : "\u2014"}
                </td>
                <td className="py-1 font-mono">
                  {job.exitcode != null ? String(job.exitcode) : "\u2014"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <p className="text-xs text-fgmuted">No job data available.</p>
      )}

      {analysis && (
        <pre className="mt-2 max-h-48 w-full overflow-auto whitespace-pre-wrap [overflow-wrap:anywhere] break-all rounded bg-gray-900 p-3 text-xs text-green-400">
          {analysis}
        </pre>
      )}
    </div>
  );
}
