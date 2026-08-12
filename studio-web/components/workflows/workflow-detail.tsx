"use client";

import {
  useWorkflowJobs,
  useAnalyzeWorkflow,
  useWorkflows,
  useWorkflowEstimate,
  useDeleteWorkflow,
} from "@/lib/hooks/use-workflows";
import { useWorkflowStore } from "@/lib/stores/workflow-store";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { apiUrl } from "@/lib/urls";
import { cn, formatDuration, statusColor } from "@/lib/utils";
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
  // Find the run metadata first — job polling is gated on its status.
  const run = runsData?.workflows.find((w) => w.run_id === runId);
  const isRunning = run?.status === "running";

  // Gate job polling: once the run is terminal its jobs are final (FE-9).
  const { data, isLoading } = useWorkflowJobs(runId, isRunning);
  const analyze = useAnalyzeWorkflow();
  const deleteRun = useDeleteWorkflow();
  const selectWorkflow = useWorkflowStore((s) => s.selectWorkflow);
  const [analysis, setAnalysis] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const setEventSource = useWorkflowStore((s) => s.setEventSource);
  const [liveStats, setLiveStats] = useState<SSEEvent | null>(null);
  const eventSourceRef = useRef<EventSource | null>(null);

  // Remaining-time estimate (historical-mean heuristic) — running runs only.
  const { data: estimateData } = useWorkflowEstimate(runId, isRunning);
  const estimate = isRunning ? estimateData?.estimate ?? null : null;

  // Connect to SSE for running workflows
  useEffect(() => {
    if (!isRunning) {
      setLiveStats(null);
      return;
    }

    // Reconnect with capped backoff instead of killing the stream on the first
    // transient error (FE-5). The old `onerror = () => es.close()` also
    // disabled the browser's native retry, so one blip froze the live view
    // until the modal was reopened.
    let cancelled = false;
    let attempts = 0;
    let reconnectTimer: ReturnType<typeof setTimeout> | undefined;

    const connect = () => {
      if (cancelled) return;
      const es = new EventSource(
        apiUrl(`/workflows/${encodeURIComponent(runId)}/events`)
      );
      eventSourceRef.current = es;
      setEventSource(es);

      es.onmessage = (event) => {
        if (event.data === "[DONE]") {
          es.close(); // run finished — no reconnect
          return;
        }
        try {
          const parsed = JSON.parse(event.data) as SSEEvent;
          attempts = 0; // stream healthy again
          setLiveStats(parsed);
        } catch {
          // Ignore parse errors
        }
      };

      es.onerror = () => {
        es.close();
        if (cancelled) return;
        // Drop the frozen snapshot so the 5s poll's fresh numbers show through
        // instead of stale liveStats masking them; then reconnect.
        setLiveStats(null);
        const delay = Math.min(1000 * 2 ** attempts, 15000);
        attempts += 1;
        reconnectTimer = setTimeout(connect, delay);
      };
    };

    connect();

    return () => {
      cancelled = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      eventSourceRef.current?.close();
      eventSourceRef.current = null;
    };
  }, [runId, isRunning, setEventSource]);

  const handleAnalyze = async () => {
    const result = await analyze.mutateAsync(runId);
    setAnalysis(result.analysis);
  };

  const handleDelete = async () => {
    try {
      await deleteRun.mutateAsync(runId);
      selectWorkflow(null); // run is gone — clear the now-dangling selection
    } finally {
      setConfirmDelete(false);
    }
  };

  // Failed jobs with captured stderr feed the Diagnostics panel below.
  const failedJobDetails = (data?.jobs ?? []).filter(
    (job: Record<string, unknown>) => job.status === "failed" && job.stderr
  );

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
          <div className="flex items-center gap-2">
            <button
              onClick={handleAnalyze}
              disabled={analyze.isPending}
              className="rounded-md bg-pegasus-600 px-3 py-1.5 text-sm text-white hover:bg-pegasus-700 disabled:opacity-50"
            >
              {analyze.isPending ? "Analyzing..." : "Run Analyzer"}
            </button>
            <button
              onClick={() => setConfirmDelete(true)}
              title="Delete this run and its files"
              className="rounded-md border border-line px-3 py-1.5 text-sm font-medium text-fgmuted hover:border-red-400 hover:text-red-600 dark:hover:text-red-400"
            >
              Delete
            </button>
          </div>
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
                {estimate && estimate.remaining_seconds > 0 && (
                  <span
                    title={`Historical per-transformation mean over ${estimate.remaining_jobs} remaining job(s); ${estimate.confidence} confidence`}
                  >
                    ~{formatDuration(estimate.remaining_seconds)} remaining
                  </span>
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

        {/* Diagnostics: failed-job stderr beside pegasus-analyzer output, so
            the failure reason is visible without leaving the run page. */}
        {(failedJobDetails.length > 0 || analysis) && (
          <div className="mt-6">
            <h3 className="mb-2 text-sm font-semibold text-fg">Diagnostics</h3>

            {failedJobDetails.length > 0 && (
              <div className="mb-4 space-y-3">
                {failedJobDetails.map((job, i) => (
                  <div
                    key={i}
                    className="rounded-md border border-red-200 bg-red-50 p-3 dark:border-rose-500/30 dark:bg-rose-500/10"
                  >
                    <p className="text-xs font-semibold text-red-700 dark:text-rose-300">
                      {String(job.job_id ?? "")}
                      {job.exitcode != null && (
                        <span className="font-normal">
                          {" "}
                          — exit code {String(job.exitcode)}
                        </span>
                      )}
                    </p>
                    <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap [overflow-wrap:anywhere] break-all text-xs text-red-800 dark:text-rose-200">
                      {String(job.stderr)}
                    </pre>
                  </div>
                ))}
              </div>
            )}

            {analysis && (
              <pre className="max-h-96 w-full overflow-auto whitespace-pre-wrap [overflow-wrap:anywhere] break-all rounded-md bg-gray-900 p-4 text-xs text-green-400">
                {analysis}
              </pre>
            )}

            {!analysis && failedJobDetails.length > 0 && (
              <p className="text-xs text-fgmuted">
                Run the analyzer for Pegasus&apos;s full diagnosis of these
                failures.
              </p>
            )}
          </div>
        )}
      </div>

      {confirmDelete && (
        <ConfirmDialog
          title="Delete this run?"
          message={`This permanently deletes run "${run?.name ?? runId}" and its files from your workspace. If it is still running it will be cancelled first. This cannot be undone.`}
          confirmLabel="Delete run"
          destructive
          busy={deleteRun.isPending}
          onConfirm={handleDelete}
          onCancel={() => setConfirmDelete(false)}
        />
      )}
    </div>
  );
}
