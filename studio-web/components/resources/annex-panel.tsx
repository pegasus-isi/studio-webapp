"use client";

/**
 * The user's HTCondor annex on the hosted deployment (#532, #534): what it
 * is doing right now, and the steps to start one, with the Studio side done
 * by buttons and the cluster side paste-ready.
 *
 * An annex is a set of nodes the user leases from a Slurm cluster they
 * have an account on (a campus cluster, an ACCESS allocation). The nodes
 * join Studio's access point and run only that user's jobs; when the annex
 * is down the jobs still run on Studio's own pool. The access point names
 * every job's annex after its owner, so the annex MUST be called by the
 * username, and everything below is rendered with it filled in.
 *
 * Steps 1 (create the bundle) and 5 (shut down) run here, through the API.
 * Step 2 is a download: the user carries the tarball to the cluster
 * however they log in there (Studio cannot, and should not try to: no SSH
 * from the pod, and the cluster's MFA). Step 3 is generated as a paste
 * from a short form, including a ~/.condor/annex_slurm_args block that
 * makes editing hpc.slurm unnecessary. The form is remembered in this
 * browser only; it is a convenience, not state.
 *
 * Status comes from the same three queries `htcondor annex status` makes,
 * structured (services/annex.py); the CLI's only output is prose.
 */

import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Check, Cloud, Copy, Download } from "lucide-react";
import * as api from "@/lib/api/client";
import type { AnnexStatus } from "@/lib/api/client";
import { cn, formatDuration } from "@/lib/utils";

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-lg border border-line bg-base p-3">
      <p className="text-[11px] font-medium uppercase tracking-wider text-fgsubtle">{label}</p>
      <p className="mt-0.5 text-lg font-semibold text-fg">{value}</p>
    </div>
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

const fieldCls =
  "mt-1 block w-full rounded-md border border-line bg-base px-2 py-1 font-mono text-xs text-fg outline-none focus:border-pegasus-400";

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

