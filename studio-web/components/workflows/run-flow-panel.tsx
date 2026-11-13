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
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type {
  ProjectParams,
  RerunPreview,
  RunFlowSnapshot,
  RunFlowStage,
  WorkflowParam,
  WorkflowStep,
} from "@/lib/api/client";
import * as api from "@/lib/api/client";
import { apiUrl } from "@/lib/urls";
import { siteChoices, type SiteChoice } from "@/lib/vocabulary";
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
import { TONE_TEXT } from "@/lib/tone";
import { useProvisioningStatus } from "@/lib/hooks/use-dashboard";
import { useAnnexPin } from "@/components/resources/annex-pin-legacy";

type Placement = api.Placement;

/**
 * Which machines this workflow's steps may use. One Pegasus site on the
 * hosted deployment is served by Studio's pool, the OSPool (for steps with a
 * project name) and the user's annex. The choice is per workflow and lives
 * in its pegasus.properties, so a pegasus-plan from a terminal does the same
 * as Run (services/run_placement.py). Saved as soon as it is clicked.
 *
 * Named in the words the team settled on (2026-11-02 meeting): "compute
 * resources" for Studio's, "Bring Your Own Compute" for nodes the user
 * connects, which HTCondor calls an annex. The default no longer names the
 * OSPool: which pools stand behind Studio's resources is not the user's
 * decision here, and naming them made the choice look bigger than it is.
 * The OSPool is still described on Resources → OSPool.
 */
const PLACEMENTS: { value: Placement; label: string; detail: string; setup?: boolean }[] = [
  {
    value: "elsewhere",
    label: "Studio's Compute Resources",
    // The default is said here, on the option, rather than in a note under
    // both: the note also explained pegasus.properties, which is not the
    // user's concern when choosing (2026-11 feedback).
    detail: "The default for every workflow. Studio's shared pool, ready to use.",
  },
  {
    value: "annex",
    label: "Bring Your Own Compute",
    detail: "Only nodes you connect from your own cluster or allocation. Steps wait until they join, so start the run first, then the nodes.",
    setup: true,
  },
];

/** The hosted sites whose jobs the placement choice routes (run_placement.HOSTED_SITES). */
const PLACEMENT_SITES = ["compute", "condorpool"];

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
  pending: TONE_TEXT.off,
  running: TONE_TEXT.active,
  done: TONE_TEXT.ok,
  skipped: TONE_TEXT.off,
  failed: TONE_TEXT.error,
};


function SiteOption({
  site,
  selected,
  onChoose,
}: {
  site: SiteChoice;
  selected: boolean;
  onChoose: (value: string) => void;
}) {
  // Not ready: shown where the user expects the cluster they chose, but
  // not selectable, and the hint says what it needs rather than what it is
  // good for.
  const needs = site.missing ?? [];
  const ready = needs.length === 0;
  return (
    <label
      className={cn(
        "flex items-start gap-3 rounded-xl border p-3 transition-colors",
        ready ? "cursor-pointer" : "cursor-not-allowed opacity-70",
        // A ring as well as the tint: the tint alone was too faint to tell
        // the chosen option from the rest on a white panel.
        selected
          ? "border-pegasus-600 bg-pegasus-50 ring-1 ring-pegasus-600 dark:border-pegasus-400 dark:bg-pegasus-400/10 dark:ring-pegasus-400"
          : ready
            ? "border-line hover:bg-base"
            : "border-dashed border-line"
      )}
    >
      <input
        type="radio"
        name="run-site"
        className="mt-0.5"
        value={site.value}
        checked={selected}
        disabled={!ready}
        onChange={() => onChoose(site.value)}
      />
      <span>
        <span className="block text-sm font-medium text-fg">
          {site.label}
          {!ready && (
            <span className="ml-2 rounded-full bg-amber-100 px-1.5 py-0.5 text-2xs font-semibold uppercase tracking-wide text-amber-700 dark:bg-amber-500/15 dark:text-amber-300">
              Needs setup
            </span>
          )}
        </span>
        <span className="block text-xs text-fgmuted">
          {ready
            ? site.hint
            : `Still needs ${needs.join(", ").toLowerCase()}. Finish setting it up on Resources.`}
        </span>
      </span>
    </label>
  );
}

