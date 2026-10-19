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
import { CARD, FIELD, HINT, LABEL, StatusCard, type Tone } from "@/components/resources/status-card";
import { cn, formatDuration } from "@/lib/utils";

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


type ClusterForm = {
  partition: string;
  account: string;
  walltime: string;
  nodes: string;
};
const EMPTY_FORM: ClusterForm = { partition: "", account: "", walltime: "", nodes: "" };
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
    return out;
  } catch {
    return EMPTY_FORM;
  }
}

/**
 * What each field may contain once it is pasted into a shell. The values
 * are the user's own and go into the user's own terminal, so this is not
 * a security boundary; it is what keeps a stray newline or quote (typed,
 * or left in storage) from breaking the heredoc.
 */
const SHAPE: Record<keyof ClusterForm, RegExp> = {
  partition: /^[A-Za-z0-9._-]+$/,
  account: /^[A-Za-z0-9._-]+$/,
  walltime: /^[0-9]{1,3}(-[0-9]{1,2})?(:[0-9]{1,2}){0,2}$/,
  nodes: /^[0-9]{1,5}$/,
};
const valid = (f: ClusterForm, k: keyof ClusterForm) => {
  const v = f[k].trim();
  return v && SHAPE[k].test(v) ? v : "";
};
const problem = (f: ClusterForm, k: keyof ClusterForm) => !!f[k].trim() && !valid(f, k);

/** The #SBATCH line for ~/.condor/annex_slurm_args, or "" when nothing is set. */
function sbatchLine(f: ClusterForm): string {
  const parts: string[] = [];
  const account = valid(f, "account");
  const partition = valid(f, "partition");
  const walltime = valid(f, "walltime");
  const nodes = valid(f, "nodes");
  if (account) parts.push(`-A ${account}`);
  if (partition) parts.push(`-p ${partition}`);
  if (walltime) parts.push(`-t ${walltime}`);
  if (nodes) parts.push(`--nodes=${nodes}`);
  return parts.length ? `#SBATCH ${parts.join(" ")}` : "";
}

