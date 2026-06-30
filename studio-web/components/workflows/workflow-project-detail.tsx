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
} from "@/lib/hooks/use-workflows";
import type { WorkflowParam } from "@/lib/api/client";
import { DagViewer } from "@/components/workflows/dag-viewer";
import { apiUrl } from "@/lib/urls";
import { cn, statusColor } from "@/lib/utils";

interface Props {
  projectId: string;
}

type ParamValues = Record<string, string | boolean>;

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

  const [output, setOutput] = useState<string | null>(null);
  const [outputCollapsed, setOutputCollapsed] = useState(false);
  // Run dir produced by the last Plan, so Run can submit it via pegasus-run
  // instead of re-planning.
  const [plannedRunDir, setPlannedRunDir] = useState<string | null>(null);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
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
  };

  const handleGenerate = async () => {
    setOutput(null);
    // Regenerating invalidates any prior plan.
    setPlannedRunDir(null);
    const result = await generate.mutateAsync({
      projectId,
      args: buildArgs(genParams, values),
    });
    showOutput(result.output);
  };

  const handlePlan = async () => {
    setOutput(null);
    const result = await plan.mutateAsync({ projectId, opts: planOpts() });
    setPlannedRunDir(result.run_dir || null);
    showOutput(result.output);
  };

  const handleRun = async () => {
    setOutput(null);
    const result = await submit.mutateAsync({
      projectId,
      opts: { ...planOpts(), run_dir: plannedRunDir ?? undefined },
    });
    showOutput(result.output ?? "Submitted");
  };

  const isBusy = generate.isPending || plan.isPending || submit.isPending;

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
              label="Dockerfile"
              exists={project.has_dockerfile}
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
                  <button
                    onClick={() =>
                      setSelectedRunId(
                        selectedRunId === run.run_id ? null : run.run_id
                      )
                    }
                    className={cn(
                      "w-full px-4 py-3 text-left transition-colors hover:bg-base",
                      selectedRunId === run.run_id && "bg-pegasus-50 dark:bg-pegasus-400/10"
                    )}
                  >
                    <div className="flex items-center justify-between">
                      <span
                        className="font-mono text-sm text-fg"
                        title={run.run_id}
                      >
                        {run.run_name || `${run.run_id.slice(0, 12)}...`}
                      </span>
                      <span className="flex items-center gap-2">
                        <span
                          onClick={(e) => {
                            e.stopPropagation();
                            setDag({
                              url: apiUrl(
                                `workflows/${run.run_id}/graph.json`
                              ),
                              title: `${run.run_name || run.run_id.slice(0, 12)} — DAG`,
                              live: true,
                            });
                          }}
                          title="Visualize the planned DAG"
                          className="cursor-pointer rounded border border-line px-2 py-0.5 text-xs text-fgmuted hover:border-pegasus-400 hover:text-pegasus-700 dark:hover:text-pegasus-300"
                        >
                          DAG
                        </span>
                        <span
                          className={cn(
                            "rounded-full px-2 py-0.5 text-xs font-medium",
                            statusColor(run.status)
                          )}
                        >
                          {run.status}
                        </span>
                      </span>
                    </div>
                  </button>
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
          placeholder={param.default == null ? "" : String(param.default)}
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
  const [analysis, setAnalysis] = useState<string | null>(null);

  const handleAnalyze = async () => {
    const result = await analyze.mutateAsync(runId);
    setAnalysis(result.analysis);
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
