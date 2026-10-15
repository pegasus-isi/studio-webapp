"use client";

/**
 * Rows of site profiles laid over the active catalog (#478):
 * pegasus.catalog.site.sites.<site>.profiles.<namespace>.<key> = <value>.
 *
 * Shared by the OOD app's "Where your workflows run" card and the hosted
 * deployment's job-settings card (#532): the same ~/.pegasusrc lines, two
 * different reasons to set them (a Slurm queue there, a condor requirement
 * or OSPool project here).
 */

import { Plus, X } from "lucide-react";
import type { SiteOverride } from "@/lib/api/client";

// Suggestions only; any namespace key Pegasus accepts can be typed.
const KEY_HINTS: Record<string, string[]> = {
  condor: ["requirements", "request_memory", "request_cpus", "request_disk", "+ProjectName", "priority"],
  pegasus: ["queue", "project", "runtime", "cores", "nodes", "memory", "glite.arguments", "auxillary.local"],
  env: ["PATH", "PYTHONPATH", "OMP_NUM_THREADS"],
  dagman: ["retry", "maxjobs", "post.scope"],
  globus: ["maxwalltime", "queue", "project"],
  selector: ["execution.site"],
};

export type Row = SiteOverride;

export const blankRow = (): Row => ({ site: "compute", namespace: "condor", key: "", value: "" });
export const filled = (rows: Row[]) => rows.filter((r) => r.key.trim());
export const sameRows = (a: Row[], b: Row[]) =>
  JSON.stringify(filled(a).map((r) => [r.site, r.namespace, r.key, r.value])) ===
  JSON.stringify(filled(b).map((r) => [r.site, r.namespace, r.key, r.value]));

const inputCls =
  "rounded-md border border-line bg-base px-2 py-1 font-mono text-xs text-fg outline-none focus:border-pegasus-400";

export function SiteOverridesEditor({
  rows,
  onChange,
  namespaces,
  site,
  effectiveSite,
  intro,
}: {
  rows: Row[];
  onChange: (rows: Row[]) => void;
  namespaces: string[];
  /** Name of the execution site, for the "as Pegasus sees it" preview. */
  site: string;
  /** YAML of that site with the saved overrides applied, if known. */
  effectiveSite: string | null;
  intro: React.ReactNode;
}) {
  return (
    <div className="space-y-2">
      <p className="text-[11px] text-fgsubtle">
        {intro} A <code className="font-mono">condor</code> value is a ClassAd
        expression: a string such as a project name needs its quotes, as in{" "}
        <code className="font-mono">&quot;USC_Deelman&quot;</code>.
      </p>
      <datalist id="site-override-sites">
        <option value="compute" />
        <option value="local" />
        <option value="condorpool" />
      </datalist>
      {rows.map((r, i) => {
        const set = (patch: Partial<Row>) =>
          onChange(rows.map((x, j) => (j === i ? { ...x, ...patch } : x)));
        const listId = `site-override-keys-${r.namespace}`;
        return (
          <div key={i} className="flex flex-wrap items-center gap-1.5">
            <input
              aria-label="Site"
              list="site-override-sites"
              value={r.site}
              onChange={(e) => set({ site: e.target.value })}
              spellCheck={false}
              className={`${inputCls} w-24`}
            />
            <select
              aria-label="Namespace"
              value={r.namespace}
              onChange={(e) => set({ namespace: e.target.value })}
              className={`${inputCls} w-24`}
            >
              {namespaces.map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
            <input
              aria-label="Key"
              list={listId}
              value={r.key}
              placeholder="key"
              onChange={(e) => set({ key: e.target.value })}
              spellCheck={false}
              className={`${inputCls} w-36`}
            />
            <datalist id={listId}>
              {(KEY_HINTS[r.namespace] ?? []).map((k) => (
                <option key={k} value={k} />
              ))}
            </datalist>
            <input
              aria-label="Value"
              value={r.value}
              placeholder="value"
              onChange={(e) => set({ value: e.target.value })}
              spellCheck={false}
              className={`${inputCls} min-w-0 flex-1`}
            />
            <button
              type="button"
              aria-label="Remove setting"
              onClick={() => onChange(rows.filter((_, j) => j !== i))}
              className="rounded p-1 text-fgsubtle hover:bg-muted hover:text-fg"
            >
              <X size={14} aria-hidden="true" />
            </button>
          </div>
        );
      })}
      <button
        type="button"
        onClick={() => onChange([...rows, blankRow()])}
        className="inline-flex items-center gap-1 text-xs font-medium text-pegasus-700 hover:underline dark:text-pegasus-300"
      >
        <Plus size={13} aria-hidden="true" /> Add setting
      </button>
      {effectiveSite && (
        <details className="pt-1">
          <summary className="cursor-pointer text-[11px] text-fgsubtle">
            The “{site}” site as Pegasus sees it (as saved)
          </summary>
          <pre className="mt-1 max-h-64 overflow-auto rounded bg-muted p-2 font-mono text-[11px] text-fg">
            {effectiveSite}
          </pre>
        </details>
      )}
    </div>
  );
}
