"use client";

/**
 * The one-button Run experience (#241, Phase 1b).
 *
 * Replaces the Generate / Build Image / Plan / Run row with a form and a single
 * Run button. The four Pegasus verbs still exist — they moved into the Advanced
 * disclosure on the project page — but a first-time user never has to know they
 * are phases, or which order they go in.
 *
 * The flow itself runs server-side (`services/run_flow.py`), so this component
 * is a view over it rather than the thing sequencing it. That is what makes
 * closing the tab harmless: on mount we ask whether a flow exists and attach to
 * its event stream, which replays from the beginning before going live.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type {
  ProjectParams,
  RunFlowSnapshot,
  RunFlowStage,
  WorkflowParam,
} from "@/lib/api/client";
import * as api from "@/lib/api/client";
import { apiUrl } from "@/lib/urls";
import { EXECUTION_SITES } from "@/lib/vocabulary";
import { cn } from "@/lib/utils";
import {
  ParamField,
  buildArgs,
  missingRequired,
  type ParamValues,
} from "@/components/workflows/param-field";

interface Props {
  projectId: string;
  params: ProjectParams | undefined;
  values: ParamValues;
  onChange: (dest: string, value: string | boolean) => void;
  /** Called when a run reaches the cluster, so the page can refresh its runs. */
  onSubmitted?: () => void;
}

const STAGE_ICON: Record<RunFlowStage["status"], string> = {
  pending: "○",
  running: "◐",
  done: "✓",
  skipped: "–",
  failed: "✕",
};

const STAGE_TONE: Record<RunFlowStage["status"], string> = {
  pending: "text-fgsubtle",
  running: "text-pegasus-600 dark:text-pegasus-300",
  done: "text-green-600 dark:text-emerald-400",
  skipped: "text-fgsubtle",
  failed: "text-red-600 dark:text-rose-400",
};