/**
 * One placement, drawn like a SiteOption so the two read as one kind of
 * choice. The card is a div, not the radio itself: Bring Your Own Compute
 * carries a "Connect your nodes…" link inside it, and a link cannot sit
 * inside a button.
 */
function PlacementOption({
  placement,
  selected,
  disabled,
  onChoose,
}: {
  placement: (typeof PLACEMENTS)[number];
  selected: boolean;
  disabled: boolean;
  onChoose: () => void;
}) {
  return (
    <div
      className={cn(
        "rounded-xl border transition-colors",
        selected
          ? "border-pegasus-600 bg-pegasus-50 ring-1 ring-pegasus-600 dark:border-pegasus-400 dark:bg-pegasus-400/10 dark:ring-pegasus-400"
          : "border-line hover:bg-base"
      )}
    >
      <button
        type="button"
        role="radio"
        aria-checked={selected}
        disabled={disabled}
        onClick={onChoose}
        className="flex w-full items-start gap-3 p-3 text-left disabled:cursor-not-allowed"
      >
        <span
          aria-hidden
          className={cn(
            "mt-1 h-3.5 w-3.5 shrink-0 rounded-full border",
            selected ? "border-[4px] border-pegasus-600 dark:border-pegasus-400" : "border-fgsubtle"
          )}
        />
        <span>
          <span className="block text-sm font-medium text-fg">{placement.label}</span>
          <span className="block text-xs text-fgmuted">{placement.detail}</span>
        </span>
      </button>
      {placement.setup && (
        <div className="-mt-1 pb-3 pl-[2.375rem]">
          <AppLink
            href="/provisioning#compute"
            className="text-xs font-medium text-pegasus-700 hover:underline dark:text-pegasus-300"
          >
            Connect your nodes…
          </AppLink>
        </div>
      )}
    </div>
  );
}

function stepList(steps: WorkflowStep[]): string {
  return steps.map((s) => (s.count > 1 ? `${s.name} (${s.count})` : s.name)).join(", ");
}

/** What a re-run from a step would do, in plain words. An estimate: the run's
 *  own plan stage reports what Pegasus actually planned. */
function RerunPreviewNote({ preview }: { preview: RerunPreview }) {
  const nothingReused = preview.reused.length === 0;
  return (
    <div className="mt-2 space-y-1 text-xs">
      <p className="text-fg">
        <span className="text-fgmuted">Runs:</span> {stepList(preview.run)}
      </p>
      {nothingReused ? (
        <p className="text-amber-700 dark:text-amber-400">
          No results from earlier runs were found for the other steps, so every
          step will run.
        </p>
      ) : (
        <p className="text-fg">
          <span className="text-fgmuted">Reuses:</span> {stepList(preview.reused)}
          {preview.sources.length > 0 && (
            <span className="text-fgmuted"> (from {preview.sources.join(", ")})</span>
          )}
        </p>
      )}
      {preview.missing.length > 0 && (
        <p className="text-amber-700 dark:text-amber-400">
          {preview.missing.length === 1
            ? `${preview.missing[0].file} wasn't kept`
            : `${preview.missing.length} files these steps need weren't kept`}{" "}
          (only files copied to the output folder are), so{" "}
          {Array.from(new Set(preview.missing.map((m) => m.step))).join(", ")} runs
          again too.
        </p>
      )}
    </div>
  );
}

