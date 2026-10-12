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
 */

import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Server } from "lucide-react";
import * as api from "@/lib/api/client";

const NONE = "";

export function ComputeSiteCard() {
  const qc = useQueryClient();
  const { data, isLoading, error } = useQuery({
    queryKey: ["compute-site"],
    queryFn: () => api.getComputeSite(),
  });
  const [catalog, setCatalog] = useState<string>(NONE);
  const [env, setEnv] = useState<Record<string, string>>({});
  const [saved, setSaved] = useState(false);

  // Start from what is stored; re-seed when the server's view changes.
  useEffect(() => {
    if (!data) return;
    setCatalog(data.catalog ?? NONE);
    setEnv(data.env);
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
        )
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
      needs.some((k) => (data.env[k] ?? "") !== (env[k] ?? "")));
  const missing = needs.filter((k) => !(env[k] ?? "").trim());

  const current = data?.catalog
    ? `${data.catalogs.find((c) => c.name === data.catalog)?.label ?? data.catalog} (site “compute”)`
    : "Studio’s own pool (site “condorpool”)";

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
                setCatalog(e.target.value);
                setSaved(false);
              }}
              className="mt-1 block w-full max-w-md rounded-md border border-line bg-base px-2 py-1.5 text-sm text-fg outline-none focus:border-pegasus-400"
            >
              <option value={NONE}>Studio&apos;s own pool (no setup needed)</option>
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

          <div className="flex flex-wrap items-center gap-3 pt-1">
            <button
              type="submit"
              disabled={!dirty || save.isPending || (catalog !== NONE && missing.length > 0)}
              className="rounded-md bg-pegasus-600 px-3 py-1.5 text-sm font-semibold text-white hover:bg-pegasus-700 disabled:opacity-50"
            >
              {save.isPending ? "Saving…" : "Save"}
            </button>
            {catalog !== NONE && missing.length > 0 && (
              <span className="text-xs text-fgsubtle">
                Fill in {missing.map((k) => data.field_labels[k] ?? k).join(", ").toLowerCase()} to save.
              </span>
            )}
            {saved && !dirty && (
              <span className="text-xs text-emerald-700 dark:text-emerald-400">
                Saved. {catalog ? "Analyses now offer “compute” under Where it runs." : "Analyses run on Studio’s own pool."}
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
