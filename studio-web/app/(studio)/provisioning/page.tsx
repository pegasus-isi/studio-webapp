"use client";

import type { PoolBucket, WorkflowDemand } from "@/lib/api/client";
import { useProvisioningStatus } from "@/lib/hooks/use-dashboard";
import { cn, formatDuration } from "@/lib/utils";

function Metric({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-lg border border-line bg-surface p-4">
      <p className="text-xs font-medium uppercase tracking-wider text-fgsubtle">
        {label}
      </p>
      <p className="mt-1 text-2xl font-semibold text-fg">{value}</p>
    </div>
  );
}

function PoolRow({ name, bucket }: { name: string; bucket: PoolBucket }) {
  return (
    <tr className="hover:bg-base">
      <td className="py-2 pr-4 text-sm text-fg">{bucket.label ?? name}</td>
      <td className="py-2 pr-4 text-sm">{bucket.machines}</td>
      <td className="py-2 pr-4 font-mono text-sm">
        {bucket.idle_cpus}/{bucket.total_cpus}
      </td>
      <td className="py-2 pr-4 font-mono text-sm">
        {bucket.total_gpus > 0
          ? `${bucket.idle_gpus}/${bucket.total_gpus}`
          : "—"}
      </td>
      <td className="py-2 font-mono text-sm">
        {Math.round(bucket.idle_memory_mb / 1024)}/
        {Math.round(bucket.total_memory_mb / 1024)} GB
      </td>
    </tr>
  );
}

/**
 * The answer, before the evidence (#241, Phase 7).
 *
 * This page was an HTCondor admin console: idle/total CPUs, queued/held jobs,
 * slot inventory, per-workflow demand. All true, none of it answering the
 * question a scientist actually arrives with — "can I run my analysis now, or
 * am I going to wait?"
 *
 * Deliberately hedged rather than precise. Whether *your* run waits depends on
 * what it asks for, which we don't know here, so this says what is generally
 * true and stops short of a promise it can't keep.
 */
function Verdict({
  idleCpus,
  totalCpus,
  pressureCount,
  queued,
}: {
  idleCpus: number;
  totalCpus: number;
  pressureCount: number;
  queued: number;
}) {
  let tone = "border-green-300 bg-green-50 dark:border-emerald-500/40 dark:bg-emerald-500/10";
  let headline = "There's capacity available now.";
  let detail =
    `${idleCpus} of ${totalCpus} processors are free, so a run started now ` +
    "should begin without much of a wait.";

  if (idleCpus === 0) {
    tone = "border-amber-300 bg-amber-50 dark:border-amber-500/40 dark:bg-amber-500/10";
    headline = "Everything is busy right now.";
    detail =
      "All processors are in use. You can still start a run — it will wait " +
      "its turn and begin automatically when something frees up.";
  } else if (pressureCount > 0 || queued > 0) {
    tone = "border-amber-300 bg-amber-50 dark:border-amber-500/40 dark:bg-amber-500/10";
    headline = "There's some capacity, but work is already queued.";
    detail =
      `${idleCpus} of ${totalCpus} processors are free and ${queued} ` +
      `step${queued === 1 ? " is" : "s are"} already waiting, so a new run ` +
      "may take a while to start.";
  }

  return (
    <div className={cn("rounded-xl border p-5", tone)}>
      <p className="text-base font-semibold text-fg">{headline}</p>
      <p className="mt-1 text-sm text-fgmuted">{detail}</p>
    </div>
  );
}

