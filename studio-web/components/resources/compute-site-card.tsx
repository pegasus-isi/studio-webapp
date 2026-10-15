"use client";

/**
 * "Where your workflows run": the cluster this install sits on, and the
 * user's allocation there (#468, #532).
 *
 * Shown on the Open OnDemand app only. There the execution site "compute"
 * is the administrator's catalog (STUDIO_SITE_CATALOG_FILE or a published
 * one named by STUDIO_SITE_CATALOG in site.env), so the cluster is a fact
 * of the install, not a choice: the card names it and asks for the
 * allocation fields that catalog references (RESOURCE_*), the ACCESS setup
 * done from a form instead of a notebook cell. Unity asks for nothing and
 * NERSC asks for three.
 *
 * Until #532 this card also offered every published catalog to pick from.
 * Those describe "compute" as a BLAHP site on the cluster's own access
 * point, which only an install on that cluster can use; on the hosted
 * deployment the way to a cluster is an annex (see annex-panel.tsx), so the
 * picker had no deployment left to serve.
 *
 * "Advanced: site settings" (#478) adds profiles on top of the catalog, a
 * Slurm queue say, as Pegasus' own pegasus.catalog.site.sites.* properties,
 * so the published catalog stays untouched and the planner merges them in.
 */

import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Server } from "lucide-react";
import * as api from "@/lib/api/client";
import {
  SiteOverridesEditor,
  filled,
  sameRows,
  type Row,
} from "@/components/resources/site-overrides-editor";

export function ComputeSiteCard() {
  const qc = useQueryClient();
  const { data, isLoading, error } = useQuery({
    queryKey: ["compute-site"],
    queryFn: () => api.getComputeSite(),
  });
  const [env, setEnv] = useState<Record<string, string>>({});
  const [saved, setSaved] = useState(false);
  const [rows, setRows] = useState<Row[]>([]);
  const [showAdvanced, setShowAdvanced] = useState(false);

  // Start from what is stored; re-seed only when the stored values change,
  // not on every refetch, or a refresh would throw away what is being typed.
  const savedValues = data ? JSON.stringify([data.env, data.overrides]) : null;
  useEffect(() => {
    if (!data) return;
    setEnv(data.env);
    setRows(data.overrides);
    if (data.overrides.length) setShowAdvanced(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [savedValues]);

  const option = useMemo(
    () => data?.catalogs.find((c) => c.name === data.catalog),
    [data]
  );
  const needs = option?.needs ?? [];

  const save = useMutation({
    mutationFn: () =>
      api.updateComputeSite(
        data?.catalog ?? null,
        // Send every field the catalog uses, and clear the ones it doesn't.
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
    (needs.some((k) => (data.env[k] ?? "") !== (env[k] ?? "")) ||
      !sameRows(data.overrides, rows));
  const rowProblem = filled(rows).find((r) => !r.value.trim() || !r.site.trim());
  const missing = needs.filter((k) => !(env[k] ?? "").trim());

  // The cluster's own catalog when an administrator supplied one (#496),
  // else the published catalog the entrypoint wrote into ~/.pegasusrc.
  const clusterLabel = data?.catalog
    ? option?.label ?? data.catalog
    : data?.default_label ?? "This cluster";
  const current = `${clusterLabel} (site “${data?.site ?? "compute"}”)`;
  const stillNeeds = (data?.missing ?? []).map((m) => m.toLowerCase()).join(", ");

  return (
    <section className="rounded-xl border border-line bg-surface p-4">
      <div className="flex items-start gap-3">
        <Server size={18} aria-hidden="true" className="mt-0.5 shrink-0 text-pegasus-600 dark:text-pegasus-300" />
        <div className="min-w-0 flex-1">
          <h2 className="text-sm font-semibold text-fg">Where your workflows run</h2>
          <p className="mt-0.5 text-xs text-fgmuted">
            {isLoading ? "Checking…" : <>Now: <span className="font-medium text-fg">{current}</span></>}
          </p>
          {data && !data.configured && (
            <p className="mt-1 text-xs text-amber-700 dark:text-amber-400">
              Not ready yet: still needs {stillNeeds}. Workflows cannot run
              there until it is filled in below.
            </p>
          )}
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
          <p className="text-xs text-fgsubtle">
            This cluster is where this copy of Studio runs; its scheduler is
            described by a site catalog your administrator set up
            {data.pegasus_series ? ` for Pegasus ${data.pegasus_series}` : ""}.
          </p>

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

          {needs.length === 0 && (
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
              <div className="border-t border-line px-3 py-3">
                <SiteOverridesEditor
                  rows={rows}
                  onChange={(next) => {
                    setRows(next);
                    setSaved(false);
                  }}
                  namespaces={data.override_namespaces}
                  site={data.site}
                  effectiveSite={data.effective_site}
                  intro={
                    <>
                      Extra profiles applied on top of this cluster&apos;s catalog,
                      for example a Slurm <code className="font-mono">pegasus</code>{" "}
                      <code className="font-mono">queue</code> or a{" "}
                      <code className="font-mono">condor</code>{" "}
                      <code className="font-mono">requirements</code> expression.
                      Yours win over the catalog&apos;s.
                    </>
                  }
                />
              </div>
            )}
          </div>

          <div className="flex flex-wrap items-center gap-3 pt-1">
            <button
              type="submit"
              disabled={!dirty || save.isPending || !!rowProblem || missing.length > 0}
              className="rounded-md bg-pegasus-600 px-3 py-1.5 text-sm font-semibold text-white hover:bg-pegasus-700 disabled:opacity-50"
            >
              {save.isPending ? "Saving…" : "Save"}
            </button>
            {missing.length > 0 && (
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
                Saved. Analyses run on “{data.site}” at {clusterLabel}.
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
