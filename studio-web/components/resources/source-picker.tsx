"use client";

/**
 * Where extra compute comes from, on the hosted deployment (#532).
 *
 * Studio's own pool is always there. Beyond it a user has two kinds of
 * source, told apart by whether anything has to be *provisioned*:
 *
 * - a cluster they already have an account on (a campus cluster, an ACCESS
 *   allocation): nothing to provision, they lease nodes from its scheduler
 *   as an HTCondor annex and the nodes join the access point;
 * - a cloud or testbed (FABRIC, Chameleon, Jetstream2, ...): the machines
 *   do not exist yet, so Studio has to create them first, after which they
 *   join the access point the same way. Named generically here, as one
 *   source kind with providers under it, so adding a provider is a row in a
 *   list and not a new card.
 *
 * The choice is a per-user preference. It selects what the page explains
 * and shows; it does not route jobs, which the access point does on its own
 * (every job may run on the pool or on the user's annex). The provisioned
 * kind is shown but not selectable until it exists: a card that cannot be
 * clicked is the agreed way to show what is coming (#522).
 */

import { Cloud, Server, Sparkles } from "lucide-react";
import { cn } from "@/lib/utils";

export type ResourceSource = "pool" | "annex";

const OPTIONS: {
  id: ResourceSource | "provisioned";
  title: string;
  detail: string;
  icon: typeof Server;
  soon?: boolean;
}[] = [
  {
    id: "pool",
    title: "Studio's own pool",
    detail: "Shared machines run by Studio. Nothing to set up; capacity is what is free right now.",
    icon: Server,
  },
  {
    id: "annex",
    title: "A cluster you already have",
    detail:
      "A campus cluster or an ACCESS allocation. Lease nodes from its scheduler; they join Studio and run only your work.",
    icon: Cloud,
  },
  {
    id: "provisioned",
    title: "Provisioned resources",
    detail:
      "Cloud and testbed providers such as FABRIC, Chameleon or Jetstream2, where Studio creates the machines first and they then join like an annex.",
    icon: Sparkles,
    soon: true,
  },
];

export function SourcePicker({
  value,
  onChange,
}: {
  value: ResourceSource;
  onChange: (next: ResourceSource) => void;
}) {
  return (
    <section>
      <h2 className="text-sm font-semibold text-fg">Where extra computing power comes from</h2>
      <div
        role="radiogroup"
        aria-label="Source of computing power"
        className="mt-2 grid gap-3 md:grid-cols-3"
      >
        {OPTIONS.map((o) => {
          const Icon = o.icon;
          const selected = o.id === value;
          if (o.soon) {
            return (
              <div
                key={o.id}
                aria-disabled="true"
                className="rounded-xl border border-dashed border-line bg-surface p-4 opacity-70"
              >
                <div className="flex items-center gap-2">
                  <Icon size={16} aria-hidden="true" className="shrink-0 text-fgsubtle" />
                  <span className="text-sm font-semibold text-fg">{o.title}</span>
                  <span className="ml-auto rounded-full border border-line px-2 py-0.5 text-[10px] font-medium uppercase tracking-wider text-fgsubtle">
                    Coming soon
                  </span>
                </div>
                <p className="mt-1.5 text-xs text-fgmuted">{o.detail}</p>
              </div>
            );
          }
          return (
            <button
              key={o.id}
              type="button"
              role="radio"
              aria-checked={selected}
              onClick={() => onChange(o.id as ResourceSource)}
              className={cn(
                "rounded-xl border bg-surface p-4 text-left transition-colors",
                selected
                  ? "border-pegasus-500 ring-1 ring-pegasus-500"
                  : "border-line hover:border-pegasus-300"
              )}
            >
              <div className="flex items-center gap-2">
                <Icon
                  size={16}
                  aria-hidden="true"
                  className={cn("shrink-0", selected ? "text-pegasus-600 dark:text-pegasus-300" : "text-fgsubtle")}
                />
                <span className="text-sm font-semibold text-fg">{o.title}</span>
              </div>
              <p className="mt-1.5 text-xs text-fgmuted">{o.detail}</p>
            </button>
          );
        })}
      </div>
    </section>
  );
}