export default function ProvisioningPage() {
  const { data, isLoading, error } = useProvisioningStatus();

  const pool = data?.pool;
  const demand = data?.demand;
  const poolUp = !!pool?.available;
  const demandUp = !!demand?.available;
  const workflows = Object.entries(demand?.by_workflow ?? {});

  return (
    <div className="h-full overflow-auto">
      <div className="border-b border-line bg-surface px-6 py-4">
        <h1 className="text-lg font-semibold text-fg">Computing capacity</h1>
        <p className="text-sm text-fgmuted">
          How much computing power is available to run your analyses right now.
        </p>
      </div>

      {error && (
        <div className="m-6 rounded-md bg-red-50 p-3 text-sm text-red-700 dark:bg-rose-500/10 dark:text-rose-400">
          Failed to load provisioning status: {(error as Error).message}
        </div>
      )}

      <div className="space-y-6 p-6">
        {isLoading ? (
          <p className="text-sm text-fgmuted">Checking…</p>
        ) : !poolUp ? (
          <div className="rounded-md border border-line bg-surface p-4">
            <p className="text-sm font-medium text-fg">
              We can&apos;t reach the shared cluster right now.
            </p>
            <p className="mt-1 text-sm text-fgmuted">
              You can still run analyses on this machine. If you need the
              cluster, this is worth reporting to whoever runs Studio
              {pool?.error ? ` — the error was: ${pool.error}` : ""}.
            </p>
          </div>
        ) : (
          <>
            {/* The one sentence this page exists to answer (#241, Phase 7).
                Everything below is the evidence for it, and belongs under
                Details rather than in front of someone who just wants to know
                whether to press Run. */}
            <Verdict
              idleCpus={pool.idle_cpus ?? 0}
              totalCpus={pool.total_cpus ?? 0}
              pressureCount={data?.pressure.length ?? 0}
              queued={demandUp ? demand?.idle_jobs ?? 0 : 0}
            />

            {/* The numbers behind the verdict. Kept in full — an operator
                needs them — but behind a disclosure so they stop being the
                first thing on the page. */}
            <details>
              <summary className="cursor-pointer text-sm font-medium text-fgmuted hover:text-fg">
                Details
              </summary>
              <div className="mt-4 space-y-6">
            {/* Supply vs demand */}
            <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
              <Metric
                label="Idle / total CPUs"
                value={`${pool.idle_cpus ?? 0}/${pool.total_cpus ?? 0}`}
              />
              <Metric
                label="Idle / total GPUs"
                value={
                  (pool.total_gpus ?? 0) > 0
                    ? `${pool.idle_gpus ?? 0}/${pool.total_gpus}`
                    : "—"
                }
              />
              <Metric
                label="CPUs requested (idle jobs)"
                value={demandUp ? demand?.idle_cpus_requested ?? 0 : "—"}
              />
              <Metric
                label="Queued / held jobs"
                value={
                  demandUp
                    ? `${demand?.idle_jobs ?? 0} / ${demand?.held_jobs ?? 0}`
                    : "—"
                }
              />
            </div>

            {/* Pressure findings */}
            {(data?.pressure.length ?? 0) > 0 && (
              <div className="rounded-md border border-amber-300 bg-amber-50 p-4 dark:border-amber-500/40 dark:bg-amber-500/10">
                <p className="text-sm font-semibold text-amber-800 dark:text-amber-300">
                  Resources required
                </p>
                <ul className="mt-1 space-y-1 text-sm text-amber-700 dark:text-amber-200">
                  {data?.pressure.map((f) => (
                    <li key={f.kind}>
                      <span className="font-medium">{f.title}</span> —{" "}
                      {f.detail}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {/* Pool inventory */}
            <div className="rounded-lg border border-line bg-surface">
              <div className="border-b border-line px-4 py-3">
                <h2 className="text-sm font-semibold text-fg">
                  Pool inventory
                </h2>
                <p className="text-xs text-fgmuted">
                  {pool.machines ?? 0} machine(s), {pool.slots ?? 0} slot(s)
                </p>
              </div>
              <div className="overflow-x-auto p-4">
                <table className="w-full text-left">
                  <thead>
                    <tr className="border-b border-line text-xs font-medium text-fgmuted">
                      <th className="pb-2 pr-4">Pool</th>
                      <th className="pb-2 pr-4">Machines</th>
                      <th className="pb-2 pr-4">CPUs idle/total</th>
                      <th className="pb-2 pr-4">GPUs idle/total</th>
                      <th className="pb-2">Memory idle/total</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-line">
                    {Object.entries(pool.pools ?? {}).map(([name, bucket]) => (
                      <PoolRow key={name} name={name} bucket={bucket} />
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

            {/* Per-workflow demand */}
            <div className="rounded-lg border border-line bg-surface">
              <div className="border-b border-line px-4 py-3">
                <h2 className="text-sm font-semibold text-fg">
                  Workflow demand
                </h2>
                <p className="text-xs text-fgmuted">
                  Queued Pegasus jobs grouped by workflow
                </p>
              </div>
              {!demandUp ? (
                <p className="p-4 text-sm text-fgmuted">
                  Queue not reachable{demand?.error ? ` (${demand.error})` : ""}
                  .
                </p>
              ) : workflows.length === 0 ? (
                <p className="p-4 text-sm text-fgmuted">
                  No Pegasus jobs in the queue.
                </p>
              ) : (
                <div className="overflow-x-auto p-4">
                  <table className="w-full text-left">
                    <thead>
                      <tr className="border-b border-line text-xs font-medium text-fgmuted">
                        <th className="pb-2 pr-4">Workflow</th>
                        <th className="pb-2 pr-4">Running</th>
                        <th className="pb-2 pr-4">Idle</th>
                        <th className="pb-2 pr-4">Held</th>
                        <th className="pb-2 pr-4">CPUs / GPUs requested</th>
                        <th className="pb-2">Longest wait</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-line">
                      {workflows.map(
                        ([uuid, wf]: [string, WorkflowDemand]) => (
                          <tr key={uuid} className="hover:bg-base">
                            <td
                              className="py-2 pr-4 text-sm text-fg"
                              title={uuid}
                            >
                              {wf.name ?? uuid.slice(0, 8)}
                            </td>
                            <td className="py-2 pr-4 text-sm">
                              {wf.running_jobs}
                            </td>
                            <td className="py-2 pr-4 text-sm">
                              {wf.idle_jobs}
                            </td>
                            <td
                              className={cn(
                                "py-2 pr-4 text-sm",
                                wf.held_jobs > 0 &&
                                  "font-medium text-red-600 dark:text-rose-400"
                              )}
                              title={wf.held_reason ?? undefined}
                            >
                              {wf.held_jobs}
                            </td>
                            <td className="py-2 pr-4 font-mono text-sm">
                              {wf.idle_cpus_requested} /{" "}
                              {wf.idle_gpus_requested}
                            </td>
                            <td className="py-2 text-sm">
                              {wf.oldest_idle_seconds != null
                                ? formatDuration(wf.oldest_idle_seconds)
                                : "—"}
                            </td>
                          </tr>
                        )
                      )}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
              </div>
            </details>
          </>
        )}

        {/* OSPool (OSG) — shown independently of the local pool. */}
        {!isLoading && data?.ospool && <OSPoolPanel ospool={data.ospool} />}
      </div>
    </div>
  );
}

function OSPoolPanel({
  ospool,
}: {
  ospool: NonNullable<import("@/lib/api/client").ProvisioningStatus["ospool"]>;
}) {
  const mine = ospool.mine;
  return (
    <div className="rounded-lg border border-line bg-surface">
      <div className="border-b border-line px-4 py-3">
        <h2 className="text-sm font-semibold text-fg">OSPool (OSG)</h2>
        <p className="text-xs text-fgmuted">
          Your usage and available capacity via{" "}
          {ospool.cm ?? "the OSG collector"}. Other users&rsquo; tasks are not
          shown.
        </p>
      </div>
      {!ospool.available ? (
        <p className="p-4 text-sm text-fgmuted">
          OSPool not reachable{ospool.error ? ` (${ospool.error})` : ""}.
        </p>
      ) : (
        <div className="grid grid-cols-2 gap-4 p-4 lg:grid-cols-4">
          <Metric label="My CPUs (running)" value={mine?.cpus ?? 0} />
          <Metric
            label="My GPUs (running)"
            value={(mine?.gpus ?? 0) > 0 ? (mine?.gpus ?? 0) : "—"}
          />
          <Metric label="Available CPUs" value={ospool.avail_cpus ?? 0} />
          <Metric label="Available GPUs" value={ospool.avail_gpus ?? 0} />
        </div>
      )}
    </div>
  );
}
