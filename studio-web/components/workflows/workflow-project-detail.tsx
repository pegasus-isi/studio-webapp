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
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [values, setValues] = useState<ParamValues>({});

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
      <div className="flex h-full items-center justify-center text-gray-400">
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

  const handleGenerate = async () => {
    setOutput(null);
    const result = await generate.mutateAsync({
      projectId,
      args: buildArgs(genParams, values),
    });
    setOutput(result.output);
  };

  const handlePlan = async () => {
    setOutput(null);
    const result = await plan.mutateAsync({ projectId, opts: planOpts() });
    setOutput(result.output);
  };

  const handleSubmit = async () => {
    setOutput(null);
    const result = await submit.mutateAsync({ projectId, opts: planOpts() });
    setOutput(result.output ?? "Submitted");
  };

  const isBusy = generate.isPending || plan.isPending || submit.isPending;

  return (
    <div className="h-full overflow-auto">
      {/* Header */}
      <div className="border-b border-gray-200 bg-white px-6 py-4">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-lg font-semibold text-gray-900">
              {project.name}
            </h2>
            <p className="text-sm text-gray-500">{project.project_dir}</p>
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
          <h3 className="mb-3 text-sm font-semibold text-gray-700">
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
            <h3 className="mb-3 text-sm font-semibold text-gray-700">
              Parameters
            </h3>

            {genParams.length > 0 && (
              <div className="mb-4 rounded-md border border-gray-200 p-4">
                <p className="mb-3 text-xs font-medium uppercase tracking-wide text-gray-400">
                  Generate
                </p>
                {mutexGroups.map((grp, i) => (
                  <p key={i} className="mb-2 text-xs text-amber-600">
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
              <div className="rounded-md border border-gray-200 p-4">
                <p className="mb-3 text-xs font-medium uppercase tracking-wide text-gray-400">
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
          <h3 className="mb-3 text-sm font-semibold text-gray-700">
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
                  onClick={handleSubmit}
                  disabled={isBusy}
                  className="rounded-md bg-pegasus-600 px-4 py-2 text-sm text-white hover:bg-pegasus-700 disabled:opacity-50"
                >
                  {submit.isPending ? "Submitting..." : "Submit"}
                </button>
              </>
            )}
          </div>
        </div>

        {/* Command output */}
        {output && (
          <div>
            <h3 className="mb-2 text-sm font-semibold text-gray-700">
              Output
            </h3>
            <pre className="max-h-64 overflow-auto rounded-md bg-gray-900 p-4 text-xs text-green-400">
              {output}
            </pre>
          </div>
        )}

        {/* Submitted runs */}
        {project.runs.length > 0 && (
          <div>
            <h3 className="mb-3 text-sm font-semibold text-gray-700">
              Runs ({project.runs.length})
            </h3>
            <ul className="divide-y divide-gray-100 rounded-md border border-gray-200">
              {project.runs.map((run) => (
                <li key={run.run_id}>
                  <button
                    onClick={() =>
                      setSelectedRunId(
                        selectedRunId === run.run_id ? null : run.run_id
                      )
                    }
                    className={cn(
                      "w-full px-4 py-3 text-left transition-colors hover:bg-gray-50",
                      selectedRunId === run.run_id && "bg-pegasus-50"
                    )}
                  >
                    <div className="flex items-center justify-between">
                      <span className="font-mono text-sm text-gray-700">
                        {run.run_id.slice(0, 12)}...
                      </span>
                      <span
                        className={cn(
                          "rounded-full px-2 py-0.5 text-xs font-medium",
                          statusColor(run.status)
                        )}
                      >
                        {run.status}
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
      <label className="flex items-center gap-2 text-sm text-gray-700">
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
      <label className="text-xs font-medium text-gray-600">
        {label}
        {param.required && <span className="ml-1 text-red-500">*</span>}
      </label>
      {param.choices ? (
        <select
          value={String(value ?? "")}
          onChange={(e) => onChange(e.target.value)}
          className="rounded-md border border-gray-300 px-2 py-1 text-sm"
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
          className="rounded-md border border-gray-300 px-2 py-1 text-sm font-mono"
        />
      )}
      {param.help && (
        <span className="text-xs text-gray-400">{param.help}</span>
      )}
    </div>
  );
}

function FileCheck({ label, exists }: { label: string; exists: boolean }) {
  return (
    <div className="flex items-center gap-2 rounded-md border border-gray-200 px-3 py-2 text-sm">
      <span className={exists ? "text-green-600" : "text-gray-300"}>
        {exists ? "\u2713" : "\u2717"}
      </span>
      <span className={exists ? "text-gray-700" : "text-gray-400"}>
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
    <div className="border-t border-gray-100 px-4 py-3 bg-gray-50">
      <div className="mb-2 flex items-center justify-between">
        <span className="text-xs font-medium text-gray-500">Jobs</span>
        <button
          onClick={handleAnalyze}
          disabled={analyze.isPending}
          className="rounded bg-pegasus-600 px-2 py-1 text-xs text-white hover:bg-pegasus-700 disabled:opacity-50"
        >
          {analyze.isPending ? "Analyzing..." : "Run Analyzer"}
        </button>
      </div>

      {isLoading ? (
        <p className="text-xs text-gray-500">Loading jobs...</p>
      ) : data?.jobs && data.jobs.length > 0 ? (
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-gray-200 text-left text-xs font-medium text-gray-500">
              <th className="pb-1 pr-3">Job</th>
              <th className="pb-1 pr-3">Status</th>
              <th className="pb-1 pr-3">Duration</th>
              <th className="pb-1">Exit</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
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
        <p className="text-xs text-gray-500">No job data available.</p>
      )}

      {analysis && (
        <pre className="mt-2 max-h-48 overflow-auto rounded bg-gray-900 p-3 text-xs text-green-400">
          {analysis}
        </pre>
      )}
    </div>
  );
}
