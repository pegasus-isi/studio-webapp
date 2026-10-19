"use client";

import { useEffect, useState } from "react";
import { Activity, ChevronsLeft, ChevronsRight, Globe, Server } from "lucide-react";
import type { PoolBucket, ProvisioningStatus, WorkflowDemand } from "@/lib/api/client";
import { useProvisioningStatus } from "@/lib/hooks/use-dashboard";
import { cn, formatDuration } from "@/lib/utils";
import { ComputeSiteCard } from "@/components/resources/compute-site-card";
import { JobSettingsCard } from "@/components/resources/job-settings-card";
import { SourceTabs } from "@/components/resources/source-tabs";
import { StatusCard, TONE_DOT, type Tone } from "@/components/resources/status-card";
import { usePanelStore } from "@/lib/stores/panel-store";

function SectionHeader({ title, subtitle }: { title: string; subtitle?: React.ReactNode }) {
  return (
    <div className="mb-3">
      <h2 className="text-sm font-semibold text-fg">{title}</h2>
      {subtitle && <p className="mt-0.5 text-xs text-fgmuted">{subtitle}</p>}
    </div>
  );
}

const TH = "pb-2 pr-4 font-medium";
const TH_NUM = "pb-2 pl-4 text-right font-medium";
const TD = "py-2 pr-4 text-sm text-fg";
const TD_NUM = "py-2 pl-4 text-right text-sm tabular-nums text-fg";

function PoolRow({ name, bucket }: { name: string; bucket: PoolBucket }) {
  return (
    <tr>
      <td className={TD}>{bucket.label ?? name}</td>
      <td className={TD_NUM}>{bucket.machines}</td>
      <td className={TD_NUM}>
        {bucket.idle_cpus} / {bucket.total_cpus}
      </td>
      <td className={TD_NUM}>
        {bucket.total_gpus > 0 ? `${bucket.idle_gpus} / ${bucket.total_gpus}` : "—"}
      </td>
      <td className={TD_NUM}>
        {Math.round(bucket.idle_memory_mb / 1024)} /{" "}
        {Math.round(bucket.total_memory_mb / 1024)} GB
      </td>
    </tr>
  );
}

/**
 * The answer, before the evidence (#241, Phase 7).
 *
 * A scientist arrives with "can I run my analysis now, or am I going to
 * wait?", so the headline says that. Deliberately hedged rather than
 * precise: whether *your* run waits depends on what it asks for, which we
 * don't know here. The numbers that back it sit in the same card, not behind
 * a disclosure: they are what lets someone judge the verdict for themselves.
 */
function verdictFor(idleCpus: number, totalCpus: number, pressureCount: number, queued: number) {
  if (idleCpus === 0) {
    return {
      tone: "warn" as Tone,
      headline: "Everything is busy right now.",
      detail:
        "All processors are in use. You can still start a run: it will wait " +
        "its turn and begin automatically when something frees up.",
    };
  }
  if (pressureCount > 0 || queued > 0) {
    return {
      tone: "warn" as Tone,
      headline: "There's some capacity, but work is already queued.",
      detail:
        `${idleCpus} of ${totalCpus} processors are free and ${queued} ` +
        `step${queued === 1 ? " is" : "s are"} already waiting, so a new run ` +
        "may take a while to start.",
    };
  }
  return {
    tone: "ok" as Tone,
    headline: "There's capacity available now.",
    detail:
      `${idleCpus} of ${totalCpus} processors are free, so a run started now ` +
      "should begin without much of a wait.",
  };
}

function poolTone(data: ProvisioningStatus): Tone {
  const pool = data.pool;
  if (!pool?.available) return "off";
  return verdictFor(
    pool.idle_cpus ?? 0,
    pool.total_cpus ?? 0,
    data.pressure?.length ?? 0,
    data.demand?.available ? data.demand.idle_jobs ?? 0 : 0
  ).tone;
}

