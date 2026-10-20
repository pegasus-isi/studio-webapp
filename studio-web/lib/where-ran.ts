/**
 * Where a run's steps executed, grouped for people (#556).
 *
 * The hostname comes from Pegasus's own records (each job's kickstart, via
 * the stampede DB), so it is the machine the step actually ran on. Raw
 * hostnames are noise at a glance (29 lines of exp-1-19.expanse.sdsc.edu),
 * so they are grouped:
 *
 * - jobs on Pegasus's "local" site ran on the access point: Pegasus's own
 *   bookkeeping (directories, staging, cleanup), not the science;
 * - a bare hostname with no domain is one of Studio's pool pods
 *   (ep-67bb7f5dd-bk6kw, studio-cpu-shared-…): Kubernetes pod names have
 *   no dots, and nothing outside the cluster reports one;
 * - anything else is grouped by its domain, so every node of an annex on
 *   Expanse shows as one "expanse.sdsc.edu" line, and OSPool slots under
 *   their institutions' domains.
 */

export type WhereGroup = {
  key: string;
  label: string;
  /** What the group is, in a few words, or null for a plain domain. */
  kind: "access-point" | "studio-pool" | "domain";
  steps: number;
  hosts: string[];
};

type JobLike = { host?: unknown; site?: unknown };

export function placeOf(host: string, site: string | null): Pick<WhereGroup, "key" | "label" | "kind"> {
  if (site === "local") return { key: "local", label: "Access point", kind: "access-point" };
  const dot = host.indexOf(".");
  if (dot < 0) return { key: "pool", label: "Studio pool", kind: "studio-pool" };
  const domain = host.slice(dot + 1);
  return { key: `d:${domain}`, label: domain, kind: "domain" };
}

/** Groups, largest first, with the access point last (it is bookkeeping). */
export function whereRan(jobs: JobLike[]): WhereGroup[] {
  const groups = new Map<string, WhereGroup>();
  for (const j of jobs) {
    const host = typeof j.host === "string" && j.host ? j.host : null;
    if (!host) continue;
    const site = typeof j.site === "string" ? j.site : null;
    const p = placeOf(host, site);
    const g = groups.get(p.key) ?? { ...p, steps: 0, hosts: [] };
    g.steps += 1;
    if (!g.hosts.includes(host)) g.hosts.push(host);
    groups.set(p.key, g);
  }
  return [...groups.values()].sort((a, b) => {
    if (a.kind === "access-point" !== (b.kind === "access-point")) {
      return a.kind === "access-point" ? 1 : -1;
    }
    return b.steps - a.steps;
  });
}
