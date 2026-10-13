"use client";

/**
 * "Where your workflows run": the hosted site catalog and allocation (#468).
 *
 * Workflows no longer define where they run (the pegasus-gromacs template,
 * #466). The site comes from a centrally hosted catalog named in
 * ~/.pegasusrc, and the allocation travels as RESOURCE_* properties the
 * catalog references — the ACCESS setup, done from a form instead of a
 * notebook cell. The fields shown are the ones the chosen catalog actually
 * uses, so Unity asks for nothing and NERSC asks for three.
 *
 * "Advanced: site settings" (#478) adds profiles on top of the catalog —
 * a condor requirement, a queue, an env var — as Pegasus' own
 * pegasus.catalog.site.sites.* properties, so the published catalog stays
 * untouched and the planner merges them in. Kept per cluster: every catalog
 * calls its site "compute", so one cluster's settings must not follow the
 * user to the next.
 */

import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Server, X } from "lucide-react";
import * as api from "@/lib/api/client";

const NONE = "";

// Suggestions only; any namespace key Pegasus accepts can be typed.
const KEY_HINTS: Record<string, string[]> = {
  condor: ["requirements", "request_memory", "request_cpus", "request_disk", "+ProjectName", "priority"],
  pegasus: ["queue", "project", "runtime", "cores", "nodes", "memory", "glite.arguments", "auxillary.local"],
  env: ["PATH", "PYTHONPATH", "OMP_NUM_THREADS"],
  dagman: ["retry", "maxjobs", "post.scope"],
  globus: ["maxwalltime", "queue", "project"],
  selector: ["execution.site"],
};

type Row = api.SiteOverride;

const blankRow = (): Row => ({ site: "compute", namespace: "condor", key: "", value: "" });
const filled = (rows: Row[]) => rows.filter((r) => r.key.trim());
const sameRows = (a: Row[], b: Row[]) =>
  JSON.stringify(filled(a).map((r) => [r.site, r.namespace, r.key, r.value])) ===
  JSON.stringify(filled(b).map((r) => [r.site, r.namespace, r.key, r.value]));

const inputCls =
  "rounded-md border border-line bg-base px-2 py-1 font-mono text-xs text-fg outline-none focus:border-pegasus-400";

