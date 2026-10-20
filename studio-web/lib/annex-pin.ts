/**
 * "Send runs to your annex only" as ~/.pegasusrc site profiles (#558).
 *
 * Studio's access point sends every job to the user's annex *or* the pool
 * (annex-or-pool). In the first live test the pool, idle, took every step
 * before Expanse's node arrived, and the node found nothing to run. Pinning
 * meant typing a ClassAd requirements expression into Advanced site
 * settings under the right namespace, and a container workflow also needed
 * the cluster's Apptainer on the job's PATH (an env profile, which was
 * first saved under "dagman" by mistake). This turns both into rows Studio
 * manages: on adds them, off removes exactly those, and a requirements row
 * the user wrote themselves is never touched.
 */

import type { SiteOverride } from "@/lib/api/client";

/** Directories after the runtime's, so the job still finds the basics. */
export const PATH_TAIL = ":/usr/local/bin:/usr/bin:/bin";

export const pinValue = (name: string) => `TARGET.AnnexName =?= "${name}"`;

const isReq = (r: SiteOverride, site: string) =>
  r.site === site && r.namespace === "condor" && r.key === "requirements";
const isPath = (r: SiteOverride, site: string) =>
  r.site === site && r.namespace === "env" && r.key === "PATH";

/** A folder, absolute, with nothing a PATH or a profile line would choke on. */
export const validRuntimeDir = (d: string) => /^\/[A-Za-z0-9._+/-]*$/.test(d.trim()) && !d.includes(":");

export type PinState = {
  on: boolean;
  /** A requirements row of the user's own: the toggle leaves it alone. */
  foreign: string | null;
  /** The runtime folder from a PATH row this toggle wrote, or "". */
  runtimeDir: string;
};

export function pinState(rows: SiteOverride[], site: string, name: string): PinState {
  const req = rows.find((r) => isReq(r, site));
  const on = !!req && req.value.trim() === pinValue(name);
  const path = rows.find((r) => isPath(r, site));
  const runtimeDir =
    on && path && path.value.endsWith(PATH_TAIL) ? path.value.slice(0, -PATH_TAIL.length) : "";
  return { on, foreign: req && !on ? req.value : null, runtimeDir };
}

/** The rows with the pin set as asked. Off removes only what on would add. */
export function applyPin(
  rows: SiteOverride[],
  site: string,
  name: string,
  on: boolean,
  runtimeDir = ""
): SiteOverride[] {
  const before = pinState(rows, site, name);
  if (before.foreign) return rows;
  const ours = (r: SiteOverride) =>
    (isReq(r, site) && r.value.trim() === pinValue(name)) ||
    (isPath(r, site) && before.on && r.value.endsWith(PATH_TAIL));
  const rest = rows.filter((r) => !ours(r));
  if (!on) return rest;
  const next: SiteOverride[] = [...rest, { site, namespace: "condor", key: "requirements", value: pinValue(name) }];
  const dir = runtimeDir.trim().replace(/\/+$/, "");
  if (dir && validRuntimeDir(dir) && !rest.some((r) => isPath(r, site))) {
    next.push({ site, namespace: "env", key: "PATH", value: `${dir}${PATH_TAIL}` });
  }
  return next;
}
