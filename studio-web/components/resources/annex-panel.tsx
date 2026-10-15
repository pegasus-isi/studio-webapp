"use client";

/**
 * The user's HTCondor annex on the hosted deployment (#532): what it is
 * doing right now, and how to start one.
 *
 * An annex is a set of nodes the user leases from a Slurm cluster they
 * have an account on (a campus cluster, an ACCESS allocation). The nodes
 * join Studio's access point and run only that user's jobs; when the annex
 * is down the jobs still run on Studio's own pool. The access point names
 * every job's annex after its owner, so the annex MUST be called by the
 * username, and the instructions below are rendered with it filled in.
 *
 * Status comes from the same three queries `htcondor annex status` makes,
 * structured (services/annex.py); the CLI's only output is prose.
 */

import { useState } from "react";
import { Cloud } from "lucide-react";
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

function Cmd({ children }: { children: string }) {
  return (
    <pre className="mt-1 overflow-x-auto rounded bg-muted px-3 py-2 font-mono text-xs text-fg">
      {children}
    </pre>
  );
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
  const name = annex.name || "$USER";
  const v = verdict(annex);
  // Open the instructions when there is nothing to report yet.
  const [showHow, setShowHow] = useState(!annex.available || !annex.established);

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
        <p className="text-sm font-semibold text-fg">{v.headline}</p>
        <p className="mt-1 text-sm text-fgmuted">{v.detail}</p>
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
          <span>How to add an annex from a cluster</span>
          <span aria-hidden="true">{showHow ? "−" : "+"}</span>
        </button>
        {showHow && (
          <div className="space-y-3 border-t border-line px-4 py-3 text-sm text-fgmuted">
            <p>
              Works with any Slurm cluster you can log in to, for example a
              campus cluster or an ACCESS allocation. The commands below run in
              Studio&apos;s terminal unless they say otherwise. Full details are in
              HTCondor&apos;s{" "}
              <a
                href="https://htcondor.readthedocs.io/en/latest/users-manual/bringing-your-own-resources.html"
                target="_blank"
                rel="noreferrer"
                className="underline"
              >
                Bringing Your Own Resources
              </a>{" "}
              guide; skip its first step (submitting the job), Studio does that
              for you.
            </p>
            <div className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-500/40 dark:bg-amber-500/10 dark:text-amber-200">
              <strong>Two rules.</strong> Name the annex{" "}
              <code className="font-mono">{name}</code>, your username: Studio
              sends your jobs to an annex of that name and no other. And submit
              your workflow first: annex nodes that find nothing to run shut
              themselves down after a few minutes.
            </div>
            <ol className="list-decimal space-y-3 pl-5">
              <li>
                <span className="font-medium text-fg">Create the annex here.</span>{" "}
                This writes <code className="font-mono">annex-{name}.tar</code>{" "}
                in the current directory.
                <Cmd>{`htcondor annex create ${name}`}</Cmd>
              </li>
              <li>
                <span className="font-medium text-fg">Copy the tarfile to the cluster.</span>
                <Cmd>{`scp annex-${name}.tar <login-node-of-your-cluster>:`}</Cmd>
              </li>
              <li>
                <span className="font-medium text-fg">On the cluster, set up and submit the annex job.</span>{" "}
                Edit the generated <code className="font-mono">hpc.slurm</code>{" "}
                to set your partition, account, walltime and node count, then
                submit it.
                <Cmd>{`tar xf annex-${name}.tar\ncd annex-${name}\n./annex-setup.sh\n# edit hpc.slurm, then:\nsbatch hpc.slurm`}</Cmd>
              </li>
              <li>
                <span className="font-medium text-fg">Watch it come up</span>, here
                or in the terminal. Once Slurm starts the job, the nodes appear
                above and your queued steps begin running on them.
                <Cmd>{`htcondor annex status ${name}`}</Cmd>
              </li>
              <li>
                <span className="font-medium text-fg">Shut it down when you are done</span>{" "}
                so it stops using your allocation. Idle nodes also leave on
                their own after a few minutes, and every annex ends when its
                Slurm walltime expires.
                <Cmd>{`htcondor annex shutdown ${name}`}</Cmd>
              </li>
            </ol>
            <p className="text-xs text-fgsubtle">
              Tip: if you add annexes often, put your{" "}
              <code className="font-mono">#SBATCH</code> options in{" "}
              <code className="font-mono">~/.condor/annex_slurm_args</code> on the
              cluster so you need not edit{" "}
              <code className="font-mono">hpc.slurm</code> each time.
            </p>
          </div>
        )}
      </div>
    </section>
  );
}