function Overview({ data }: { data: ProvisioningStatus }) {
  const pool = data.pool;
  const demand = data.demand;
  const demandUp = !!demand?.available;
  const workflows = Object.entries(demand?.by_workflow ?? {});
  const pressure = data.pressure ?? [];

  if (!pool?.available) {
    return (
      <div className="rounded-xl border border-line bg-surface p-5">
        <p className="text-sm font-medium text-fg">
          We can&apos;t reach Studio&apos;s own pool right now.
        </p>
        <p className="mt-1 text-sm text-fgmuted">
          You can still run analyses on this machine. If you need the pool, this
          is worth reporting to whoever runs Studio
          {pool?.error ? `. The error was: ${pool.error}` : ""}.
        </p>
      </div>
    );
  }

  const v = verdictFor(
    pool.idle_cpus ?? 0,
    pool.total_cpus ?? 0,
    pressure.length,
    demandUp ? demand?.idle_jobs ?? 0 : 0
  );

  return (
    <div className="space-y-8">
      <StatusCard
        tone={v.tone}
        headline={v.headline}
        detail={v.detail}
        stats={[
          { label: "CPUs free", value: `${pool.idle_cpus ?? 0} / ${pool.total_cpus ?? 0}` },
          {
            label: "GPUs free",
            value: (pool.total_gpus ?? 0) > 0 ? `${pool.idle_gpus ?? 0} / ${pool.total_gpus}` : "—",
          },
          {
            label: "Steps waiting / held",
            value: demandUp ? `${demand?.idle_jobs ?? 0} / ${demand?.held_jobs ?? 0}` : "—",
          },
          { label: "CPUs requested", value: demandUp ? demand?.idle_cpus_requested ?? 0 : "—" },
        ]}
        footer={
          pressure.length > 0 && (
            <ul className="space-y-1">
              {pressure.map((f) => (
                <li key={f.kind}>
                  <span className="font-medium">{f.title}:</span> {f.detail}
                </li>
              ))}
            </ul>
          )
        }
      />

      <section>
        <SectionHeader
          title="Machines"
          subtitle={`${pool.machines ?? 0} machine(s), ${pool.slots ?? 0} slot(s)`}
        />
        <div className="overflow-x-auto rounded-xl border border-line bg-surface px-4 pt-3">
          <table className="w-full text-left">
            <thead>
              <tr className="border-b border-line text-xs text-fgmuted">
                <th className={TH}>Pool</th>
                <th className={TH_NUM}>Machines</th>
                <th className={TH_NUM}>CPUs free</th>
                <th className={TH_NUM}>GPUs free</th>
                <th className={TH_NUM}>Memory free</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {Object.entries(pool.pools ?? {}).map(([name, bucket]) => (
                <PoolRow key={name} name={name} bucket={bucket} />
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section>
        <SectionHeader title="Waiting work" subtitle="Queued Pegasus steps, by workflow" />
        {!demandUp ? (
          <p className="text-sm text-fgmuted">
            Queue not reachable{demand?.error ? ` (${demand.error})` : ""}.
          </p>
        ) : workflows.length === 0 ? (
          <p className="text-sm text-fgmuted">Nothing queued.</p>
        ) : (
          <div className="overflow-x-auto rounded-xl border border-line bg-surface px-4 pt-3">
            <table className="w-full text-left">
              <thead>
                <tr className="border-b border-line text-xs text-fgmuted">
                  <th className={TH}>Workflow</th>
                  <th className={TH_NUM}>Running</th>
                  <th className={TH_NUM}>Waiting</th>
                  <th className={TH_NUM}>Held</th>
                  <th className={TH_NUM}>CPUs / GPUs requested</th>
                  <th className={TH_NUM}>Longest wait</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {workflows.map(([uuid, wf]: [string, WorkflowDemand]) => (
                  <tr key={uuid}>
                    <td className={TD} title={uuid}>
                      {wf.name ?? uuid.slice(0, 8)}
                    </td>
                    <td className={TD_NUM}>{wf.running_jobs}</td>
                    <td className={TD_NUM}>{wf.idle_jobs}</td>
                    <td
                      className={cn(
                        TD_NUM,
                        wf.held_jobs > 0 && "font-medium text-red-600 dark:text-rose-400"
                      )}
                      title={wf.held_reason ?? undefined}
                    >
                      {wf.held_jobs}
                    </td>
                    <td className={TD_NUM}>
                      {wf.idle_cpus_requested} / {wf.idle_gpus_requested}
                    </td>
                    <td className={TD_NUM}>
                      {wf.oldest_idle_seconds != null
                        ? formatDuration(wf.oldest_idle_seconds)
                        : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

/**
 * OSPool capacity and, on the hosted deployment, the settings that decide
 * whether a step may go there at all. The project name is the only reason a
 * hosted user touches job settings, so it lives beside the OSPool numbers
 * rather than in a section of its own.
 */
function OSPoolSection({
  ospool,
  hosted,
}: {
  ospool: ProvisioningStatus["ospool"];
  /** The hosted access point flocks there, for jobs naming a project (#532). */
  hosted: boolean;
}) {
  const mine = ospool?.mine;
  return (
    <div className="space-y-8">
      <section>
        <SectionHeader
          title="OSPool (OSG)"
          subtitle={
            <>
              Your usage and available capacity via {ospool?.cm ?? "the OSG collector"}.
              Other users&rsquo; tasks are not shown.
              {hosted &&
                " Studio sends steps there when its own pool is busy, but only steps that carry an OSPool project name, set below."}
            </>
          }
        />
        {!ospool?.available ? (
          <p className="text-sm text-fgmuted">
            OSPool not reachable{ospool?.error ? ` (${ospool.error})` : ""}.
          </p>
        ) : (
          <StatusCard
            tone={(mine?.cpus ?? 0) > 0 ? "ok" : "off"}
            headline={
              (mine?.cpus ?? 0) > 0
                ? `You have ${mine?.cpus.toLocaleString()} CPUs running on the OSPool.`
                : "Nothing of yours is running on the OSPool right now."
            }
            detail={`${(ospool.avail_cpus ?? 0).toLocaleString()} CPUs and ${(ospool.avail_gpus ?? 0).toLocaleString()} GPUs are free there for jobs that can use them.`}
            stats={[
              { label: "My CPUs running", value: mine?.cpus ?? 0 },
              { label: "My GPUs running", value: (mine?.gpus ?? 0) > 0 ? mine?.gpus ?? 0 : "—" },
              { label: "CPUs available", value: ospool.avail_cpus ?? 0 },
              { label: "GPUs available", value: ospool.avail_gpus ?? 0 },
            ]}
          />
        )}
      </section>
      {hosted && <JobSettingsCard />}
    </div>
  );
}

const SECTIONS = ["overview", "ospool", "compute"] as const;
type SectionId = (typeof SECTIONS)[number];

/**
 * Two deployments, two pages (#532), now one section at a time.
 *
 * The page used to stack everything (verdict, three grids of numbers, the
 * source picker, the annex, OSPool, job settings) and read as clutter. A
 * section menu down the left shows one at a time, and collapses to icons
 * (persisted in the panel store) for anyone who wants the width back. Overview is the default
 * and carries every capacity number with none of it behind a click, which
 * is what the old "Details" disclosure got wrong.
 *
 * On the hosted deployment "Extra computing" is the sources a user can add
 * (source-tabs.tsx: their own cluster as an annex, or cloud and testbeds
 * later), and the OSPool section holds the
 * project name. Under Open OnDemand the cluster is the install, so that
 * section is "Where workflows run" and takes the allocation. Until the
 * status arrives the deployment is unknown, so only Overview is listed:
 * guessing wrong would flash the other deployment's menu.
 *
 * The selection lives in the URL hash so a link can point at a section; a
 * bare hash is relative, so it is safe under the KubeSpawner/OOD prefix.
 */
export default function ProvisioningPage() {
  const { data, isLoading, error } = useProvisioningStatus();
  const collapsed = usePanelStore((s) => s.resourcesNavCollapsed);
  const toggleCollapsed = usePanelStore((s) => s.toggleResourcesNav);
  const [section, setSection] = useState<SectionId>("overview");

  useEffect(() => {
    const read = () => {
      const h = window.location.hash.slice(1) as SectionId;
      if (SECTIONS.includes(h)) setSection(h);
    };
    read();
    window.addEventListener("hashchange", read);
    return () => window.removeEventListener("hashchange", read);
  }, []);

  const go = (id: SectionId) => {
    setSection(id);
    history.replaceState(null, "", `#${id}`);
  };

  const deployment = data?.deployment;
  const hosted = deployment === "hosted";
  const annex = data?.annex;
  const annexLive = !!annex?.available && (!!annex.established || (annex.requests?.pending ?? 0) > 0);

  const items: { id: SectionId; label: string; icon: typeof Server; tone?: Tone }[] = [
    { id: "overview", label: "Overview", icon: Activity, tone: data ? poolTone(data) : undefined },
  ];
  // Hosted always lists OSPool: the project name lives there even when the
  // collector is unreachable.
  if (data?.ospool || hosted) {
    items.push({
      id: "ospool",
      label: "OSPool",
      icon: Globe,
      tone: data?.ospool?.available ? undefined : "off",
    });
  }
  if (hosted) {
    items.push({ id: "compute", label: "Extra computing", icon: Server, tone: annexLive ? "ok" : undefined });
  } else if (deployment === "ood") {
    items.push({ id: "compute", label: "Where workflows run", icon: Server });
  }
  // A hash naming a section this deployment lacks falls back to Overview.
  const active = items.some((i) => i.id === section) ? section : "overview";

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="border-b border-line bg-surface px-6 py-4">
        <h1 className="text-lg font-semibold text-fg">Resources</h1>
        <p className="text-sm text-fgmuted">
          {deployment === "ood"
            ? "Where your analyses run, and how much computing power is available right now."
            : "How much computing power is available right now, and how to bring your own."}
        </p>
      </div>

      <div className="flex min-h-0 flex-1 flex-col md:flex-row">
        <nav
          aria-label="Resources sections"
          className={cn(
            "flex shrink-0 gap-1 overflow-x-auto border-b border-line bg-surface px-3 py-2 md:flex-col md:overflow-visible md:border-b-0 md:border-r md:py-4",
            collapsed ? "md:w-[60px] md:px-2" : "md:w-52"
          )}
        >
          {items.map((it) => {
            const Icon = it.icon;
            const selected = it.id === active;
            return (
              <button
                key={it.id}
                type="button"
                onClick={() => go(it.id)}
                aria-current={selected ? "page" : undefined}
                title={collapsed ? it.label : undefined}
                className={cn(
                  "relative flex shrink-0 items-center gap-2.5 rounded-md px-3 py-2 text-left text-sm transition-colors",
                  selected
                    ? "bg-pegasus-50 font-medium text-pegasus-700 dark:bg-pegasus-400/10 dark:text-pegasus-300"
                    : "text-fgmuted hover:bg-base hover:text-fg"
                )}
              >
                <Icon size={16} aria-hidden="true" className="shrink-0" />
                <span className={cn("whitespace-nowrap", collapsed && "md:sr-only")}>{it.label}</span>
                {it.tone && (
                  <span
                    aria-hidden="true"
                    className={cn(
                      "h-1.5 w-1.5 shrink-0 rounded-full",
                      TONE_DOT[it.tone],
                      collapsed ? "ml-auto md:absolute md:right-1.5 md:top-1.5 md:ml-0" : "ml-auto"
                    )}
                  />
                )}
              </button>
            );
          })}
          {/* Collapse is a desktop affordance: below md the menu is already a
              one-line tab row. Remembered across visits with the rest of the
              panel state. */}
          <button
            type="button"
            onClick={toggleCollapsed}
            aria-label={collapsed ? "Expand menu" : "Collapse menu"}
            title={collapsed ? "Expand menu" : "Collapse menu"}
            className="mt-auto hidden items-center gap-2.5 rounded-md px-3 py-2 text-xs text-fgsubtle hover:bg-base hover:text-fg md:flex"
          >
            {collapsed ? (
              <ChevronsRight size={16} aria-hidden="true" className="shrink-0" />
            ) : (
              <>
                <ChevronsLeft size={16} aria-hidden="true" className="shrink-0" />
                Collapse
              </>
            )}
          </button>
        </nav>

        <div className="min-w-0 flex-1 overflow-auto">
          <div className="mx-auto max-w-4xl p-6">
            {error && (
              <div className="mb-6 rounded-md bg-red-50 p-3 text-sm text-red-700 dark:bg-rose-500/10 dark:text-rose-400">
                Failed to load provisioning status: {(error as Error).message}
              </div>
            )}

            {isLoading || !data ? (
              !error && <p className="text-sm text-fgmuted">Checking…</p>
            ) : active === "ospool" ? (
              <OSPoolSection ospool={data.ospool} hosted={hosted} />
            ) : active === "compute" ? (
              deployment === "ood" ? (
                <ComputeSiteCard />
              ) : (
                <SourceTabs annex={annex} />
              )
            ) : (
              <Overview data={data} />
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
