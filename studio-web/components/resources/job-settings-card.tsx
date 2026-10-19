"use client";

/**
 * Job settings on the hosted deployment (#532), shown in the Resources
 * page's OSPool section because the field they exist for is the OSPool
 * project name. They also carry the advanced site profiles that used to
 * live inside "Where your workflows run" before that card became OOD-only.
 *
 * The hosted access point flocks to the OSPool, but the OSPool only takes
 * jobs that carry a project name (`+ProjectName = "USC_Deelman"`), so the
 * one field a hosted user is likely to need gets its own input. It is
 * stored as the same ~/.pegasusrc line the advanced editor would write:
 * pegasus.catalog.site.sites.compute.profiles.condor.+ProjectName.
 */

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import * as api from "@/lib/api/client";
import {
  SiteOverridesEditor,
  filled,
  sameRows,
  type Row,
} from "@/components/resources/site-overrides-editor";
import { CARD, FIELD, HINT, LABEL, SectionHeader } from "@/components/resources/status-card";

const PROJECT_ROW = { site: "compute", namespace: "condor", key: "+ProjectName" };
const isProjectRow = (r: Row) =>
  r.site === PROJECT_ROW.site && r.namespace === PROJECT_ROW.namespace && r.key === PROJECT_ROW.key;

/**
 * The value goes into the submit file verbatim as `+ProjectName = <value>`,
 * and there it is a ClassAd expression: unquoted, `USC_Deelman` is a
 * reference to an attribute of that name, which does not exist, so the
 * project name arrives at the access point as undefined and the OSPool
 * never sees it (#539). Store it as a quoted string; show it bare.
 */
const quoteProject = (bare: string) => `"${bare.trim().replace(/^"|"$/g, "").replace(/"/g, "")}"`;
const bareProject = (stored: string) => stored.trim().replace(/^"(.*)"$/, "$1");

