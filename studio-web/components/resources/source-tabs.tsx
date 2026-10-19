"use client";

/**
 * Where extra compute comes from, on the hosted deployment (#532, #544).
 *
 * Studio's own pool is always there, so it is not a choice here: it used to
 * be a card beside the others, which read as something to pick, and
 * picking it did nothing but hide the annex how-to. Its live numbers are
 * under Overview. What is offered is what a user can add, as two tabs
 * rather than three large cards:
 *
 * - "Your cluster": a cluster they already have an account on (a campus
 *   cluster, an ACCESS allocation). Nothing to provision; they lease nodes
 *   from its scheduler as an HTCondor annex and the nodes join the access
 *   point. The tab is the annex's status and setup checklist.
 * - "Cloud & testbeds" (FABRIC, Chameleon, Jetstream2, ...): the machines
 *   do not exist yet, so Studio has to create them first. Not built; the
 *   tab says so, which is the agreed way to show what is coming (#522).
 *
 * Tabs select what is shown, never what runs: the access point routes every
 * job to the pool or the user's annex on its own. The `resource_source`
 * UI preference that the old picker wrote is no longer read here.
 */

import { useState } from "react";
import { Cloud, Sparkles } from "lucide-react";
import type { AnnexStatus } from "@/lib/api/client";
import { AnnexPanel } from "@/components/resources/annex-panel";
import { cn } from "@/lib/utils";

type Tab = "cluster" | "cloud";

export function SourceTabs({ annex }: { annex?: AnnexStatus }) {
  const [tab, setTab] = useState<Tab>("cluster");
  const live = !!annex?.available && (!!annex.established || (annex.requests?.pending ?? 0) > 0);

  const tabs: { id: Tab; label: string; icon: typeof Cloud; badge?: React.ReactNode }[] = [
    {
      id: "cluster",
      label: "Your cluster",
      icon: Cloud,
      badge: live ? <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-emerald-500" /> : undefined,
    },
    {
      id: "cloud",
      label: "Cloud & testbeds",
      icon: Sparkles,
      badge: (
        <span className="rounded-full border border-line px-1.5 py-px text-2xs font-medium uppercase tracking-wider text-fgsubtle">
          Soon
        </span>
      ),
    },
  ];

  return (
    <section>
      <h2 className="text-sm font-semibold text-fg">Add computing power</h2>
      <p className="mt-0.5 text-xs text-fgmuted">
        Studio&apos;s own pool is always available; its capacity is under Overview.
        When you need more, bring your own.
      </p>

      <div role="tablist" aria-label="Source of extra computing" className="mt-3 flex gap-1 border-b border-line">
        {tabs.map((t) => {
          const Icon = t.icon;
          const selected = t.id === tab;
          return (
            <button
              key={t.id}
              type="button"
              role="tab"
              aria-selected={selected}
              onClick={() => setTab(t.id)}
              className={cn(
                "-mb-px flex items-center gap-2 border-b-2 px-3 py-2 text-sm transition-colors",
                selected
                  ? "border-pegasus-600 font-medium text-pegasus-700 dark:border-pegasus-300 dark:text-pegasus-300"
                  : "border-transparent text-fgmuted hover:text-fg"
              )}
            >
              <Icon size={15} aria-hidden="true" className="shrink-0" />
              {t.label}
              {t.badge}
            </button>
          );
        })}
      </div>

      <div role="tabpanel" className="pt-4">
        {tab === "cluster" ? (
          <>
            <p className="mb-4 text-sm text-fgmuted">
              Lease nodes from a campus cluster or an ACCESS allocation you already
              have. They join Studio and run only your work; when they leave,
              Studio&apos;s own pool carries on. Studio calls these leased nodes your
              annex.
            </p>
            {annex ? (
              <AnnexPanel annex={annex} />
            ) : (
              <p className="text-sm text-fgmuted">Annex status is not available.</p>
            )}
          </>
        ) : (
          <div className="rounded-xl border border-dashed border-line p-6 text-center">
            <Sparkles size={20} aria-hidden="true" className="mx-auto text-fgsubtle" />
            <p className="mt-2 text-sm font-medium text-fg">Coming soon</p>
            <p className="mx-auto mt-1 max-w-md text-sm text-fgmuted">
              Cloud and testbed providers such as FABRIC, Chameleon or Jetstream2,
              where Studio creates the machines first and they then join like an
              annex.
            </p>
          </div>
        )}
      </div>
    </section>
  );
}
