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
import { useExecutionBackend } from "@/lib/hooks/use-execution-backend";
import { EXECUTION_SITES } from "@/lib/vocabulary";
import { GlossaryTerm } from "@/components/ui/glossary-term";
import { AppLink } from "@/components/layout/app-link";
import { cn } from "@/lib/utils";
import {
  ParamField,
  buildArgs,
  isAdvancedParam,
  missingRequired,
  paramLabel,
  type ParamValues,
} from "@/components/workflows/param-field";

interface Props {
  projectId: string;
  params: ProjectParams | undefined;
  values: ParamValues;
  onChange: (dest: string, value: string | boolean) => void;
  /** Called when a run reaches the cluster, so the page can refresh its runs. */
  onSubmitted?: () => void;
  /** Rendered inside a disclosure that already supplies the card and the
   *  heading. Without this the page showed "Set up your run" twice, once on
   *  the summary and again on this panel, inside two nested bordered cards. */
  embedded?: boolean;
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
  embedded = false,
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

  // Which compute backend the flow's stages execute on. With a remote backend
  // active, "This machine" really means the connected system's submit host —
  // the chip below the legend names that system, so the familiar labels stay.
  const { backend: activeBackend, isRemote } = useExecutionBackend();

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

  // Split the form by what the parameter *is for*. Running the EEG workflow
  // showed `output: workflow.yml` and `skip sites catalog` sitting at the same
  // visual weight as `num subjects` — the workflow's bookkeeping competing for
  // attention with the actual science (#256). The backend marks the difference
  // (#241, Phase 3a) and never marks a required parameter as plumbing.
  const primaryParams = useMemo(
    () => genParams.filter((p) => !isAdvancedParam(p)),
    [genParams]
  );
  const plumbingParams = useMemo(
    () => genParams.filter(isAdvancedParam),
    [genParams]
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

  /**
   * Whether the settings can start collapsed (#361).
   *
   * Read off the parameter *metadata*, not the current values, and that is
   * load-bearing: `values` is empty on the first render and filled from the
   * defaults by an effect, so a values-based test would say "incomplete" for a
   * frame and the form would flash open before collapsing. The question this
   * asks — does every required parameter carry a default — is a property of the
   * workflow and does not change while the form is being filled.
   *
   * A flag is never missing (unchecked is a real value), which is why
   * `missingRequired` skips them and so does this.
   */
  const collapsible = useMemo(() => {
    const everyRequiredHasDefault = genParams
      .filter((p) => p.required && !p.is_flag)
      .every((p) => String(p.default ?? "").trim() !== "");
    // Below a few fields there is nothing to gain: two rows of form is ~180px,
    // Run is on screen either way, and collapsing would only add a click and
    // some hidden state. The threshold is where the grid starts costing more
    // height than the rest of the panel put together.
    const longEnoughToBeInTheWay = genParams.length >= 6;
    return everyRequiredHasDefault && longEnoughToBeInTheWay;
  }, [genParams]);
  // null = follow the data; a click pins it either way for the rest of the
  // visit. Someone who opened the settings to look at them should not have
  // them shut again by a re-render.
  const [paramsOpenOverride, setParamsOpenOverride] = useState<boolean | null>(
    null
  );
  const paramsOpen = paramsOpenOverride ?? !collapsible;

  /**
   * Credentials this analysis reads from its environment, and whether they are
   * set (#364).
   *
   * Five of the workflows in this corpus need one — an OpenAQ key, a NEON
   * token, Kaggle credentials — and nothing in the UI said so. The run failed
   * on a worker node with the data source's own error, which names the API and
   * not the missing credential.
   *
   * A hint, not a gate: a variable appearing in the generator's source does not
   * mean the options you picked need it (crophealth reads the Kaggle variables
   * only for its kaggle data source), so this must not disable Run.
   */
  const envVars = useMemo(
    () => params?.generator.env_vars ?? [],
    [params]
  );
  const [secretNames, setSecretNames] = useState<string[] | null>(null);
  useEffect(() => {
    if (envVars.length === 0) return;
    api
      .getWorkflowSecrets()
      .then((r) => setSecretNames(r.secrets.map((s) => s.name)))
      // A failed lookup must not invent a warning about missing keys.
      .catch(() => setSecretNames(null));
  }, [envVars.length]);
  const missingSecrets = useMemo(
    () =>
      secretNames === null
        ? []
        : envVars.filter((name) => !secretNames.includes(name)),
    [envVars, secretNames]
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

  // A plain-language recap of the choices that matter, built from the values
  // the user actually set. Defaults left untouched are omitted: repeating them
  // buries the two or three lines that are the point.
  const summary = useMemo(() => {
    const rows: { label: string; value: string }[] = [];
    const site = String(values["site"] ?? "condorpool");
    const siteLabel =
      EXECUTION_SITES.find((s) => s.value === site)?.label ?? site;
    rows.push({ label: "Where it runs", value: siteLabel });

    for (const p of primaryParams) {
      if (p.is_flag) {
        if (values[p.dest] === true) {
          rows.push({ label: paramLabel(p), value: "yes" });
        }
        continue;
      }
      const current = String(values[p.dest] ?? "").trim();
      if (!current) continue;
      if (p.default != null && current === String(p.default)) continue;
      rows.push({ label: paramLabel(p), value: current });
    }
    return rows;
  }, [primaryParams, values]);

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
      <section
        className={
          embedded ? "" : "rounded-xl border border-line bg-surface p-5"
        }
      >
        {!embedded && (
          <h3 className="text-base font-semibold text-fg">Set up your run</h3>
        )}
        {/* "Fill in what your analysis needs" is an instruction to do
            something, and telling someone to do it when there is nothing to
            fill in sends them looking for the work. */}
        <p className={cn("text-sm text-fgmuted", !embedded && "mt-1")}>
          {collapsible
            ? "This analysis already has everything it needs. Choose where it runs and press Run — Studio prepares the rest and starts it for you."
            : "Fill in what your analysis needs, choose where it runs, then press Run. Studio prepares everything and starts it for you."}
        </p>

        {/* The settings, collapsed when the workflow can already run without
            them (#361).
            The earthquake workflow has 20 parameters, every one of them with a
            default from its own generator. Rendered open, that is ~700px of
            filled-in form between the user and the Run button — so the first
            thing Studio shows someone whose analysis is ready to run is a wall
            of fields they do not have to touch, and the button is off screen.
            Collapsed, Run is the first thing they see, and the summary line
            says what the run will use. */}
        {/* Guarded on both lists, not just the primary one: "More options" is
            nested in here now, and a workflow whose every parameter is
            plumbing would otherwise lose it. */}
        {(primaryParams.length > 0 || plumbingParams.length > 0) && (
          <div className="mt-4">
            {collapsible && (
              <button
                type="button"
                onClick={() => setParamsOpenOverride(!paramsOpen)}
                aria-expanded={paramsOpen}
                className="flex w-full items-center gap-2 rounded-lg border border-line px-3 py-2 text-left hover:bg-base"
              >
                <span
                  aria-hidden="true"
                  className={cn(
                    "text-fgsubtle transition-transform",
                    !paramsOpen && "-rotate-90"
                  )}
                >
                  ▾
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block text-sm font-medium text-fg">
                    Settings ({primaryParams.length + plumbingParams.length})
                  </span>
                  <span className="block text-xs text-fgmuted">
                    {missing.length > 0
                      ? // Only reachable by clearing a field that had a value:
                        // nothing is collapsed unless every required parameter
                        // ships a default. Say it here anyway, because the
                        // fields explaining it are hidden.
                        `${missing.length} still needed — ${missing.join(", ")}`
                      : paramsOpen
                        ? "Every setting has a value from this analysis. Change what you need to."
                        : "Every setting has a value from this analysis — open to change them."}
                  </span>
                </span>
              </button>
            )}

            {paramsOpen && (
              <>
                {primaryParams.length > 0 && (
                  <div
                    className={cn(
                      "grid grid-cols-1 gap-3 sm:grid-cols-2",
                      collapsible && "mt-3"
                    )}
                  >
                    {primaryParams.map((p: WorkflowParam) => (
                      <ParamField
                        key={p.dest}
                        param={p}
                        value={values[p.dest]}
                        onChange={(v) => onChange(p.dest, v)}
                      />
                    ))}
                  </div>
                )}

                {plumbingParams.length > 0 && (
                  <details className="mt-3">
                    <summary className="cursor-pointer text-xs font-medium text-fgmuted hover:text-fg">
                      More options ({plumbingParams.length})
                    </summary>
                    <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
                      {plumbingParams.map((p: WorkflowParam) => (
                        <ParamField
                          key={p.dest}
                          param={p}
                          value={values[p.dest]}
                          onChange={(v) => onChange(p.dest, v)}
                        />
                      ))}
                    </div>
                  </details>
                )}
              </>
            )}
          </div>
        )}

        <fieldset className="mt-5">
          <legend className="text-xs font-medium uppercase tracking-wide text-fgsubtle">
            Where it runs
            <GlossaryTerm term="Where it runs" />
          </legend>
          {/* Name the system the run is handed to — the generic labels below
              deliberately don't, so this is the one place it appears. */}
          {isRemote && activeBackend && (
            <span className="mt-1 inline-block rounded-full border border-line px-1.5 py-px text-xs text-fgsubtle">
              Connected to {activeBackend.label}
              {activeBackend.type
                ? ` via ${activeBackend.type === "ssh" ? "SSH" : activeBackend.type}`
                : ""}
            </span>
          )}
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
                      {isRemote ? site.remoteHint : site.hint}
                    </span>
                  </span>
                </label>
              );
            })}
          </div>
        </fieldset>

        {/* What pressing Run will actually do. A non-specialist has no way to
            predict the consequences of a form they only half understand, and
            "it started something on a cluster" is a bad moment to find out.
            Values only — no flags, no command line. */}
        {!running && summary.length > 0 && (
          <div className="mt-5 rounded-lg border border-line bg-base p-4">
            <p className="text-xs font-medium uppercase tracking-wide text-fgsubtle">
              What will happen
            </p>
            <dl className="mt-2 space-y-1">
              {summary.map((row) => (
                <div key={row.label} className="flex gap-2 text-sm">
                  <dt className="shrink-0 text-fgmuted">{row.label}:</dt>
                  <dd className="min-w-0 flex-1 truncate text-fg" title={row.value}>
                    {row.value}
                  </dd>
                </div>
              ))}
            </dl>
          </div>
        )}

        {missingSecrets.length > 0 && (
          <div className="mt-5 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-800 dark:border-amber-500/40 dark:bg-amber-500/10 dark:text-amber-300">
            <p className="font-medium">
              This analysis reads {missingSecrets.length === 1 ? "a key" : "keys"}{" "}
              you haven&apos;t set:{" "}
              {missingSecrets.map((name, i) => (
                <span key={name}>
                  {i > 0 && ", "}
                  <code>{name}</code>
                </span>
              ))}
            </p>
            <p className="mt-1 text-xs">
              Add {missingSecrets.length === 1 ? "it" : "them"} under{" "}
              <AppLink
                href="/settings"
                className="font-semibold underline"
              >
                Settings → Pegasus Options → Workflow secrets
              </AppLink>
              . You can still run without{" "}
              {missingSecrets.length === 1 ? "it" : "them"} — some are only
              needed for particular data sources.
            </p>
          </div>
        )}

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