export function RunFlowPanel({
  projectId,
  params,
  values,
  onChange,
  onSubmitted,
  embedded = false,
}: Props) {
  const [flow, setFlow] = useState<RunFlowSnapshot | null>(null);
  const provisioning = useProvisioningStatus().data;
  const hosted = provisioning?.deployment === "hosted";
  const annexName = provisioning?.annex?.name ?? "";
  // The old Resources switch wrote a standing annex-only row (#564); while
  // it is there, "wherever is free" would not be true.
  const legacyPin = useAnnexPin(annexName || "-");
  // GPUs are the workflow's to ask for, step by step (#576); the panel only
  // says when those steps would find no machine with one.
  const gpuSteps = useQuery({
    queryKey: ["gpu-steps", projectId],
    queryFn: () => api.getGpuSteps(projectId),
    staleTime: 30_000,
  }).data?.steps ?? [];
  const gpusAvailable =
    (provisioning?.pool?.total_gpus ?? 0) + (provisioning?.annex?.total_gpus ?? 0);
  // Re-run from a step (#680): "" runs every step. Per run, not saved — the
  // next Run after a partial one should not silently be partial too.
  const [startFrom, setStartFrom] = useState("");
  const workflowSteps = useQuery({
    queryKey: ["workflow-steps", projectId],
    queryFn: () => api.getWorkflowSteps(projectId),
    staleTime: 30_000,
    // 404 until the workflow is generated; nothing to offer then.
    retry: false,
  }).data?.steps ?? [];
  const rerunPreview = useQuery({
    queryKey: ["rerun-preview", projectId, startFrom],
    queryFn: () => api.getRerunPreview(projectId, startFrom),
    enabled: !!startFrom,
    retry: false,
  });
  // A regenerated workflow may no longer have the step that was picked.
  useEffect(() => {
    if (startFrom && workflowSteps.length > 0 && !workflowSteps.some((s) => s.name === startFrom)) {
      setStartFrom("");
    }
  }, [startFrom, workflowSteps]);
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
   * decision as the "Choose where to run the workflow" radio below — and the two could disagree.
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
        /^(exec(ution)?[_-]?site([_-]?name)?|site)$/i.test(
          p.dest.replace(/-/g, "_")
        )
      ),
    [allGenParams]
  );

  // The sites this project can plan against (its sites.yml, a hosted catalog,
  // the deployment's default) — not a fixed local/condorpool pair, which
  // offers a site nothing defines on a Slurm or hosted-catalog install.
  const defaultSite = params?.sites?.default ?? "condorpool";
  // Under Open OnDemand, Studio is itself a batch job on one compute node,
  // so "This machine" read as the user's laptop or the whole cluster. It is
  // neither: the local site runs every step on that one node, inside the
  // session's allocation. The cluster's scheduler is offered separately as
  // "HPC cluster" (2026-11 feedback).
  const ood = provisioning?.deployment === "ood";
  const sites = useMemo(() => {
    const base = siteChoices(params?.sites?.sites);
    return ood
      ? base.map((s) =>
          s.value === "local"
            ? {
                ...s,
                label: "This Studio session's node",
                hint: "Runs every step on the compute node your Studio session is using, within its allocation. Best for small tests; use the HPC cluster for a full analysis.",
              }
            : s
        )
      : base;
  }, [params, ood]);
  // A site the backend demoted — a generator-written condorpool once a
  // cluster has been chosen (#518) — is offered under "More places", so the
  // main choice is the one the user made on Resources. If a saved value
  // points at one, that group opens so the selection is never invisible.
  const primarySites = useMemo(() => sites.filter((s) => !s.advanced), [sites]);
  const chosenSite = String(values["site"] ?? defaultSite);
  // "This machine" (local) is not offered on hosted: it runs every step
  // inside the user's own container, a debugging escape hatch that read as a
  // third kind of compute next to Studio's and Bring Your Own Compute
  // (2026-11 feedback). A workflow that already has it saved still shows it,
  // so the selection is never invisible.
  const advancedSites = useMemo(
    () =>
      sites.filter(
        (s) => s.advanced && !(hosted && s.value === "local" && chosenSite !== "local")
      ),
    [sites, hosted, chosenSite]
  );
  // The workflow's own choice, from its pegasus.properties.
  const qc = useQueryClient();
  const placementKey = ["placement", projectId, chosenSite];
  const placementQuery = useQuery({
    queryKey: placementKey,
    queryFn: () => api.getPlacement(projectId, chosenSite),
    enabled: hosted && PLACEMENT_SITES.includes(chosenSite),
    staleTime: 10_000,
  });
  const placementState = placementQuery.data;
  const placement: Placement = placementState?.placement ?? "elsewhere";
  const placementForeign = placementState?.foreign ?? null;
  const placesMachines = hosted && PLACEMENT_SITES.includes(chosenSite);
  const poolSite =
    hosted && primarySites.length === 1 && PLACEMENT_SITES.includes(primarySites[0].value)
      ? primarySites[0]
      : null;
  // The site travels with the choice: a placement card can be clicked while
  // "More places" has another site selected, and the save must go to the
  // pool site the card stands for, not to whatever was chosen before.
  // `fromSite` is that earlier site, so a failed save undoes the switch too.
  const savePlacement = useMutation({
    mutationFn: ({ site, p }: { site: string; p: Placement; fromSite?: string }) =>
      api.setPlacement(projectId, site, p),
    onMutate: ({ site, p }) => {
      const key = ["placement", projectId, site];
      const prev = qc.getQueryData<api.PlacementState>(key);
      qc.setQueryData<api.PlacementState>(key, {
        placement: p, source: p === "annex" ? "workflow" : "default", foreign: null,
      });
      return { prev, key };
    },
    onError: (_e, { fromSite }, ctx) => {
      if (ctx) {
        // Nothing cached means the query never ran for this site (it was not
        // the chosen one): drop the optimistic row so the next read is the
        // server's, rather than leave the failed choice looking saved.
        if (ctx.prev) qc.setQueryData(ctx.key, ctx.prev);
        else qc.removeQueries({ queryKey: ctx.key, exact: true });
      }
      if (fromSite) chooseSite(fromSite);
    },
    onSuccess: (fresh, { site }) => qc.setQueryData(["placement", projectId, site], fresh),
  });
  const chosenIsAdvanced = advancedSites.some((s) => s.value === chosenSite);
  // A cluster whose catalog wants allocation details that are not set is
  // shown, so the user sees what they chose on Resources, but cannot be run
  // on: the plan would go through and the job fail on the cluster with the
  // scheduler's own error (#516).
  const chosenNeeds = sites.find((s) => s.value === chosenSite)?.missing ?? [];

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
    const current = String(values["site"] ?? defaultSite);
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
    const site = String(values["site"] ?? defaultSite);
    const siteLabel =
      sites.find((s) => s.value === site)?.label ?? site;
    if (hosted && PLACEMENT_SITES.includes(site)) {
      // One answer, as the form asks one question: the site is implied.
      rows.push({
        label: "Compute resources",
        value: placementForeign
          ? `${siteLabel}, machines set by the workflow`
          : PLACEMENTS.find((x) => x.value === placement)?.label ?? placement,
      });
    } else {
      rows.push({ label: "Compute resources", value: siteLabel });
    }
    if (gpuSteps.length > 0) {
      rows.push({
        label: "GPUs",
        value: `asked for by ${gpuSteps.length === 1 ? gpuSteps[0] : `${gpuSteps.length} steps`}`,
      });
    }
    if (startFrom) {
      rows.push({ label: "Starts from", value: `${startFrom}, reusing earlier results` });
    }

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
  }, [primaryParams, values, sites, defaultSite, hosted, placement, placementForeign, gpuSteps, startFrom]);

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
        site: String(values["site"] ?? defaultSite),
        output_site: String(values["output_site"] ?? "local"),
        ...(hosted && !placementForeign ? { placement } : {}),
        ...(startFrom ? { rerun_from: startFrom } : {}),
      });
      // Back to "the beginning" for the next Run: a partial run should be
      // asked for each time, not inherited.
      setStartFrom("");
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
            ? "This analysis already has everything it needs. Choose where the workflow runs and press Run — Studio prepares the rest and starts it for you."
            : "Fill in what your analysis needs, choose where the workflow runs, then press Run. Studio prepares everything and starts it for you."}
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
                    Settings <span className="tabular-nums">({primaryParams.length + plumbingParams.length})</span>
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
                      More options <span className="tabular-nums">({plumbingParams.length})</span>
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
          <legend className="flex w-full items-center text-2xs font-semibold uppercase tracking-wider text-fgsubtle">
            Choose where to run the workflow
            <GlossaryTerm term="Compute resources" />
            {/* A cluster and allocation are chosen once, on Resources
                (#468); the sites below follow from that choice. With the
                placement cards, the link sits in Bring Your Own Compute,
                the choice it sets up (2026-11-02 meeting). */}
            {/* Named for what the user does there, not "set up a cluster",
                which they never do (#644): under Open OnDemand the cluster is
                the administrator's and Resources asks only for the user's
                allocation; on hosted, Resources connects nodes from an
                allocation the user already has. */}
            {!poolSite && (
              <AppLink
                href="/provisioning#compute"
                className="ml-auto text-xs font-medium normal-case tracking-normal text-pegasus-700 hover:underline dark:text-pegasus-300"
              >
                {ood ? "Set your allocation…" : "Connect your nodes…"}
              </AppLink>
            )}
          </legend>
          {poolSite ? (
            // Hosted, with Studio's pool the only main site: picking the site
            // is not a choice, so the question is only whose machines. Two
            // headings for one decision ("Where it runs", then "On which
            // machines") read as two decisions (2026-11 feedback).
            <div role="radiogroup" aria-label="Choose where to run the workflow" className="mt-2 space-y-2">
              {PLACEMENTS.map((p) => (
                <PlacementOption
                  key={p.value}
                  placement={p}
                  selected={chosenSite === poolSite.value && !placementForeign && placement === p.value}
                  disabled={(chosenSite === poolSite.value && !!placementForeign) || savePlacement.isPending}
                  onChoose={() => {
                    const fromSite = chosenSite !== poolSite.value ? chosenSite : undefined;
                    if (fromSite) chooseSite(poolSite.value);
                    savePlacement.mutate({ site: poolSite.value, p: p.value, fromSite });
                  }}
                />
              ))}
            </div>
          ) : (
            <div className="mt-2 space-y-2">
              {primarySites.map((site) => (
                <SiteOption
                  key={site.value}
                  site={site}
                  selected={chosenSite === site.value}
                  onChoose={chooseSite}
                />
              ))}
            </div>
          )}
          {placesMachines && !poolSite && (
            // More than one main site and the pool is chosen: the machine
            // choice qualifies that site, so it sits under it, not beside it.
            <div className="mt-3 border-l-2 border-line pl-3">
              <p className="text-xs font-medium text-fg">Which of the pool&apos;s resources</p>
              <div role="radiogroup" aria-label="Which of the pool's resources" className="mt-1.5 grid gap-1.5 sm:grid-cols-2">
                {PLACEMENTS.map((p) => (
                  <PlacementOption
                    key={p.value}
                    placement={p}
                    selected={!placementForeign && placement === p.value}
                    disabled={!!placementForeign || savePlacement.isPending}
                    onChoose={() => savePlacement.mutate({ site: chosenSite, p: p.value })}
                  />
                ))}
              </div>
            </div>
          )}
          {placesMachines && (
            <div>
            {placementForeign && (
              <p className="mt-1.5 text-xs text-fgmuted">
                This workflow&apos;s pegasus.properties chooses its machines itself (
                <code className="font-mono">{placementForeign}</code>), so Studio leaves it alone.
              </p>
            )}
            {savePlacement.error && (
              <p className="mt-1.5 text-xs text-rose-600 dark:text-rose-400">
                Couldn&apos;t save: {(savePlacement.error as Error).message}
              </p>
            )}
            {placement === "annex" && !placementForeign && !provisioning?.annex?.established && (
              <p className="mt-1.5 text-xs text-amber-700 dark:text-amber-400">
                None of your own nodes are up yet. Connect them under{" "}
                <AppLink href="/provisioning#compute" className="underline">
                  Resources → Bring Your Own Compute
                </AppLink>{" "}
                once this run is waiting.
              </p>
            )}
            {legacyPin.ready && legacyPin.on && placement === "elsewhere" && placementState?.source === "default" && (
              <p className="mt-1.5 text-xs text-amber-700 dark:text-amber-400">
                An older setting in ~/.pegasusrc sends every workflow that doesn&rsquo;t choose to your own nodes only, so &ldquo;Studio&rsquo;s Compute Resources&rdquo; isn&rsquo;t true here yet.{" "}
                <button
                  type="button"
                  onClick={() =>
                    legacyPin.set(false, {
                      // The default row takes the old pin's place (the
                      // placement GET writes it when it is missing).
                      onSuccess: () => qc.invalidateQueries({ queryKey: ["placement", projectId] }),
                    })
                  }
                  disabled={legacyPin.saving}
                  className="font-medium underline disabled:opacity-50"
                >
                  Remove it
                </button>
              </p>
            )}
            </div>
          )}
          {advancedSites.length > 0 && (
            <details className="mt-2" open={chosenIsAdvanced || undefined}>
              <summary className="cursor-pointer text-xs font-medium text-fgmuted hover:text-fg">
                More places <span className="tabular-nums">({advancedSites.length})</span>
              </summary>
              <div className="mt-2 space-y-2">
                {advancedSites.map((site) => (
                  <SiteOption
                    key={site.value}
                    site={site}
                    selected={chosenSite === site.value}
                    onChoose={chooseSite}
                  />
                ))}
              </div>
            </details>
          )}
          {gpuSteps.length > 0 && provisioning && gpusAvailable === 0 && (
            <p className="mt-3 text-xs text-amber-700 dark:text-amber-400">
              {gpuSteps.length === 1 ? `${gpuSteps[0]} asks` : `${gpuSteps.length} steps (${gpuSteps.slice(0, 3).join(", ")}${gpuSteps.length > 3 ? ", …" : ""}) ask`}{" "}
              for a GPU, and no machine in Studio&apos;s pool or among your own nodes has one
              right now. They will wait until one joins: nodes with GPUs, from{" "}
              <AppLink href="/provisioning#compute" className="underline">
                Resources → Bring Your Own Compute
              </AppLink>
              .
            </p>
          )}
        </fieldset>

        {/* Re-run from a step (#680). Changing the plot at the end of a
            workflow used to cost a full re-run; this reuses what earlier runs
            left in the output folder for every step before the chosen one. */}
        {!running && workflowSteps.length > 1 && (
          <div className="mt-5">
            <label htmlFor="rerun-from" className="block text-sm font-medium text-fg">
              Start from
            </label>
            <select
              id="rerun-from"
              value={startFrom}
              onChange={(e) => setStartFrom(e.target.value)}
              className="mt-1 block w-full rounded-md border border-line bg-surface px-2 py-1.5 text-sm text-fg"
            >
              <option value="">The beginning: run every step</option>
              {workflowSteps.map((s) => (
                <option key={s.name} value={s.name}>
                  {s.name}
                  {s.count > 1 ? ` (${s.count} jobs)` : ""}
                </option>
              ))}
            </select>
            {!startFrom && (
              <p className="mt-1 text-xs text-fgmuted">
                Changed only a later step, like a plot? Start there to run it and
                what follows, reusing earlier results for the rest.
              </p>
            )}
            {startFrom && rerunPreview.isPending && (
              <p className="mt-1 text-xs text-fgmuted">Checking earlier runs…</p>
            )}
            {startFrom && rerunPreview.isError && (
              <p className="mt-1 text-xs text-red-600 dark:text-red-400">
                {(rerunPreview.error as Error).message}
              </p>
            )}
            {startFrom && rerunPreview.data && (
              <RerunPreviewNote preview={rerunPreview.data} />
            )}
          </div>
        )}

        {/* What pressing Run will actually do. A non-specialist has no way to
            predict the consequences of a form they only half understand, and
            "it started something on a cluster" is a bad moment to find out.
            Values only — no flags, no command line. */}
        {/* Only when it says something the form doesn't show at a glance:
            a changed setting (Settings start collapsed) or the GPU note. With
            every default left, the one row left would repeat the choice
            selected just above it (2026-11 feedback). */}
        {!running && summary.length > 1 && (
          <div className="mt-5 rounded-xl border border-line bg-base p-4">
            <p className="text-2xs font-semibold uppercase tracking-wider text-fgsubtle">
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
                Application Settings → Pegasus Options → Workflow secrets
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
            disabled={
              running || starting || missing.length > 0 || chosenNeeds.length > 0
            }
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
          {missing.length === 0 && chosenNeeds.length > 0 && (
            <p className="text-sm text-amber-700 dark:text-amber-400">
              This cluster still needs {chosenNeeds.join(", ").toLowerCase()}.{" "}
              <AppLink href="/provisioning" className="underline">
                Finish setting it up on Resources
              </AppLink>
              , or pick another place.
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
            <div className="mt-4 rounded-xl border border-red-300 bg-red-50 p-4 dark:border-rose-500/40 dark:bg-rose-500/10">
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
