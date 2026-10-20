"use client";

/**
 * The user's HTCondor annex on the hosted deployment (#532, #534): what it
 * is doing right now, and a checklist to start one, with the Studio side
 * done by buttons and the cluster side paste-ready.
 *
 * An annex is a set of nodes the user leases from a Slurm cluster they
 * have an account on (a campus cluster, an ACCESS allocation). The nodes
 * join Studio's access point and run only that user's jobs; when the annex
 * is down the jobs still run on Studio's own pool. The access point names
 * every job's annex after its owner, so the annex MUST be called by the
 * username, and everything below is rendered with it filled in.
 *
 * The steps are a checklist whose state comes from the polled status, so
 * a user can see where they are (#544): queued steps tick "run a
 * workflow", an existing bundle ticks "create", a pending request ticks the
 * cluster steps, and nodes joining ticks the last. Studio cannot see the
 * upload itself, so that step goes current alongside the paste. Steps run
 * here, through the API, are the bundle and the shutdown. The upload is
 * the user's (no SSH from the pod, and the cluster's MFA). The cluster
 * command is generated from a short form, including a
 * ~/.condor/annex_slurm_args block that makes editing hpc.slurm
 * unnecessary. The form is remembered in this browser only; it is a
 * convenience, not state.
 *
 * Status comes from the same three queries `htcondor annex status` makes,
 * structured (services/annex.py); the CLI's only output is prose.
 */

import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Check, Copy, Download } from "lucide-react";
import * as api from "@/lib/api/client";
import type { AnnexStatus } from "@/lib/api/client";
import { AppLink } from "@/components/layout/app-link";
import { useAnnexPin } from "@/components/resources/annex-pin-legacy";
import {
  EMPTY_FORM,
  cautions,
  clusterCommand,
  missingRequired,
  problem,
  type ClusterForm,
} from "@/lib/annex-command";
import {
  ACCESS_ANNEX_GUIDE,
  CLUSTERS,
  SLURM_ACCOUNT_COMMAND,
  applyPreset,
  presetFor,
  type NodeType,
} from "@/lib/annex-presets";
import { CARD, FIELD, HINT, LABEL, StatusCard, type Tone } from "@/components/resources/status-card";
import { cn, formatDuration } from "@/lib/utils";
import { HelpPopover } from "@/components/ui/help-popover";

type StepState = "done" | "current" | "todo";

function Step({
  n,
  state,
  title,
  last,
  children,
}: {
  n: number;
  state: StepState;
  title: string;
  last?: boolean;
  children?: React.ReactNode;
}) {
  return (
    <li className="relative flex gap-3 pb-6 last:pb-0">
      {!last && (
        <span aria-hidden="true" className="absolute left-3 top-7 h-[calc(100%-1.75rem)] w-px bg-line" />
      )}
      <span
        className={cn(
          "relative z-10 flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-xs font-semibold tabular-nums",
          state === "done" && "bg-emerald-500 text-white",
          state === "current" && "bg-pegasus-600 text-white ring-4 ring-pegasus-100 dark:ring-pegasus-500/20",
          state === "todo" && "border border-line bg-surface text-fgsubtle"
        )}
      >
        {state === "done" ? <Check size={13} aria-hidden="true" /> : n}
        <span className="sr-only">{state === "done" ? " (done)" : state === "current" ? " (next)" : ""}</span>
      </span>
      <div className="min-w-0 flex-1 pt-0.5">
        <p className={cn("text-sm font-medium", state === "todo" ? "text-fgmuted" : "text-fg")}>{title}</p>
        {children && <div className="mt-1 text-sm text-fgmuted">{children}</div>}
      </div>
    </li>
  );
}

/** A command block with a copy button. */
function Cmd({ children }: { children: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(children);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // No clipboard (insecure context, permissions): the text is still
      // selectable.
    }
  };
  return (
    <div className="relative mt-1">
      <pre className="overflow-x-auto rounded bg-muted px-3 py-2 pr-10 font-mono text-xs text-fg">
        {children}
      </pre>
      <button
        type="button"
        onClick={copy}
        aria-label="Copy command"
        className="absolute right-1.5 top-1.5 rounded p-1 text-fgsubtle hover:bg-base hover:text-fg"
      >
        {copied ? <Check size={14} aria-hidden="true" /> : <Copy size={14} aria-hidden="true" />}
      </button>
    </div>
  );
}