export function ComputeSiteCard() {
  const qc = useQueryClient();
  const { data, isLoading, error } = useQuery({
    queryKey: ["compute-site"],
    queryFn: () => api.getComputeSite(),
  });
  const [catalog, setCatalog] = useState<string>(NONE);
  const [env, setEnv] = useState<Record<string, string>>({});
  const [saved, setSaved] = useState(false);
  const [rows, setRows] = useState<Row[]>([]);
  const [showAdvanced, setShowAdvanced] = useState(false);

  const slotFor = (name: string) =>
    name || (data?.studio_pool_available ? "studio" : "none");

  // Start from what is stored; re-seed when the server's view changes.
  useEffect(() => {
    if (!data) return;
    setCatalog(data.catalog ?? NONE);
    setEnv(data.env);
    setRows(data.overrides);
    if (data.overrides.length) setShowAdvanced(true);
  }, [data]);

  const option = useMemo(
    () => data?.catalogs.find((c) => c.name === catalog),
    [data, catalog]
  );
  const needs = option?.needs ?? [];

  const save = useMutation({
    mutationFn: () =>
      api.updateComputeSite(
        catalog || null,
        // Send every field the catalog uses, and clear the ones it doesn't,
        // so switching clusters leaves no stale account behind.
        Object.fromEntries(
          Array.from(new Set([...Object.keys(env), ...needs])).map((k) => [
            k,
            needs.includes(k) ? (env[k] ?? "") : "",
          ])
        ),
        filled(rows).map((r) => ({ ...r, key: r.key.trim(), value: r.value.trim() }))
      ),
    onSuccess: (fresh) => {
      qc.setQueryData(["compute-site"], fresh);
      // "Where it runs" is derived from ~/.pegasusrc.
      qc.invalidateQueries({ queryKey: ["project-params"] });
      setSaved(true);
    },
  });

  const dirty =
    !!data &&
    ((data.catalog ?? NONE) !== catalog ||
      needs.some((k) => (data.env[k] ?? "") !== (env[k] ?? "")) ||
      !sameRows(data.overrides, rows));
  const rowProblem = filled(rows).find((r) => !r.value.trim() || !r.site.trim());
  const missing = needs.filter((k) => !(env[k] ?? "").trim());

  // The cluster's own catalog when an administrator supplied one (#496).
  const defaultLabel = data?.default_label ?? "Studio’s own pool";
  const current = data?.catalog
    ? `${data.catalogs.find((c) => c.name === data.catalog)?.label ?? data.catalog} (site “compute”)`
    : `${defaultLabel} (site “${data?.site ?? "compute"}”)`;

  return (
    <section className="rounded-xl border border-line bg-surface p-4">
      <div className="flex items-start gap-3">
        <Server size={18} aria-hidden="true" className="mt-0.5 shrink-0 text-pegasus-600 dark:text-pegasus-300" />
        <div className="min-w-0 flex-1">
          <h2 className="text-sm font-semibold text-fg">Where your workflows run</h2>
          <p className="mt-0.5 text-xs text-fgmuted">
            {isLoading ? "Checking…" : <>Now: <span className="font-medium text-fg">{current}</span></>}
          </p>
        </div>
      </div>

      {error ? (
        <p className="mt-3 text-sm text-red-600 dark:text-rose-400">
          Couldn&apos;t load: {(error as Error).message}
        </p>
      ) : data ? (
        <form
          className="mt-4 space-y-3"
          onSubmit={(e) => {
            e.preventDefault();
            setSaved(false);
            save.mutate();
          }}
        >
          <label className="block text-xs font-medium text-fgmuted">
            Cluster
            <select
              value={catalog}
              onChange={(e) => {
                const next = e.target.value;
                setCatalog(next);
                // Show the settings kept for that cluster, not this one's.
                setRows(
                  next === (data.catalog ?? NONE)
                    ? data.overrides
                    : (data.saved_overrides[slotFor(next)] ?? [])
                );
                setSaved(false);
              }}
              className="mt-1 block w-full max-w-md rounded-md border border-line bg-base px-2 py-1.5 text-sm text-fg outline-none focus:border-pegasus-400"
            >
              <option value={NONE}>{defaultLabel} (no setup needed)</option>
              {data.catalogs.map((c) => (
                <option key={c.name} value={c.name}>
                  {c.label}
                </option>
              ))}
            </select>
            <span className="mt-1 block text-[11px] font-normal text-fgsubtle">
              Site catalogs published by the Pegasus team
              {data.pegasus_series ? ` for Pegasus ${data.pegasus_series}` : ""}.
              Pick the cluster you have an allocation on.
            </span>
          </label>

          {needs.map((k) => {
            const label = data.field_labels[k] ?? k;
            const accounts = k === "RESOURCE_PROJECT" ? data.slurm_accounts : [];
            return (
              <label key={k} className="block text-xs font-medium text-fgmuted">
                {label}
                {accounts.length > 0 ? (
                  <select
                    value={env[k] ?? ""}
                    onChange={(e) => {
                      setEnv((v) => ({ ...v, [k]: e.target.value }));
                      setSaved(false);
                    }}
                    className="mt-1 block w-full max-w-md rounded-md border border-line bg-base px-2 py-1.5 text-sm text-fg outline-none focus:border-pegasus-400"
                  >
                    <option value="">Choose an account…</option>
                    {accounts.map((a) => (
                      <option key={a} value={a}>
                        {a}
                      </option>
                    ))}
                  </select>
                ) : (
                  <input
                    value={env[k] ?? ""}
                    onChange={(e) => {
                      setEnv((v) => ({ ...v, [k]: e.target.value }));
                      setSaved(false);
                    }}
                    spellCheck={false}
                    autoComplete="off"
                    className="mt-1 block w-full max-w-md rounded-md border border-line bg-base px-2 py-1.5 font-mono text-sm text-fg outline-none focus:border-pegasus-400"
                  />
                )}
              </label>
            );
          })}

          {catalog && needs.length === 0 && (
            <p className="text-xs text-fgsubtle">
              This cluster needs no allocation details here: it uses your
              account as you are.
            </p>
          )}

          <div className="rounded-md border border-line">
            <button
              type="button"
              onClick={() => setShowAdvanced((v) => !v)}
              aria-expanded={showAdvanced}
              className="flex w-full items-center justify-between px-3 py-2 text-left text-xs font-medium text-fgmuted hover:text-fg"
            >
              <span>
                Advanced: site settings
                {filled(rows).length > 0 && ` (${filled(rows).length})`}
              </span>
              <span aria-hidden="true">{showAdvanced ? "−" : "+"}</span>
            </button>
            {showAdvanced && (
              <div className="space-y-2 border-t border-line px-3 py-3">
                <p className="text-[11px] text-fgsubtle">
                  Extra profiles applied on top of this cluster&apos;s catalog,
                  for example a <code className="font-mono">condor</code>{" "}
                  <code className="font-mono">requirements</code> expression or a
                  Slurm <code className="font-mono">pegasus</code>{" "}
                  <code className="font-mono">queue</code>. Yours win over the
                  catalog&apos;s; they are kept separately for each cluster.
                </p>
                <datalist id="site-override-sites">
                  <option value="compute" />
                  <option value="local" />
                  <option value="condorpool" />
                </datalist>
                {rows.map((r, i) => {
                  const set = (patch: Partial<Row>) => {
                    setRows((all) => all.map((x, j) => (j === i ? { ...x, ...patch } : x)));
                    setSaved(false);
                  };
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
                        {data.override_namespaces.map((n) => (
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
                        onClick={() => {
                          setRows((all) => all.filter((_, j) => j !== i));
                          setSaved(false);
                        }}
                        className="rounded p-1 text-fgsubtle hover:bg-muted hover:text-fg"
                      >
                        <X size={14} aria-hidden="true" />
                      </button>
                    </div>
                  );
                })}
                <button
                  type="button"
                  onClick={() => setRows((all) => [...all, blankRow()])}
                  className="inline-flex items-center gap-1 text-xs font-medium text-pegasus-700 hover:underline dark:text-pegasus-300"
                >
                  <Plus size={13} aria-hidden="true" /> Add setting
                </button>
                {data.effective_site && (
                  <details className="pt-1">
                    <summary className="cursor-pointer text-[11px] text-fgsubtle">
                      The “{data.site}” site as Pegasus sees it (as saved)
                    </summary>
                    <pre className="mt-1 max-h-64 overflow-auto rounded bg-muted p-2 font-mono text-[11px] text-fg">
                      {data.effective_site}
                    </pre>
                  </details>
                )}
              </div>
            )}
          </div>

          <div className="flex flex-wrap items-center gap-3 pt-1">
            <button
              type="submit"
              disabled={
                !dirty || save.isPending || !!rowProblem || (catalog !== NONE && missing.length > 0)
              }
              className="rounded-md bg-pegasus-600 px-3 py-1.5 text-sm font-semibold text-white hover:bg-pegasus-700 disabled:opacity-50"
            >
              {save.isPending ? "Saving…" : "Save"}
            </button>
            {catalog !== NONE && missing.length > 0 && (
              <span className="text-xs text-fgsubtle">
                Fill in {missing.map((k) => data.field_labels[k] ?? k).join(", ").toLowerCase()} to save.
              </span>
            )}
            {rowProblem && (
              <span className="text-xs text-fgsubtle">
                Give “{rowProblem.key}” a site and a value to save.
              </span>
            )}
            {saved && !dirty && (
              <span className="text-xs text-emerald-700 dark:text-emerald-400">
                Saved. {catalog
                  ? "Analyses now run on “compute” at this cluster."
                  : `Analyses run on ${defaultLabel}.`}
              </span>
            )}
            {save.isError && (
              <span className="text-xs text-red-600 dark:text-rose-400">
                Couldn&apos;t save: {(save.error as Error).message}
              </span>
            )}
          </div>
          <p className="text-[11px] text-fgsubtle">
            Stored in <code className="font-mono">~/.pegasusrc</code>, where
            Pegasus reads it for every workflow; anything else you keep there is
            left as it is.
          </p>
        </form>
      ) : null}
    </section>
  );
}