function verdict(a: AnnexStatus): { tone: Tone; headline: string; detail: string } {
  if (!a.available) {
    return {
      tone: "off",
      headline: "Annex status is not available.",
      detail: a.error ?? "The access point did not answer.",
    };
  }
  const r = a.requests ?? { total: 0, pending: 0, active: 0, retired: 0 };
  const j = a.jobs ?? { total: 0, running: 0, idle: 0, held: 0 };
  if (a.established) {
    const now = Date.now() / 1000;
    const left = a.oldest_retires_at ? Math.max(0, a.oldest_retires_at - now) : null;
    return {
      tone: "ok",
      headline: `Your annex is up: ${a.nodes} node${a.nodes === 1 ? "" : "s"}, ${a.busy_cpus} of ${a.total_cpus} processors busy.`,
      detail:
        `${j.running} of your ${j.total} queued step${j.total === 1 ? "" : "s"} ${j.running === 1 ? "is" : "are"} running` +
        (left != null ? `; the oldest part of the annex retires in ${formatDuration(left)}.` : "."),
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
    setForm((f) => {
      const next = { ...f, [k]: value };
      try {
        window.localStorage.setItem(FORM_KEY, JSON.stringify(next));
      } catch {
        // Private window or storage blocked: the form just does not persist.
      }
      return next;
    });

  const refresh = () => qc.invalidateQueries({ queryKey: ["provisioning"] });
  const create = useMutation({ mutationFn: () => api.createAnnexBundle(), onSuccess: refresh });
  const stop = useMutation({
    mutationFn: () => api.shutdownAnnex(),
    onSuccess: () => {
      setConfirmStop(false);
      refresh();
    },
  });

  // The polled status is the truth (another tab may have made a newer
  // bundle); the mutation's own answer only bridges the gap to the refetch.
  const bundle = annex.bundle ?? create.data?.bundle ?? null;
  // `htcondor annex create` refuses to run while nothing is queued for the
  // annex (#537), so step 1 is a real prerequisite, not advice.
  const queued = annex.jobs?.total ?? 0;
  const needsJobs = annex.available && annex.requires_jobs !== false && queued === 0;
  const sbatch = sbatchLine(form);
  const clusterCmd =
    (sbatch
      ? `mkdir -p ~/.condor && cat > ~/.condor/annex_slurm_args <<'ANNEX_SLURM_ARGS'\n${sbatch}\nANNEX_SLURM_ARGS\n`
      : "") +
    `tar xf annex-${name}.tar && cd annex-${name} && ./annex-setup.sh` +
    (sbatch ? ` && sbatch hpc.slurm` : `\n# edit hpc.slurm (partition, account, walltime, nodes), then:\nsbatch hpc.slurm`);

  const s1: StepState = !needsJobs || live ? "done" : "current";
  const s2: StepState = bundle || live ? "done" : s1 === "done" ? "current" : "todo";
  const s34: StepState = live ? "done" : s2 === "done" ? "current" : "todo";
  const s5: StepState = annex.established ? "done" : live ? "current" : "todo";

  return (
    <section className="space-y-4">
      <StatusCard
        tone={v.tone}
        headline={v.headline}
        detail={v.detail}
        action={
          live && (
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
                {
                  label: "Requests waiting / up / retired",
                  value: `${annex.requests?.pending ?? 0} / ${annex.requests?.active ?? 0} / ${annex.requests?.retired ?? 0}`,
                },
                { label: "Your steps running / queued", value: `${annex.jobs?.running ?? 0} / ${annex.jobs?.total ?? 0}` },
              ]
            : undefined
        }
      >
        {stop.isError && (
          <p className="mt-2 text-xs text-red-600 dark:text-rose-400">
            Couldn&apos;t shut down: {(stop.error as Error).message}
          </p>
        )}
        {stop.isSuccess && (
          <p className="mt-2 text-xs text-fgmuted">
            Shutdown sent. The nodes leave within a minute or so; the Slurm job on the cluster ends with them.
          </p>
        )}
      </StatusCard>

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
                {s1 === "done" ? (
                  <>Steps are waiting for your annex.</>
                ) : (
                  <>
                    The annex can only be created while your steps are waiting for
                    it, and nodes that find nothing to run leave after a few
                    minutes. Start one from{" "}
                    <AppLink href="/workflows" className="font-medium text-pegasus-600 underline dark:text-pegasus-300">
                      Run Jobs
                    </AppLink>
                    , then come back.
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
                    onClick={() => create.mutate()}
                    className={cn(
                      "rounded-md px-3 py-1.5 text-xs font-semibold disabled:opacity-50",
                      bundle
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
                    Your Slurm options <span className="font-normal text-fgsubtle">(optional, filled into the command)</span>
                  </p>
                  {(Object.keys(EMPTY_FORM) as (keyof ClusterForm)[]).some((k) => problem(form, k)) && (
                    <p className="mt-1 text-xs text-amber-700 dark:text-amber-400">
                      A value with spaces or punctuation is left out of the command.
                    </p>
                  )}
                  <div className="mt-2 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
                    <label className={LABEL}>
                      Account / project
                      <input value={form.account} onChange={(e) => setField("account", e.target.value)} placeholder="abc123" spellCheck={false} className={FIELD} />
                    </label>
                    <label className={LABEL}>
                      Partition
                      <input value={form.partition} onChange={(e) => setField("partition", e.target.value)} placeholder="compute" spellCheck={false} className={FIELD} />
                    </label>
                    <label className={LABEL}>
                      Walltime
                      <input value={form.walltime} onChange={(e) => setField("walltime", e.target.value)} placeholder="02:00:00" spellCheck={false} className={FIELD} />
                    </label>
                    <label className={LABEL}>
                      Nodes
                      <input value={form.nodes} onChange={(e) => setField("nodes", e.target.value)} placeholder="1" inputMode="numeric" spellCheck={false} className={FIELD} />
                    </label>
                  </div>
                </div>
                <div className="mt-2">
                  <Cmd>{clusterCmd}</Cmd>
                </div>
                {sbatch && (
                  <p className={`mt-1 ${HINT}`}>
                    Your options are saved on the cluster, so next time you can skip the form.
                  </p>
                )}
              </Step>

              <Step n={5} state={s5} title="Wait for the nodes to join" last>
                {annex.established ? (
                  <>Your annex is up and running your steps.</>
                ) : (
                  <>
                    This page refreshes on its own. Once the cluster&apos;s
                    scheduler starts the job, the nodes appear above and your
                    waiting steps start on them. Shut the annex down above when you
                    are done, so it stops using your allocation; it also ends on
                    its own when idle or when its walltime runs out.
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