export function RunFlowPanel({
  projectId,
  params,
  values,
  onChange,
  onSubmitted,
}: Props) {
  const [flow, setFlow] = useState<RunFlowSnapshot | null>(null);
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);
  const [showTechnical, setShowTechnical] = useState(false);
  const [logLines, setLogLines] = useState<string[]>([]);
  const sourceRef = useRef<EventSource | null>(null);
  // Fires onSubmitted exactly once per flow, so a reconnect that replays the
  // terminal event doesn't refetch the runs list on a loop.
  const notifiedRef = useRef<string | null>(null);

  const allGenParams = useMemo(
    () => params?.generator.params ?? [],
    [params]
  );

  /**
   * Generator parameters that name the execution site.
   *
   * Most generators expose their own `--execution-site`, which is the same
   * decision as the "Where it runs" radio below — and the two could disagree.
   * Picking "This machine" while the generator's field still said `condorpool`
   * wrote a site catalog for the cluster and then planned against `local`, a
   * contradiction whose failure names neither field.
   *
   * So: keep them in lockstep and show the choice once, as the radio. The raw
   * field is still editable under Advanced for anyone who wants a site the
   * radio doesn't offer.
   */
  const siteParams = useMemo(
    () =>
      allGenParams.filter((p) =>
        /^(exec(ution)?[_-]?site|site)$/i.test(p.dest.replace(/-/g, "_"))
      ),
    [allGenParams]
  );

  const genParams = useMemo(
    () => allGenParams.filter((p) => !siteParams.includes(p)),
    [allGenParams, siteParams]
  );

  // Radio → every site-ish generator param, so what's submitted matches what
  // the user picked regardless of which knob the generator reads.
  const chooseSite = useCallback(
    (site: string) => {
      onChange("site", site);
      for (const p of siteParams) onChange(p.dest, site);
    },
    [onChange, siteParams]
  );
  const missing = useMemo(
    () => missingRequired(genParams, values),
    [genParams, values]
  );
  // Hidden site params are satisfied by the radio, which always has a value,
  // so they can never be what's missing — seed them on first render for the
  // case where the generator's default differs from the radio's.
  useEffect(() => {
    if (!siteParams.length) return;
    const current = String(values["site"] ?? "condorpool");
    for (const p of siteParams) {
      if (String(values[p.dest] ?? "") !== current) onChange(p.dest, current);
    }
    // Only re-sync when the chosen site changes, not on every keystroke
    // elsewhere in the form.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [values["site"], siteParams]);
  const running = flow?.state === "running";

  // ── attach to the server-side flow ──────────────────────────────────────

  const attach = useCallback(() => {
    if (sourceRef.current) return;
    const es = new EventSource(
      apiUrl(`workflows/projects/${encodeURIComponent(projectId)}/run-flow/events`)
    );
    sourceRef.current = es;

    es.onmessage = (event) => {
      if (event.data === "[DONE]") {
        es.close();
        sourceRef.current = null;
        return;
      }
      let parsed: Record<string, unknown>;
      try {
        parsed = JSON.parse(event.data);
      } catch {
        return; // malformed frame — the next one will carry the same state
      }

      if (parsed.type === "stage") {
        const stage = parsed.stage as RunFlowStage;
        setFlow((prev) =>
          prev
            ? {
                ...prev,
                stages: (prev.stages ?? []).map((s) =>
                  s.key === stage.key ? stage : s
                ),
              }
            : prev
        );
      } else if (parsed.type === "line") {
        // Cap the buffer: a container build can emit thousands of lines and
        // this pane is a diagnostic tail, not a log viewer.
        setLogLines((prev) => [...prev, String(parsed.line)].slice(-500));
      } else if (parsed.type === "flow") {
        setFlow((prev) =>
          prev
            ? {
                ...prev,
                state: parsed.state as RunFlowSnapshot["state"],
                failure: (parsed.failure ?? null) as RunFlowSnapshot["failure"],
                run_dir: String(parsed.run_dir ?? prev.run_dir ?? ""),
                active: false,
              }
            : prev
        );
        if (parsed.state === "succeeded") {
          const id = String(parsed.run_dir ?? "");
          if (notifiedRef.current !== id) {
            notifiedRef.current = id;
            onSubmitted?.();
          }
        }
      }
    };

    // The stream ends normally when the flow finishes; the server closes and
    // the browser would otherwise retry forever. Close on error and rely on the
    // snapshot fetch below for recovery instead of reconnect storms.
    es.onerror = () => {
      es.close();
      sourceRef.current = null;
    };
  }, [projectId, onSubmitted]);

  // On mount (and whenever the project changes) ask whether a flow already
  // exists. This is the reattach path: a user who started a run, navigated
  // away and came back sees it still going, with its whole history.
  useEffect(() => {
    let cancelled = false;
    setFlow(null);
    setLogLines([]);
    setShowTechnical(false);
    notifiedRef.current = null;

    api
      .getRunFlow(projectId)
      .then((snapshot) => {
        if (cancelled || snapshot.state === null) return;
        setFlow(snapshot);
        if (snapshot.active) attach();
      })
      .catch(() => {
        /* no flow, or the backend predates this endpoint — show the form */
      });

    return () => {
      cancelled = true;
      sourceRef.current?.close();
      sourceRef.current = null;
    };
  }, [projectId, attach]);

  const handleRun = async () => {
    setStarting(true);
    setStartError(null);
    setLogLines([]);
    setShowTechnical(false);
    notifiedRef.current = null;
    try {
      const snapshot = await api.startRunFlow(projectId, {
        args: buildArgs(allGenParams, values),
        site: String(values["site"] ?? "condorpool"),
        output_site: String(values["output_site"] ?? "local"),
      });
      setFlow(snapshot);
      attach();
    } catch (e) {
      setStartError((e as Error).message);
    } finally {
      setStarting(false);
    }
  };

  // ── render ──────────────────────────────────────────────────────────────

  return (
    <div className="space-y-4">
      <section className="rounded-xl border border-line bg-surface p-5">
        <h3 className="text-base font-semibold text-fg">Set up your run</h3>
        <p className="mt-1 text-sm text-fgmuted">
          Fill in what your analysis needs, choose where it runs, then press
          Run. Studio prepares everything and starts it for you.
        </p>

        {genParams.length > 0 && (
          <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2">
            {genParams.map((p: WorkflowParam) => (
              <ParamField
                key={p.dest}
                param={p}
                value={values[p.dest]}
                onChange={(v) => onChange(p.dest, v)}
              />
            ))}
          </div>
        )}

        <fieldset className="mt-5">
          <legend className="text-xs font-medium uppercase tracking-wide text-fgsubtle">
            Where it runs
          </legend>
          <div className="mt-2 space-y-2">
            {EXECUTION_SITES.map((site) => {
              const selected =
                String(values["site"] ?? "condorpool") === site.value;
              return (
                <label
                  key={site.value}
                  className={cn(
                    "flex cursor-pointer items-start gap-3 rounded-lg border p-3 transition-colors",
                    selected
                      ? "border-pegasus-400 bg-pegasus-50 dark:bg-pegasus-400/10"
                      : "border-line hover:bg-base"
                  )}
                >
                  <input
                    type="radio"
                    name="run-site"
                    className="mt-0.5"
                    value={site.value}
                    checked={selected}
                    onChange={() => chooseSite(site.value)}
                  />
                  <span>
                    <span className="block text-sm font-medium text-fg">
                      {site.label}
                    </span>
                    <span className="block text-xs text-fgmuted">
                      {site.hint}
                    </span>
                  </span>
                </label>
              );
            })}
          </div>
        </fieldset>

        <div className="mt-5 flex flex-wrap items-center gap-3">
          <button
            onClick={handleRun}
            disabled={running || starting || missing.length > 0}
            className="rounded-md bg-pegasus-600 px-5 py-2.5 text-sm font-semibold text-white transition-colors hover:bg-pegasus-700 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {running ? "Running…" : starting ? "Starting…" : "Run"}
          </button>
          {/* Say why the button is disabled. An unexplained dead button is the
              thing a non-specialist cannot get past on their own. */}
          {missing.length > 0 && (
            <p className="text-sm text-amber-700 dark:text-amber-400">
              Fill in {missing.join(", ")} to run.
            </p>
          )}
        </div>

        {startError && (
          <p className="mt-3 rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-rose-500/40 dark:bg-rose-500/10 dark:text-rose-300">
            {startError}
          </p>
        )}
      </section>

      {flow?.stages?.length ? (
        <section className="rounded-xl border border-line bg-surface p-5">
          <h3 className="text-base font-semibold text-fg">
            {flow.state === "succeeded"
              ? "Your analysis is running"
              : flow.state === "failed"
                ? "Your run didn't start"
                : "Getting your analysis ready"}
          </h3>

          <ol className="mt-3 space-y-2">
            {flow.stages.map((stage) => (
              <li key={stage.key} className="flex items-start gap-3">
                <span
                  aria-hidden="true"
                  className={cn(
                    "mt-0.5 w-4 text-center text-sm",
                    STAGE_TONE[stage.status],
                    stage.status === "running" && "animate-pulse"
                  )}
                >
                  {STAGE_ICON[stage.status]}
                </span>
                <span className="min-w-0 flex-1">
                  <span
                    className={cn(
                      "block text-sm",
                      stage.status === "pending"
                        ? "text-fgsubtle"
                        : "font-medium text-fg"
                    )}
                  >
                    {stage.label}
                    {/* Status in text, not colour alone. */}
                    <span className="sr-only"> — {stage.status}</span>
                  </span>
                  {stage.detail && (
                    <span className="block text-xs text-fgmuted">
                      {stage.detail}
                    </span>
                  )}
                </span>
              </li>
            ))}
          </ol>

          {flow.failure && (
            <div className="mt-4 rounded-lg border border-red-300 bg-red-50 p-4 dark:border-rose-500/40 dark:bg-rose-500/10">
              <p className="text-sm font-semibold text-red-800 dark:text-rose-300">
                {flow.failure.cause}
              </p>
              {/* A problem is never named without what to do about it. */}
              <p className="mt-1.5 text-sm text-red-700 dark:text-rose-200">
                {flow.failure.action}
              </p>
            </div>
          )}

          {(logLines.length > 0 || flow.failure) && (
            <div className="mt-3">
              <button
                onClick={() => setShowTechnical((v) => !v)}
                aria-expanded={showTechnical}
                className="text-xs font-medium text-fgmuted underline-offset-2 hover:text-fg hover:underline"
              >
                {showTechnical ? "Hide" : "Show"} technical details
              </button>
              {showTechnical && (
                <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap [overflow-wrap:anywhere] rounded-md border border-line bg-base p-3 font-mono text-xs text-fgmuted">
                  {(flow.failure?.technical
                    ? [flow.failure.technical]
                    : logLines
                  ).join("\n") || "No output."}
                </pre>
              )}
            </div>
          )}
        </section>
      ) : null}
    </div>
  );
}