export function JobSettingsCard() {
  const qc = useQueryClient();
  const { data, error } = useQuery({
    queryKey: ["compute-site"],
    queryFn: () => api.getComputeSite(),
  });
  const [rows, setRows] = useState<Row[]>([]);
  const [saved, setSaved] = useState(false);
  const [showAdvanced, setShowAdvanced] = useState(false);

  // Re-seed from the server only when the saved rows themselves change: the
  // status poll refreshes other fields every few seconds, and reseeding on
  // every response threw away whatever was being typed.
  const savedRows = data ? JSON.stringify(data.overrides) : null;
  useEffect(() => {
    if (!data) return;
    setRows(data.overrides);
    if (data.overrides.some((r) => !isProjectRow(r))) setShowAdvanced(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [savedRows]);

  const project = bareProject(rows.find(isProjectRow)?.value ?? "");
  const setProject = (value: string) => {
    setSaved(false);
    setRows((all) => {
      const rest = all.filter((r) => !isProjectRow(r));
      return value.trim() ? [{ ...PROJECT_ROW, value: quoteProject(value) }, ...rest] : rest;
    });
  };
  const others = rows.filter((r) => !isProjectRow(r));

  const save = useMutation({
    mutationFn: () =>
      api.updateComputeSite(
        data?.catalog ?? null,
        data?.env ?? {},
        filled(rows).map((r) => ({ ...r, key: r.key.trim(), value: r.value.trim() }))
      ),
    onSuccess: (fresh) => {
      qc.setQueryData(["compute-site"], fresh);
      qc.invalidateQueries({ queryKey: ["project-params"] });
      setSaved(true);
    },
  });

  const dirty = !!data && !sameRows(data.overrides, rows);
  const rowProblem = filled(rows).find((r) => !r.value.trim() || !r.site.trim());

  // A published catalog chosen before #532 is still the planner's "compute":
  // on this deployment that means scratch paths on a cluster the pool's
  // machines cannot see, and a run that fails with a missing directory. Say
  // so and offer the way back; never switch it silently.
  const staleCatalog = data?.catalog ?? null;
  const resetCatalog = useMutation({
    // No overrides sent: the rows on screen belong to the catalog being
    // left (a queue, a cluster's requirements), and the server keeps a set
    // per catalog, so omitting them restores whatever the pool had.
    mutationFn: () =>
      api.updateComputeSite(
        null,
        Object.fromEntries(Object.keys(data?.env ?? {}).map((k) => [k, ""]))
      ),
    onSuccess: (fresh) => {
      qc.setQueryData(["compute-site"], fresh);
      qc.invalidateQueries({ queryKey: ["project-params"] });
    },
  });

  return (
    <section>
      <SectionHeader
        title="Sending steps to the OSPool"
        subtitle="Applied to every step of every workflow you run from here."
      />
      <div className={`${CARD} p-4`}>

      {error ? (
        <p className="text-sm text-red-600 dark:text-rose-400">
          Couldn&apos;t load: {(error as Error).message}
        </p>
      ) : data ? (
        <form
          className="space-y-3"
          onSubmit={(e) => {
            e.preventDefault();
            setSaved(false);
            save.mutate();
          }}
        >
          {staleCatalog && (
            <div className="rounded-md border border-amber-300 bg-amber-50 p-3 text-xs text-amber-800 dark:border-amber-500/40 dark:bg-amber-500/10 dark:text-amber-200">
              <p>
                Your workflows are still set to run on the{" "}
                <span className="font-mono">{data.catalogs[0]?.label ?? staleCatalog}</span>{" "}
                site catalog, chosen in an earlier version of this page. Studio&apos;s
                pool and your annex cannot see that cluster&apos;s directories, so
                steps there fail with a missing directory.
              </p>
              <button
                type="button"
                disabled={resetCatalog.isPending}
                onClick={() => resetCatalog.mutate()}
                className="mt-2 rounded-md border border-amber-400 px-2 py-1 text-xs font-medium hover:bg-amber-100 disabled:opacity-50 dark:hover:bg-amber-500/20"
              >
                {resetCatalog.isPending ? "Switching…" : "Use Studio's pool instead"}
              </button>
              {resetCatalog.isError && (
                <p className="mt-1 text-red-600 dark:text-rose-400">
                  Couldn&apos;t switch: {(resetCatalog.error as Error).message}
                </p>
              )}
            </div>
          )}
          <label className={LABEL}>
            OSPool project name
            <input
              value={project}
              onChange={(e) => setProject(e.target.value)}
              placeholder="e.g. USC_Deelman"
              spellCheck={false}
              autoComplete="off"
              className={`${FIELD} max-w-md`}
            />
            <span className={`mt-1 block font-normal ${HINT}`}>
              Studio&apos;s pool can send your jobs on to the{" "}
              <a
                href="https://portal.osg-htc.org/documentation/"
                target="_blank"
                rel="noreferrer"
                className="underline"
              >
                OSPool
              </a>{" "}
              when it is busy, but the OSPool only accepts jobs that name a
              project it knows. Leave this empty to keep your jobs on Studio&apos;s
              pool and your annex.
            </span>
          </label>

          <div className="rounded-md border border-line">
            <button
              type="button"
              onClick={() => setShowAdvanced((v) => !v)}
              aria-expanded={showAdvanced}
              className="flex w-full items-center justify-between px-3 py-2 text-left text-xs font-medium text-fgmuted hover:text-fg"
            >
              <span>
                Advanced: site settings
                {filled(others).length > 0 && ` (${filled(others).length})`}
              </span>
              <span aria-hidden="true">{showAdvanced ? "−" : "+"}</span>
            </button>
            {showAdvanced && (
              <div className="border-t border-line px-3 py-3">
                <SiteOverridesEditor
                  rows={others}
                  onChange={(next) => {
                    setSaved(false);
                    // A project row typed here is the project field, not a
                    // hidden duplicate: the last one wins, as it would in
                    // ~/.pegasusrc.
                    const typed = next.filter(isProjectRow);
                    setRows((all) => [
                      ...(typed.length ? [typed[typed.length - 1]] : all.filter(isProjectRow)),
                      ...next.filter((r) => !isProjectRow(r)),
                    ]);
                  }}
                  namespaces={data.override_namespaces}
                  site={data.site}
                  effectiveSite={data.effective_site}
                  intro={
                    <>
                      Extra profiles on the “{data.site}” site, for example a{" "}
                      <code className="font-mono">condor</code>{" "}
                      <code className="font-mono">requirements</code> expression
                      or <code className="font-mono">request_memory</code>.
                    </>
                  }
                />
              </div>
            )}
          </div>

          <div className="flex flex-wrap items-center gap-3 pt-1">
            <button
              type="submit"
              disabled={!dirty || save.isPending || !!rowProblem}
              className="rounded-md bg-pegasus-600 px-3 py-1.5 text-sm font-semibold text-white hover:bg-pegasus-700 disabled:opacity-50"
            >
              {save.isPending ? "Saving…" : "Save"}
            </button>
            {rowProblem && (
              <span className="text-xs text-fgsubtle">
                Give “{rowProblem.key}” a site and a value to save.
              </span>
            )}
            {saved && !dirty && (
              <span className="text-xs text-emerald-700 dark:text-emerald-400">
                Saved. Applies to workflows planned from now on.
              </span>
            )}
            {save.isError && (
              <span className="text-xs text-red-600 dark:text-rose-400">
                Couldn&apos;t save: {(save.error as Error).message}
              </span>
            )}
          </div>
          <p className={HINT}>
            Stored in <code className="font-mono">~/.pegasusrc</code>, where
            Pegasus reads it for every workflow.
          </p>
        </form>
      ) : null}
      </div>
    </section>
  );
}