const FORM_KEY = "studio.annex.cluster";

function loadForm(): ClusterForm {
  try {
    const raw = window.localStorage.getItem(FORM_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : null;
    if (!parsed || typeof parsed !== "object") return EMPTY_FORM;
    const out = { ...EMPTY_FORM };
    for (const k of Object.keys(EMPTY_FORM) as (keyof ClusterForm)[]) {
      const v = (parsed as Record<string, unknown>)[k];
      if (typeof v === "string") out[k] = v;
    }
    // A form saved before walltime had a default kept it blank.
    if (!out.walltime.trim()) out.walltime = EMPTY_FORM.walltime;
    return out;
  } catch {
    return EMPTY_FORM;
  }
}


// Said beside the disabled button. #544's checklist rewrite kept the
// condition and dropped the sentence, so the button was simply grey (#574).
const NEEDS_JOBS =
  "None of your steps are in the queue right now (between runs, or between stages of one), and " +
  "HTCondor only creates an annex while some are. Try again while a run is going.";

function verdict(a: AnnexStatus): { tone: Tone | "error"; headline: string; detail: string } {
  if (!a.available) {
    return {
      tone: "off",
      headline: "Annex status is not available.",
      detail: a.error ?? "The access point did not answer.",
    };
  }
  const r = a.requests ?? { total: 0, pending: 0, active: 0, retired: 0, held: 0 };
  const j = a.jobs ?? { total: 0, running: 0, idle: 0, held: 0 };
  if (a.established) {
    const now = Date.now() / 1000;
    const left = a.oldest_retires_at ? Math.max(0, a.oldest_retires_at - now) : null;
    return {
      tone: "ok",
      headline: `Your annex is up: ${a.nodes} node${a.nodes === 1 ? "" : "s"}, ${a.busy_cpus} of ${a.total_cpus} processors busy.`,
      detail:
        `${j.running} of your ${j.total} queued step${j.total === 1 ? "" : "s"} ${j.running === 1 ? "is" : "are"} running` +
        (left != null ? `; the oldest part of the annex retires in ${formatDuration(left)}.` : ".") +
        ((a.untracked_nodes ?? 0) > 0
          ? ` ${a.untracked_nodes} node${a.untracked_nodes === 1 ? " comes" : "s come"} from a setup file whose request was closed, and may not run your steps; make a new setup file for the next nodes.`
          : ""),
    };
  }
  if ((r.held ?? 0) > 0 && r.pending === 0) {
    return {
      tone: "error",
      headline: "Your annex request is on hold.",
      detail:
        (a.hold_reason ? `HTCondor says: ${a.hold_reason} ` : "") +
        "Close it, then create a new setup file.",
    };
  }
  if (r.pending > 0) {
    return {
      tone: "warn",
      headline: "Your annex is requested but not up yet.",
      detail:
        `${r.pending} request${r.pending === 1 ? "" : "s"} waiting for the cluster's scheduler. ` +
        "Until nodes arrive your steps run on Studio's own pool, or wait.",
    };
  }
  return {
    tone: "off",
    headline: "No annex right now.",
    detail:
      r.retired > 0
        ? `Earlier annexes have retired (${r.retired}). Your steps run on Studio's own pool; start a new annex for more capacity.`
        : "Your steps run on Studio's own pool. Add an annex when you need more capacity than it has.",
  };
}

export function AnnexPanel({ annex }: { annex: AnnexStatus }) {
  const qc = useQueryClient();
  const name = annex.name || "$USER";
  const v = verdict(annex);
  const live = annex.available && (annex.established || (annex.requests?.pending ?? 0) > 0);
  // Open the checklist when there is nothing running to report.
  const [showHow, setShowHow] = useState(!live);
  const [form, setForm] = useState<ClusterForm>(EMPTY_FORM);
  const [confirmStop, setConfirmStop] = useState(false);

  useEffect(() => {
    setForm(loadForm());
  }, []);
  // An armed confirmation is for the annex that was on screen; if that one
  // goes away, a later annex must not inherit a one-click shutdown.
  useEffect(() => {
    if (!live) setConfirmStop(false);
  }, [live]);
  const setField = (k: keyof ClusterForm, value: string) =>
    update((f) => ({ ...f, [k]: value }));
  const update = (change: (f: ClusterForm) => ClusterForm) =>
    setForm((f) => {
      const next = change(f);
      try {
        window.localStorage.setItem(FORM_KEY, JSON.stringify(next));
      } catch {
        // Private window or storage blocked: the form just does not persist.
      }
      return next;
    });

  const refresh = () => qc.invalidateQueries({ queryKey: ["provisioning"] });
  const pin = useAnnexPin(name);
  // Done with the annex means done pinning to it: a pin left on makes every
  // later run wait for nodes that are not coming (#564).
  const released = () => {
    refresh();
    if (pin.on) pin.set(false);
  };
  const create = useMutation({ mutationFn: () => api.createAnnexBundle(), onSuccess: refresh });
  const close = useMutation({ mutationFn: () => api.closeAnnexRequests(), onSuccess: released });
  const stop = useMutation({
    mutationFn: () => api.shutdownAnnex(),
    onSuccess: () => {
      setConfirmStop(false);
      released();
    },
  });

  // The polled status is the truth (another tab may have made a newer
  // bundle); the mutation's own answer only bridges the gap to the refetch.
  const bundle = annex.bundle ?? create.data?.bundle ?? null;
  // `htcondor annex create` refuses to run while nothing is queued for the
  // annex (#537), so step 1 is a real prerequisite, not advice.
  const queued = annex.jobs?.total ?? 0;
  const needsJobs = annex.available && annex.requires_jobs !== false && queued === 0;
  const chosen = form.cluster
    ? presetFor(form.cluster, (form.nodeType || undefined) as NodeType | undefined)
    : undefined;
  // A setup file whose own request is gone (#584). Only said when the status
  // could be read: unknown is not "closed".
  const stale = !!bundle && annex.available && bundle.request_open === false;
  const missing = missingRequired(form);
  const clusterCmd = clusterCommand(name, form);
  const waiting = annex.jobs?.idle ?? 0;
  // Leftover requests no node is left to end: granted-and-retired, or held.
  const leftovers = (annex.requests?.retired ?? 0) + (annex.requests?.held ?? 0);

  // Studio sees the queue, the request and the nodes; it cannot see the
  // upload or the sbatch. "Create" itself makes a pending request, so the
  // cluster steps stay current until nodes join rather than ticking as
  // soon as the file exists (#558).
  // Any step of the user's in the queue, waiting or running, is enough for
  // `htcondor annex create`, and with "wherever is free first" the pool and
  // the annex share the work, so a run that is going counts (#574).
  const s1: StepState = queued > 0 || annex.established ? "done" : "current";
  const s2: StepState = bundle || annex.established ? "done" : queued > 0 ? "current" : "todo";
  const s34: StepState = annex.established ? "done" : bundle ? "current" : "todo";
  const s5: StepState =
    annex.established ? "done" : bundle && (annex.requests?.pending ?? 0) > 0 ? "current" : "todo";

  return (
    <section className="space-y-4">
      <StatusCard
        tone={v.tone === "error" ? "warn" : v.tone}
        headline={v.headline}
        detail={v.detail}
        action={
          !live && leftovers > 0 ? (
            <button
              type="button"
              disabled={close.isPending}
              onClick={() => close.mutate()}
              className="rounded-md border border-line bg-surface px-3 py-1.5 text-xs font-semibold text-fg hover:bg-base disabled:opacity-50"
              title="Removes this annex's old request jobs from the access point's queue"
            >
              {close.isPending ? "Closing…" : `Close ${leftovers === 1 ? "old request" : `${leftovers} old requests`}`}
            </button>
          ) : live && (
            <>
              {confirmStop && (
                <button
                  type="button"
                  onClick={() => setConfirmStop(false)}
                  className="text-xs text-fgmuted hover:text-fg"
                >
                  Keep it
                </button>
              )}
              <button
                type="button"
                disabled={stop.isPending}
                onClick={() => (confirmStop ? stop.mutate() : setConfirmStop(true))}
                className={cn(
                  "rounded-md px-3 py-1.5 text-xs font-semibold disabled:opacity-50",
                  confirmStop
                    ? "bg-red-600 text-white hover:bg-red-700"
                    : "border border-line bg-surface text-fg hover:bg-base"
                )}
              >
                {stop.isPending ? "Shutting down…" : confirmStop ? "Yes, release the nodes" : "Shut down annex"}
              </button>
            </>
          )
        }
        stats={
          annex.available && (live || (annex.requests?.retired ?? 0) > 0)
            ? [
                { label: "Nodes", value: annex.nodes ?? 0 },
                { label: "CPUs busy / total", value: `${annex.busy_cpus ?? 0} / ${annex.total_cpus ?? 0}` },
                ...((annex.total_gpus ?? 0) > 0
                  ? [{ label: "GPUs busy / total", value: `${annex.busy_gpus ?? 0} / ${annex.total_gpus}` }]
                  : []),
                {
                  label: (annex.requests?.untracked ?? 0) > 0 ? "Requests waiting / up / done (+ closed)" : "Requests waiting / up / done",
                  value:
                    `${annex.requests?.pending ?? 0} / ${annex.requests?.active ?? 0} / ${annex.requests?.retired ?? 0}` +
                    ((annex.requests?.untracked ?? 0) > 0 ? ` + ${annex.requests?.untracked}` : ""),
                },
                { label: "Your steps running / queued", value: `${annex.jobs?.running ?? 0} / ${annex.jobs?.total ?? 0}` },
              ]
            : undefined
        }
      >
        {close.isError && (
          <p className="mt-2 text-xs text-red-600 dark:text-rose-400">
            Couldn&apos;t close the requests: {(close.error as Error).message}
          </p>
        )}
        {stop.isError && (
          <p className="mt-2 text-xs text-red-600 dark:text-rose-400">
            Couldn&apos;t shut down: {(stop.error as Error).message}
          </p>
        )}
        {stop.isSuccess && (
          <p className="mt-2 text-xs text-fgmuted">
            Shutdown sent. The nodes leave within a minute or so; the Slurm job on the cluster ends with them.
            Runs go to Studio&apos;s pool again.
          </p>
        )}
      </StatusCard>

      {pin.on && (
        <p className="text-xs text-amber-700 dark:text-amber-400">
          An older setting sends every run to your annex only, so runs wait when no
          annex is up. Runs now choose this one at a time (Run → On which machines).{" "}
          <button
            type="button"
            onClick={() => pin.set(false)}
            disabled={pin.saving}
            className="font-medium underline disabled:opacity-50"
          >
            Remove the old setting
          </button>
        </p>
      )}

      {stale && !showHow && (
        <p className="text-xs text-amber-700 dark:text-amber-400">
          Your setup file&apos;s request was closed. Make a new one before your next annex:{" "}
          <button type="button" onClick={() => setShowHow(true)} className="font-medium underline">
            open step 2
          </button>
          .
        </p>
      )}

      <div className={CARD}>
        <button
          type="button"
          onClick={() => setShowHow((s) => !s)}
          aria-expanded={showHow}
          className="flex w-full items-center justify-between px-4 py-3 text-left"
        >
          <span>
            <span className="block text-sm font-semibold text-fg">
              {live ? "How your annex was set up" : "Set up your annex"}
            </span>
            <span className="block text-xs text-fgmuted">
              Five steps, about ten minutes the first time. Studio ticks them off as it sees them happen.
            </span>
          </span>
          <span aria-hidden="true" className="text-fgsubtle">{showHow ? "−" : "+"}</span>
        </button>
        {showHow && (
          <div className="border-t border-line px-4 py-4">
            <ol>
              <Step n={1} state={s1} title="Run a workflow">
                {s1 === "done" && waiting > 0 ? (
                  <>{`${waiting} step${waiting === 1 ? " is" : "s are"} waiting for your annex.`}</>
                ) : s1 === "done" && annex.established && queued === 0 ? (
                  <>Your annex is up. Start a run and its steps can use it.</>
                ) : queued > 0 ? (
                  <>
                    A run is going: you can create the setup file now. Its steps run on
                    Studio&apos;s pool for the moment, and your annex&apos;s nodes share the
                    rest with the pool when they join. To keep a run for your nodes only,
                    start it with{" "}
                    <span className="font-medium text-fg">On which machines → Only my annex</span>.
                  </>
                ) : (
                  <>
                    Start a run from{" "}
                    <AppLink href="/workflows" className="font-medium text-pegasus-600 underline dark:text-pegasus-300">
                      Run Jobs
                    </AppLink>
                    . HTCondor creates an annex only while some of your steps are queued,
                    and a node with nothing to run leaves after 5 minutes. With{" "}
                    <span className="font-medium text-fg">Wherever is free first</span> (the
                    default) its steps use Studio&apos;s pool and your annex together;{" "}
                    <span className="font-medium text-fg">Only my annex</span> keeps them for
                    your nodes.
                  </>
                )}
              </Step>

              <Step n={2} state={s2} title="Create and download your setup file">
                <p>
                  It holds a key that lets nodes join as you for 90 days, so keep it to yourself.
                </p>
                <div className="mt-2 flex flex-wrap items-center gap-2">
                  <button
                    type="button"
                    disabled={create.isPending || needsJobs}
                    title={needsJobs ? NEEDS_JOBS : undefined}
                    onClick={() => create.mutate()}
                    className={cn(
                      "rounded-md px-3 py-1.5 text-xs font-semibold disabled:opacity-50",
                      bundle && !stale
                        ? "border border-line text-fg hover:bg-base"
                        : "bg-pegasus-600 text-white hover:bg-pegasus-700"
                    )}
                  >
                    {create.isPending ? "Creating…" : bundle ? "Make a new one" : "Create setup file"}
                  </button>
                  {bundle && (
                    <a
                      href={api.annexBundleUrl()}
                      download={bundle.filename}
                      className="inline-flex items-center gap-1 rounded-md bg-pegasus-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-pegasus-700"
                    >
                      <Download size={13} aria-hidden="true" /> Download {bundle.filename}
                    </a>
                  )}
                  {bundle && (
                    <span className="text-xs text-fgsubtle">
                      made {formatDuration(Math.max(0, Date.now() / 1000 - bundle.created_at))} ago
                    </span>
                  )}
                </div>
                {stale && (
                  <p className="mt-1.5 text-xs text-amber-700 dark:text-amber-400">
                    This setup file&apos;s request was closed. Nodes started from it can still
                    join, but one did not run its step in the first test, so make a new one
                    (and copy it to the cluster again) before your next annex.
                  </p>
                )}
                {needsJobs && (
                  <p className="mt-1.5 text-xs text-amber-700 dark:text-amber-400">
                    {NEEDS_JOBS}
                    {/* Only while this file's own request is open (the backend
                        matches the file's directory to the request's working
                        directory): a setup file belongs to one request, and
                        whether nodes from a file whose request was closed still
                        join has not been verified (#574). */}
                    {bundle?.request_open
                      ? " Your current setup file's request is still open, so nodes started from that file should still join."
                      : ""}
                  </p>
                )}
                {create.isError && (
                  <p className="mt-1 text-xs text-red-600 dark:text-rose-400">
                    Couldn&apos;t create it: {(create.error as Error).message}
                  </p>
                )}
              </Step>

              <Step n={3} state={s34} title="Copy it to your home directory on the cluster">
                Use whatever you normally use to move files there: the
                cluster&apos;s web portal, <code className="font-mono">scp</code>{" "}
                from your laptop, or Globus.
              </Step>

              <Step n={4} state={s34} title="Log in to the cluster and paste one command">
                <div className="rounded-md border border-line bg-base p-3">
                  <p className="text-xs font-medium text-fg">
                    Your Slurm options{" "}
                    <span className="font-normal text-fgsubtle">(filled into the command)</span>
                  </p>
                  {missing.length > 0 && (
                    <p className="mt-1 text-xs text-amber-700 dark:text-amber-400">
                      Add your account: most clusters refuse a job without one
                      (Expanse does).
                    </p>
                  )}
                  {cautions(form).map((c) => (
                    <p key={c} className="mt-1 text-xs text-amber-700 dark:text-amber-400">
                      {c}
                    </p>
                  ))}
                  {(Object.keys(EMPTY_FORM) as (keyof ClusterForm)[]).some((k) => problem(form, k)) && (
                    <p className="mt-1 text-xs text-amber-700 dark:text-amber-400">
                      A value with spaces or punctuation is left out of the command.
                    </p>
                  )}
                  <div className="mt-2 flex flex-wrap items-end gap-3">
                    <label className={LABEL}>
                      Cluster
                      <select
                        value={chosen?.cluster.id ?? ""}
                        onChange={(e) => update((f) => applyPreset(f, e.target.value))}
                        className={`${FIELD} w-56`}
                      >
                        <option value="">Other (fill in below)</option>
                        {CLUSTERS.map((c) => (
                          <option key={c.id} value={c.id}>
                            {c.label}
                          </option>
                        ))}
                      </select>
                    </label>
                    {chosen?.cluster.gpu && (
                      <div role="radiogroup" aria-label="Node type" className="flex rounded-md border border-line p-0.5">
                        {(["cpu", "gpu"] as const).map((t) => (
                          <button
                            key={t}
                            type="button"
                            role="radio"
                            aria-checked={chosen.nodeType === t}
                            onClick={() => update((f) => applyPreset(f, chosen.cluster.id, t))}
                            className={cn(
                              "rounded px-3 py-1.5 text-xs font-medium transition-colors",
                              chosen.nodeType === t ? "bg-pegasus-600 text-white" : "text-fgmuted hover:text-fg"
                            )}
                          >
                            {t === "cpu" ? "CPU only" : "With GPUs"}
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                  {chosen && (
                    <p className={`mt-1 ${HINT}`}>
                      {chosen.note}{" "}
                      {chosen.basis === "tested"
                        ? "Tested end to end with Studio."
                        : "From HTCondor's annex settings and the cluster's documentation; not yet tested with Studio."}{" "}
                      You can change any value.
                    </p>
                  )}
                  <div className="mt-2 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
                    <label className={LABEL}>
                      Account / allocation <span className="text-amber-700 dark:text-amber-400">*</span>
                      <HelpPopover label="How to find your account" title="Finding your account">
                        <span className="block">
                          It is the Slurm account (project) on the cluster, which may not be your
                          ACCESS allocation id: <code className="font-mono">TG-ABC123456</code> can be{" "}
                          <code className="font-mono">abc123</code> there.
                        </span>
                        <span className="block">
                          On the cluster, run{" "}
                          <code className="break-all font-mono text-fg">
                            {chosen?.cluster.accountCommand ?? SLURM_ACCOUNT_COMMAND}
                          </code>
                          {chosen && chosen.cluster.accountCommand !== SLURM_ACCOUNT_COMMAND ? (
                            <>
                              {" "}(any Slurm cluster:{" "}
                              <code className="break-all font-mono">{SLURM_ACCOUNT_COMMAND}</code>)
                            </>
                          ) : null}
                          .
                        </span>
                        {chosen?.cluster.loginNote ? (
                          <span className="block">Logging in: {chosen.cluster.loginNote}</span>
                        ) : null}
                        <span className="block">
                          <a href={ACCESS_ANNEX_GUIDE} target="_blank" rel="noreferrer" className="underline">
                            ACCESS&apos;s HTCondor Annex guide
                          </a>{" "}
                          covers SSH keys and each cluster&apos;s OnDemand portal.
                        </span>
                      </HelpPopover>
                      <input value={form.account} onChange={(e) => setField("account", e.target.value)} placeholder="e.g. abc123" spellCheck={false} className={FIELD} />
                    </label>
                    <label className={LABEL}>
                      Walltime
                      <input value={form.walltime} onChange={(e) => setField("walltime", e.target.value)} placeholder="hh:mm:ss" spellCheck={false} className={FIELD} />
                    </label>
                    <label className={LABEL}>
                      Partition
                      <input value={form.partition} onChange={(e) => setField("partition", e.target.value)} placeholder="e.g. shared" spellCheck={false} className={FIELD} />
                    </label>
                    <label className={LABEL}>
                      Nodes
                      <input value={form.nodes} onChange={(e) => setField("nodes", e.target.value)} placeholder="1" inputMode="numeric" spellCheck={false} className={FIELD} />
                    </label>
                    <label className={LABEL}>
                      CPUs per node
                      <HelpPopover label="About CPUs per node">
                        <span className="block">
                          An annex runs one HTCondor worker on each node, so the command always
                          asks for one task per node, and that task gets this many CPUs.
                        </span>
                        <span className="block">
                          On a shared (part-of-a-node) partition, set CPUs and memory, or the
                          worker gets one core.
                        </span>
                      </HelpPopover>
                      <input value={form.cpus} onChange={(e) => setField("cpus", e.target.value)} placeholder="e.g. 8" inputMode="numeric" spellCheck={false} className={FIELD} />
                    </label>
                    <label className={LABEL}>
                      GPUs per node
                      <HelpPopover label="About GPUs per node">
                        <span className="block">
                          Needs a GPU partition and GPU time on the allocation. A typed request
                          such as <code className="font-mono">v100-32:1</code> also works.
                        </span>
                        <span className="block">
                          Workflow steps get a GPU only if they ask for one (a{" "}
                          <code className="font-mono">gpus</code> or{" "}
                          <code className="font-mono">request_gpus</code> profile).
                        </span>
                      </HelpPopover>
                      <input value={form.gpus} onChange={(e) => setField("gpus", e.target.value)} placeholder="none" spellCheck={false} className={FIELD} />
                    </label>
                    <label className={LABEL}>
                      Memory per node
                      <input value={form.memory} onChange={(e) => setField("memory", e.target.value)} placeholder="e.g. 16G" spellCheck={false} className={FIELD} />
                    </label>
                    <label className={`${LABEL} sm:col-span-2`}>
                      Container module{" "}
                      <span className="font-normal text-fgsubtle">(only for workflows that run in containers)</span>
                      <HelpPopover label="About the container module">
                        <span className="block">
                          Jobs on annex nodes do not see the cluster&apos;s modules, so a container
                          workflow needs the module that provides Apptainer or Singularity.{" "}
                          <code className="font-mono">module avail apptainer singularity</code> on
                          the cluster lists them.
                        </span>
                        <span className="block">
                          It is saved on the cluster (<code className="font-mono">~/.condor/annex_config</code>),
                          so each cluster keeps its own.
                        </span>
                      </HelpPopover>
                      <input value={form.module} onChange={(e) => setField("module", e.target.value)} placeholder="e.g. singularitypro" spellCheck={false} className={`${FIELD} font-mono`} />
                    </label>
                    <label className={`${LABEL} sm:col-span-2`}>
                      Other sbatch options
                      <input value={form.extra} onChange={(e) => setField("extra", e.target.value)} placeholder="e.g. --qos=normal" spellCheck={false} className={`${FIELD} font-mono`} />
                    </label>
                  </div>

                </div>
                <div className="mt-2">
                  <Cmd>{clusterCmd}</Cmd>
                </div>
                <p className={`mt-1 ${HINT}`}>
                  The first lines save your options in{" "}
                  <code className="font-mono">~/.condor/annex_slurm_args</code>
                  {form.module.trim() ? <> and <code className="font-mono">~/.condor/annex_config</code></> : null} on
                  the cluster, which every later setup reads.
                </p>
              </Step>

              <Step n={5} state={s5} title="Wait for the nodes to join" last>
                {annex.established ? (
                  <>Your annex is up and running your steps.</>
                ) : (
                  <>
                    This page refreshes on its own. Once the cluster&apos;s
                    scheduler starts the job, the nodes appear above and your
                    waiting steps start on them. A node with nothing to run leaves
                    after 5 minutes, so start the run before the nodes. Shut the
                    annex down above when you are done, so it stops using your
                    allocation; it also ends when its walltime runs out.
                  </>
                )}
              </Step>
            </ol>

            <details className={`mt-5 border-t border-line pt-3 ${HINT}`}>
              <summary className="cursor-pointer font-medium text-fgmuted hover:text-fg">
                How this works
              </summary>
              <p className="mt-2">
                Step 2 runs <code className="font-mono">htcondor annex create {name}</code>{" "}
                for you. The annex is named <code className="font-mono">{name}</code>{" "}
                because Studio sends your steps to an annex of that name and no
                other. To check on it from a terminal:{" "}
                <code className="font-mono">htcondor annex status {name}</code>. Full
                details are in HTCondor&apos;s{" "}
                <a
                  href="https://htcondor.readthedocs.io/en/latest/users-manual/bringing-your-own-resources.html"
                  target="_blank"
                  rel="noreferrer"
                  className="underline"
                >
                  Bringing Your Own Resources
                </a>{" "}
                guide; its first step is already done for you.
              </p>
            </details>
          </div>
        )}
      </div>
    </section>
  );
}
