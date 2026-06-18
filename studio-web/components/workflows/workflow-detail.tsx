"use client";

import { useWorkflowJobs, useAnalyzeWorkflow, useWorkflows } from "@/lib/hooks/use-workflows";
import { useWorkflowStore } from "@/lib/stores/workflow-store";
import { apiUrl } from "@/lib/urls";
import { cn, statusColor } from "@/lib/utils";
import { useEffect, useState, useRef } from "react";

interface Props {
  runId: string;
}

interface SSEEvent {
  type?: string;
  total_jobs?: number;
  completed_jobs?: number;
  failed_jobs?: number;
  running_jobs?: number;
  queued_jobs?: number;
  status?: string;
  [key: string]: unknown;
}

export function WorkflowDetail({ runId }: Props) {
  const { data: runsData } = useWorkflows();
  const { data, isLoading } = useWorkflowJobs(runId);
  const analyze = useAnalyzeWorkflow();
  const [analysis, setAnalysis] = useState<string | null>(null);

  const setEventSource = useWorkflowStore((s) => s.setEventSource);
  const [liveStats, setLiveStats] = useState<SSEEvent | null>(null);
  const eventSourceRef = useRef<EventSource | null>(null);

  // Find the run metadata
  const run = runsData?.workflows.find((w) => w.run_id === runId);
  const isRunning = run?.status === "running";

  // Connect to SSE for running workflows
  useEffect(() => {
    if (!isRunning) {
      setLiveStats(null);
      return;
    }

    const es = new EventSource(
      apiUrl(`/workflows/${encodeURIComponent(runId)}/events`)
    );
    eventSourceRef.current = es;
    setEventSource(es);

    es.onmessage = (event) => {
      if (event.data === "[DONE]") {
        es.close();
        return;
      }
      try {
        const parsed = JSON.parse(event.data) as SSEEvent;
        setLiveStats(parsed);
      } catch {
        // Ignore parse errors
      }
    };

    es.onerror = () => {
      es.close();
    };

    return () => {
      es.close();
      eventSourceRef.current = null;
    };
  }, [runId, isRunning, setEventSource]);

  const handleAnalyze = async () => {
    const result = await analyze.mutateAsync(runId);
    setAnalysis(result.analysis);
  };

  // Use live stats if available, otherwise fall back to run data
  const totalJobs = liveStats?.total_jobs ?? run?.total_jobs ?? 0;
  const completedJobs = liveStats?.completed_jobs ?? run?.completed_jobs ?? 0;
  const failedJobs = liveStats?.failed_jobs ?? run?.failed_jobs ?? 0;
  const runningJobs = liveStats?.running_jobs ?? 0;
  const queuedJobs = liveStats?.queued_jobs ?? 0;
  const progress = totalJobs > 0 ? Math.round((completedJobs / totalJobs) * 100) : 0;

  return (
    <div className="h-full overflow-auto">
      {/* Header */}
      <div className="border-b border-line bg-surface px-6 py-4">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-lg font-semibold text-fg">
              {run?.name ?? runId}
            </h2>
            {run && (
              <span
                className={cn(
                  "mt-1 inline-block rounded-full px-2 py-0.5 text-xs font-medium",
                  statusColor(run.status)
                )}
              >
                {run.status}
              </span>
            )}
          </div>
          <button
            onClick={handleAnalyze}
            disabled={analyze.isPending}
            className="rounded-md bg-pegasus-600 px-3 py-1.5 text-sm text-white hover:bg-pegasus-700 disabled:opacity-50"
          >
            {analyze.isPending ? "Analyzing..." : "Run Analyzer"}
          </button>
        </div>

        {/* Progress bar for running/completed workflows */}
        {totalJobs > 0 && (
          <div className="mt-4">
            <div className="flex justify-between text-sm text-fgmuted">
              <span>{completedJobs}/{totalJobs} jobs completed</span>
              <span>{progress}%</span>
            </div>
            <div className="mt-1 h-2 w-full rounded-full bg-muted">
              <div
                className={cn(
                  "h-2 rounded-full transition-all",
                  failedJobs > 0 ? "bg-red-500" : "bg-pegasus-500"
                )}
                style={{ width: `${progress}%` }}
              />
            </div>
            {(isRunning || runningJobs > 0 || queuedJobs > 0 || failedJobs > 0) && (
              <div className="mt-2 flex gap-4 text-xs text-fgmuted">
                {runningJobs > 0 && (
                  <span className="text-blue-600">{runningJobs} running</span>
                )}
                {queuedJobs > 0 && (
                  <span>{queuedJobs} queued</span>
                )}
                {failedJobs > 0 && (
                  <span className="text-red-600 dark:text-rose-400">{failedJobs} failed</span>
                )}
              </div>
            )}
          </div>
        )}
      </div>

      {/* Jobs table */}
      <div className="p-6">
        {isLoading ? (
          <p className="text-sm text-fgmuted">Loading jobs...</p>
        ) : data?.jobs && data.jobs.length > 0 ? (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-line text-left text-xs font-medium text-fgmuted">
                <th className="pb-2 pr-4">Job ID</th>
                <th className="pb-2 pr-4">Transformation</th>
                <th className="pb-2 pr-4">Site</th>
                <th className="pb-2 pr-4">Status</th>
                <th className="pb-2 pr-4">Duration</th>
                <th className="pb-2">Exit Code</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {data.jobs.map((job: Record<string, unknown>, i: number) => (
                <tr key={i} className="hover:bg-base">
                  <td className="py-2 pr-4 font-mono text-xs">
                    {String(job.job_id ?? "")}
                  </td>
                  <td className="py-2 pr-4">{String(job.transformation ?? "")}</td>
                  <td className="py-2 pr-4">{String(job.site ?? "\u2014")}</td>
                  <td className="py-2 pr-4">
                    <span
                      className={cn(
                        "rounded-full px-2 py-0.5 text-xs font-medium",
                        statusColor(String(job.status ?? ""))
                      )}
                    >
                      {String(job.status ?? "")}
                    </span>
                  </td>
                  <td className="py-2 pr-4 font-mono text-xs">
                    {job.duration != null ? `${job.duration}s` : "\u2014"}
                  </td>
                  <td className="py-2 font-mono text-xs">
                    {job.exitcode != null ? String(job.exitcode) : "\u2014"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="text-sm text-fgmuted">
            No job data available. The workflow may not have a stampede database yet.
          </p>
        )}

        {/* Analysis output */}
        {analysis && (
          <div className="mt-6">
            <h3 className="mb-2 text-sm font-semibold text-fg">
              Analysis
            </h3>
            <pre className="max-h-96 w-full overflow-auto whitespace-pre-wrap [overflow-wrap:anywhere] break-all rounded-md bg-gray-900 p-4 text-xs text-green-400">
              {analysis}
            </pre>
          </div>
        )}
      </div>
    </div>
  );
}