function verdict(a: AnnexStatus): { tone: string; headline: string; detail: string } {
  const amber = "border-amber-300 bg-amber-50 dark:border-amber-500/40 dark:bg-amber-500/10";
  const green = "border-green-300 bg-green-50 dark:border-emerald-500/40 dark:bg-emerald-500/10";
  const plain = "border-line bg-base";
  if (!a.available) {
    return {
      tone: plain,
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
      tone: green,
      headline: `Your annex is up: ${a.nodes} node${a.nodes === 1 ? "" : "s"}, ${a.busy_cpus} of ${a.total_cpus} processors busy.`,
      detail:
        `${j.running} of your ${j.total} queued step${j.total === 1 ? "" : "s"} ${j.running === 1 ? "is" : "are"} running` +
        (left != null ? `; the oldest part of the annex retires in ${formatDuration(left)}.` : "."),
    };
  }
  if (r.pending > 0) {
    return {
      tone: amber,
      headline: "Your annex is requested but not up yet.",
      detail:
        `${r.pending} request${r.pending === 1 ? "" : "s"} waiting for the cluster's scheduler. ` +
        "Until nodes arrive your steps run on Studio's own pool, or wait.",
    };
  }
  return {
    tone: plain,
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
  // Open the steps when there is nothing to report yet.
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
  const sbatch = sbatchLine(form);
  const clusterCmd =
    (sbatch
      ? `mkdir -p ~/.condor && cat > ~/.condor/annex_slurm_args <<'ANNEX_SLURM_ARGS'\n${sbatch}\nANNEX_SLURM_ARGS\n`
      : "") +
    `tar xf annex-${name}.tar && cd annex-${name} && ./annex-setup.sh` +
    (sbatch ? ` && sbatch hpc.slurm` : `\n# edit hpc.slurm (partition, account, walltime, nodes), then:\nsbatch hpc.slurm`);

  return (
    <section className="rounded-xl border border-line bg-surface p-4">
      <div className="flex items-start gap-3">
        <Cloud size={18} aria-hidden="true" className="mt-0.5 shrink-0 text-pegasus-600 dark:text-pegasus-300" />
        <div className="min-w-0 flex-1">
          <h2 className="text-sm font-semibold text-fg">Your own resources (annex)</h2>
          <p className="mt-0.5 text-xs text-fgmuted">
            Lease nodes from a cluster you have an account on. They join Studio
            and run only your workflows; when they are gone, Studio&apos;s own pool
            carries on.
          </p>
        </div>
      </div>

      <div className={cn("mt-4 rounded-lg border p-4", v.tone)}>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="text-sm font-semibold text-fg">{v.headline}</p>
            <p className="mt-1 text-sm text-fgmuted">{v.detail}</p>
          </div>
          {live && (
            <div className="flex shrink-0 items-center gap-2">
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
            </div>
          )}
        </div>
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
      </div>

      {annex.available && (
        <div className="mt-3 grid grid-cols-2 gap-3 lg:grid-cols-4">
          <Stat label="Nodes" value={annex.nodes ?? 0} />
          <Stat label="CPUs busy / total" value={`${annex.busy_cpus ?? 0}/${annex.total_cpus ?? 0}`} />
          <Stat
            label="Requests pending / up / retired"
            value={`${annex.requests?.pending ?? 0} / ${annex.requests?.active ?? 0} / ${annex.requests?.retired ?? 0}`}
          />
          <Stat label="Your steps running / queued" value={`${annex.jobs?.running ?? 0} / ${annex.jobs?.total ?? 0}`} />
        </div>
      )}

      <div className="mt-4 rounded-md border border-line">
        <button
          type="button"
          onClick={() => setShowHow((s) => !s)}
          aria-expanded={showHow}
          className="flex w-full items-center justify-between px-3 py-2 text-left text-xs font-medium text-fgmuted hover:text-fg"
        >
          <span>Add an annex from a cluster</span>
          <span aria-hidden="true">{showHow ? "−" : "+"}</span>
        </button>
        {showHow && (
          <div className="space-y-4 border-t border-line px-4 py-3 text-sm text-fgmuted">
            <p>
              Works with any Slurm cluster you can log in to, for example a
              campus cluster or an ACCESS allocation. Studio does its part here;
              the cluster&apos;s part is two pasted commands. Full details are in
              HTCondor&apos;s{" "}
              <a
                href="https://htcondor.readthedocs.io/en/latest/users-manual/bringing-your-own-resources.html"
                target="_blank"
                rel="noreferrer"
                className="underline"
              >
                Bringing Your Own Resources
              </a>{" "}
              guide; its first step (submitting the job) is already done for
              you, and the annex is named{" "}
              <code className="font-mono">{name}</code> because Studio sends
              your jobs to an annex of that name and no other.
            </p>
            <div className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-500/40 dark:bg-amber-500/10 dark:text-amber-200">
              <strong>Submit your workflow first.</strong> Annex nodes that find
              nothing to run shut themselves down after a few minutes.
            </div>

            {/* The cluster, once: fills the commands below. */}
            <div className="rounded-md border border-line bg-base p-3">
              <p className="text-xs font-medium text-fg">Your Slurm options (optional, fills in the commands)</p>
              {(Object.keys(EMPTY_FORM) as (keyof ClusterForm)[]).some((k) => problem(form, k)) && (
                <p className="mt-1 text-[11px] text-amber-700 dark:text-amber-400">
                  A value with spaces or punctuation is left out of the commands below.
                </p>
              )}
              <div className="mt-2 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
                <label className="text-[11px] text-fgsubtle">
                  Account / project
                  <input value={form.account} onChange={(e) => setField("account", e.target.value)} placeholder="abc123" spellCheck={false} className={fieldCls} />
                </label>
                <label className="text-[11px] text-fgsubtle">
                  Partition
                  <input value={form.partition} onChange={(e) => setField("partition", e.target.value)} placeholder="compute" spellCheck={false} className={fieldCls} />
                </label>
                <label className="text-[11px] text-fgsubtle">
                  Walltime
                  <input value={form.walltime} onChange={(e) => setField("walltime", e.target.value)} placeholder="02:00:00" spellCheck={false} className={fieldCls} />
                </label>
                <label className="text-[11px] text-fgsubtle">
                  Nodes
                  <input value={form.nodes} onChange={(e) => setField("nodes", e.target.value)} placeholder="1" inputMode="numeric" spellCheck={false} className={fieldCls} />
                </label>
              </div>
            </div>

            <ol className="list-decimal space-y-4 pl-5">
              <li>
                <span className="font-medium text-fg">Create the setup bundle.</span>{" "}
                Studio runs <code className="font-mono">htcondor annex create {name}</code>{" "}
                for you and gives you the result to download. It holds a key that
                lets nodes join as you, valid for 90 days, so keep it to yourself.
                <div className="mt-2 flex flex-wrap items-center gap-2">
                  <button
                    type="button"
                    disabled={create.isPending}
                    onClick={() => create.mutate()}
                    className="rounded-md bg-pegasus-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-pegasus-700 disabled:opacity-50"
                  >
                    {create.isPending ? "Creating…" : bundle ? "Create a new bundle" : "Create bundle"}
                  </button>
                  {bundle && (
                    <a
                      href={api.annexBundleUrl()}
                      download={bundle.filename}
                      className="inline-flex items-center gap-1 rounded-md border border-line px-3 py-1.5 text-xs font-medium text-fg hover:bg-base"
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
              </li>
              <li>
                <span className="font-medium text-fg">Upload the bundle to the cluster.</span>{" "}
                Download it above, then put{" "}
                <code className="font-mono">annex-{name}.tar</code> in your home
                directory on the cluster the way you usually move files there
                (the cluster&apos;s web portal, <code className="font-mono">scp</code>{" "}
                from your laptop, Globus).
              </li>
              <li>
                <span className="font-medium text-fg">On the cluster, set up and submit.</span>{" "}
                Log in to the cluster and paste:
                <Cmd>{clusterCmd}</Cmd>
                {sbatch ? (
                  <p className="mt-1 text-xs text-fgsubtle">
                    The first part saves your Slurm options in{" "}
                    <code className="font-mono">~/.condor/annex_slurm_args</code>, which the setup
                    script puts into <code className="font-mono">hpc.slurm</code>, so you need not edit
                    it now or next time.
                  </p>
                ) : (
                  <p className="mt-1 text-xs text-fgsubtle">
                    Fill in the Slurm options above to have them written for you.
                  </p>
                )}
              </li>
              <li>
                <span className="font-medium text-fg">Watch it come up here.</span>{" "}
                This page refreshes on its own. Once Slurm starts the job, the
                nodes appear above and your queued steps begin running on them.
                In the terminal: <code className="font-mono">htcondor annex status {name}</code>.
              </li>
              <li>
                <span className="font-medium text-fg">Shut it down when you are done</span>{" "}
                with the button above, so it stops using your allocation. Idle
                nodes also leave on their own after a few minutes, and every
                annex ends when its Slurm walltime expires.
              </li>
            </ol>
          </div>
        )}
      </div>
    </section>
  );
}
