"use client";

/**
 * "Where it ran": a run's steps grouped by the machines they executed on
 * (#556). After the first annex test the only way to see that 31 steps had
 * run on Expanse was condor_history in a terminal; this puts it on the run
 * page. Grouping rules are in lib/where-ran.ts.
 */

import { Building2, Server, Globe } from "lucide-react";
import { whereRan, type WhereGroup } from "@/lib/where-ran";
import { formatCount } from "@/lib/utils";
import { EYEBROW } from "@/lib/ui";

const ICON: Record<WhereGroup["kind"], typeof Server> = {
  "studio-pool": Server,
  domain: Globe,
  "access-point": Building2,
};

function describe(g: WhereGroup): string {
  const steps = `${formatCount(g.steps)} step${g.steps === 1 ? "" : "s"}`;
  if (g.kind === "access-point") return `${steps} (Pegasus's own bookkeeping)`;
  if (g.hosts.length > 1) return `${steps} on ${formatCount(g.hosts.length)} machines`;
  return steps;
}

export function WhereItRan({ jobs, compact }: { jobs: Record<string, unknown>[]; compact?: boolean }) {
  const groups = whereRan(jobs);
  if (groups.length === 0) return null;
  return (
    <div className={compact ? "mb-2" : "mb-4"}>
      <p className={EYEBROW}>Where it ran</p>
      <ul className="mt-1.5 flex flex-wrap gap-2">
        {groups.map((g) => {
          const Icon = ICON[g.kind];
          return (
            <li
              key={g.key}
              title={g.hosts.join("\n")}
              className="flex items-center gap-1.5 rounded-full border border-line bg-surface px-2.5 py-1 text-xs"
            >
              <Icon size={13} aria-hidden="true" className="shrink-0 text-fgsubtle" />
              <span className="font-medium text-fg">{g.label}</span>
              <span className="tabular-nums text-fgmuted">{describe(g)}</span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
