"use client";

import {
  useWorkflowJobs,
  useAnalyzeWorkflow,
  useWorkflows,
  useWorkflowEstimate,
  useDeleteWorkflow,
  useCancelWorkflow,
  useSlowSteps,
} from "@/lib/hooks/use-workflows";
import { useWorkflowStore } from "@/lib/stores/workflow-store";
import { useActionToast } from "@/lib/hooks/use-action-toast";
import { WhereItRan } from "@/components/workflows/where-it-ran";
import { OutputsPanel } from "@/components/workflows/outputs-panel";
import { FailureCards } from "@/components/workflows/failure-cards";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { apiUrl } from "@/lib/urls";
import { cn, formatCount, formatDuration, statusColor } from "@/lib/utils";
import { TONE_BAR, TONE_TEXT } from "@/lib/tone";
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
  const cancelRun = useCancelWorkflow();
  const runAction = useActionToast();
  const selectWorkflow = useWorkflowStore((s) => s.selectWorkflow);
  const [analysis, setAnalysis] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [confirmCancel, setConfirmCancel] = useState(false);
  // Results leads: it is what the user came for, and the job table is the
  // diagnostic view rather than the destination. Safe to lead with even mid-run
  // because progress lives in the header above the tabs, and the panel has a
  // real "no results yet" state. `?view=steps` opts out; the "finished"
  // notification deep-links here with no param and lands on Results.
  const [tab, setTab] = useState<"results" | "steps">(() => {
    if (typeof window !== "undefined") {
      const view = new URLSearchParams(window.location.search).get("view");
      if (view === "steps") return "steps";
    }
    return "results";
  });
  // Once a manual choice is made, stop steering: an auto-switch that fights
  // the user is worse than landing on the wrong tab once.
  const tabPinned = useRef(false);
  const chooseTab = (t: "results" | "steps") => {
    tabPinned.current = true;
    setTab(t);
  };
  // Only a run with queue presence can be removed from it. "running" covers a
  // DAG whose jobs are queued or executing; a finished or failed run has nothing
  // left to remove, and offering the button there just invites a 409.
  const isQueued = isRunning || run?.status === "submitted";

  const setEventSource = useWorkflowStore((s) => s.setEventSource);
  const [liveStats, setLiveStats] = useState<SSEEvent | null>(null);
  const eventSourceRef = useRef<EventSource | null>(null);

  // Remaining-time estimate (historical-mean heuristic) — running runs only.
  const { data: estimateData } = useWorkflowEstimate(runId, isRunning);
  const estimate = isRunning ? estimateData?.estimate ?? null : null;
  const { data: slowData } = useSlowSteps(runId, isRunning);
  const slow = isRunning ? slowData?.steps ?? [] : [];

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

  useEffect(() => {
    if (tabPinned.current) return;
    if (run?.status === "failed") setTab("steps");
  }, [run?.status]);

  const handleAnalyze = async () => {
    const result = await runAction("Analyze", () => analyze.mutateAsync(runId));
    if (result) setAnalysis(result.analysis);
  };

  const handleCancel = async () => {
    // The run stays selected: its logs are the reason someone removed it from
    // the queue, and they are still here to read.
    await runAction("Remove from queue", () => cancelRun.mutateAsync(runId));
    setConfirmCancel(false);
  };

  const handleDelete = async () => {
    const result = await runAction("Delete run", () =>
      deleteRun.mutateAsync(runId)
    );
    if (result) selectWorkflow(null); // run is gone — clear the selection
    setConfirmDelete(false);
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
        <div className="flex flex-wrap items-center justify-between gap-3">
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
            {/* Only offered while there is something queued to remove. */}
            {isQueued && (
              <button
                onClick={() => setConfirmCancel(true)}
                disabled={cancelRun.isPending}
                title="Remove this run from the Condor queue, keeping its files and logs"
                className="rounded-md border border-line px-3 py-1.5 text-sm font-medium text-fgmuted hover:border-amber-400 hover:text-amber-600 disabled:opacity-50 dark:hover:text-amber-400"
              >
                {cancelRun.isPending ? "Removing..." : "Remove from Queue"}
              </button>
            )}
            <button
              onClick={() => setConfirmDelete(true)}
              title="Delete this run and its files"
              className="rounded-md border border-line px-3 py-1.5 text-sm font-medium text-fgmuted hover:border-red-400 hover:text-red-600 dark:hover:text-red-400"
            >
              Delete
            </button>
          </div>
        </div>

        {slow.length > 0 && (
          <div className="mt-4 rounded-xl border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900 dark:border-amber-500/40 dark:bg-amber-500/10 dark:text-amber-200">
            <p className="font-medium">
              {slow.length === 1 ? "A step is" : `${slow.length} steps are`} taking much longer than usual
            </p>
            <ul className="mt-1 space-y-0.5 text-xs">
              {slow.map((s) => (
                <li key={s.job_id} className="tabular-nums">
                  <span className="font-medium">{s.transformation}</span>{" "}
                  {s.started ? "has been running" : "has held a slot without starting"} for{" "}
                  {formatDuration(s.running_seconds)}
                  {s.host ? ` on ${s.host}` : ""}; it usually takes about{" "}
                  {formatDuration(s.usual_seconds)} ({s.basis}).
                </li>
              ))}
            </ul>
            <p className="mt-1.5 text-xs">
              {slow.some((s) => !s.started)
                ? "A step that holds a slot without starting is usually stuck on that machine. On your own nodes (Bring Your Own Compute), ending that node's Slurm job (scancel on the cluster) evicts the step, and HTCondor runs it again on the next node."
                : "Its log arrives when it finishes. On your own nodes, the cluster's own job directory shows it sooner."}
            </p>
          </div>
        )}

        {/* Progress bar for running/completed workflows */}
        {totalJobs > 0 && (
          <div className="mt-4">
            <div className="flex justify-between text-sm tabular-nums text-fgmuted">
              <span>{formatCount(completedJobs)} / {formatCount(totalJobs)} jobs completed</span>
              <span>{progress}%</span>
            </div>
            <div className="mt-1 h-2 w-full rounded-full bg-muted">
              <div
                className={cn(
                  "h-2 rounded-full transition-all",
                  failedJobs > 0 ? TONE_BAR.error : TONE_BAR.active
                )}
                style={{ width: `${progress}%` }}
              />
            </div>
            {(isRunning || runningJobs > 0 || queuedJobs > 0 || failedJobs > 0) && (
              <div className="mt-2 flex gap-4 text-xs tabular-nums text-fgmuted">
                {runningJobs > 0 && (
                  <span className={TONE_TEXT.active}>{runningJobs} running</span>
                )}
                {queuedJobs > 0 && (
                  <span className={TONE_TEXT.warn}>{queuedJobs} queued</span>
                )}
                {failedJobs > 0 && (
                  <span className={TONE_TEXT.error}>{failedJobs} failed</span>
                )}
                {/* Confidence-gated (#241, Phase 4). The estimate is a
                    historical-mean heuristic, so on a first run — exactly the
                    user this redesign targets — there is no history to average
                    and the number would be invented. A non-specialist reads
                    "~4m remaining" literally and plans around it, so with a
                    low-confidence or absent estimate we say the honest thing
                    instead. */}
                {isRunning &&
                  (estimate &&
                  estimate.remaining_seconds > 0 &&
                  estimate.confidence !== "low" ? (
                    <span
                      title={`Historical per-transformation mean over ${estimate.remaining_jobs} remaining job(s); ${estimate.confidence} confidence`}
                    >
                      about {formatDuration(estimate.remaining_seconds)}{" "}
                      remaining
                    </span>
                  ) : (
                    <span title="Not enough history from this workflow to estimate a finish time yet">
                      this can take a while — we&apos;ll notify you when
                      it&apos;s done
                    </span>
                  ))}
              </div>
            )}
          </div>
        )}
      </div>

      {/* Results | Steps (#241, Phase 4).
          Results leads because it is what the user came for; the job table is
          the diagnostic view, not the destination. A run that hasn't finished
          opens on Steps, since there is nothing to collect yet. */}
      <div className="border-b border-line bg-surface px-6">
        <div className="flex gap-1" role="tablist">
          {(["results", "steps"] as const).map((t) => (
            <button
              key={t}
              role="tab"
              aria-selected={tab === t}
              onClick={() => chooseTab(t)}
              className={cn(
                "-mb-px border-b-2 px-3 py-2 text-sm font-medium transition-colors",
                tab === t
                  ? "border-pegasus-500 text-pegasus-700 dark:text-pegasus-300"
                  : "border-transparent text-fgmuted hover:text-fg"
              )}
            >
              {t === "results" ? "Results" : "Steps"}
            </button>
          ))}
        </div>
      </div>

      {tab === "results" && (
        <OutputsPanel runId={runId} runFinished={!isRunning} />
      )}

      {/* Jobs table */}
      <div className={cn("p-6", tab !== "steps" && "hidden")}>
        {isLoading ? (
          <p className="text-sm text-fgmuted">Loading jobs...</p>
        ) : data?.jobs && data.jobs.length > 0 ? (
          <>
          <WhereItRan jobs={data.jobs} />
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-line text-left text-xs font-medium text-fgmuted">
                <th className="pb-2 pr-4">Job ID</th>
                <th className="pb-2 pr-4">Transformation</th>
                <th className="pb-2 pr-4">Site</th>
                <th className="pb-2 pr-4">Ran on</th>
                <th className="pb-2 pr-4">Status</th>
                <th className="pb-2 pr-4 text-right">Duration</th>
                <th className="pb-2 text-right">Exit code</th>
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
                  <td className="py-2 pr-4 font-mono text-xs text-fgmuted">
                    {job.host ? String(job.host) : "\u2014"}
                  </td>
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
                  <td className="py-2 pr-4 text-right">
                    {job.duration != null ? formatDuration(Number(job.duration)) : "\u2014"}
                  </td>
                  <td className="py-2 text-right">
                    {job.exitcode != null ? String(job.exitcode) : "\u2014"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          </>
        ) : (
          <p className="text-sm text-fgmuted">
            No job data available. The workflow may not have a stampede database yet.
          </p>
        )}

        {/* Failure cards first (#241, Phase 5): what went wrong and what to
            do about it. The raw stderr and analyzer output stay below, as
            detail rather than as the whole answer. */}
        <div className="mt-6">
          <FailureCards runId={runId} runFinished={!isRunning} />
        </div>

        {(failedJobDetails.length > 0 || analysis) && (
          <details className="mt-6">
            <summary className="cursor-pointer text-sm font-semibold text-fgmuted hover:text-fg">
              Raw diagnostics
            </summary>

            {failedJobDetails.length > 0 && (
              <div className="mb-4 mt-3 space-y-3">
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
          </details>
        )}
      </div>

      {confirmCancel && (
        <ConfirmDialog
          title="Remove this run from the queue?"
          message={`This stops run "${run?.name ?? runId}" and removes its jobs from the Condor queue. The run directory, logs and any outputs already produced are kept, so you can still see what happened. Queued work does not resume.`}
          confirmLabel="Remove from queue"
          busy={cancelRun.isPending}
          onConfirm={handleCancel}
          onCancel={() => setConfirmCancel(false)}
        />
      )}

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
