"use client";

/**
 * "Send runs to your annex only" (#558): the pin the first live annex test
 * needed, as a switch rather than a ClassAd typed into Advanced site
 * settings. The rows it writes are lib/annex-pin.ts's, saved through the
 * same compute-site API as Job settings, so both views stay in step.
 */

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import * as api from "@/lib/api/client";
import { applyPin, pinState, validRuntimeDir } from "@/lib/annex-pin";
import { CARD, FIELD, HINT, LABEL } from "@/components/resources/status-card";
import { cn } from "@/lib/utils";

export function AnnexOnlyCard({ name, established }: { name: string; established: boolean }) {
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["compute-site"], queryFn: () => api.getComputeSite() });
  const site = data?.site ?? "compute";
  const state = pinState(data?.overrides ?? [], site, name);
  const [dir, setDir] = useState("");

  // Seed the field from what is saved; do not fight the user while typing.
  useEffect(() => {
    setDir(state.runtimeDir);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.runtimeDir]);

  const save = useMutation({
    mutationFn: (next: { on: boolean; dir: string }) =>
      api.updateComputeSite(
        data?.catalog ?? null,
        data?.env ?? {},
        applyPin(data?.overrides ?? [], site, name, next.on, next.dir)
      ),
    onSuccess: (fresh) => {
      qc.setQueryData(["compute-site"], fresh);
      qc.invalidateQueries({ queryKey: ["project-params"] });
    },
  });

  if (!data) return null;
  const dirProblem = !!dir.trim() && !validRuntimeDir(dir);
  const dirDirty = state.on && dir.trim().replace(/\/+$/, "") !== state.runtimeDir;

  return (
    <div
      className={cn(
        CARD,
        "p-4",
        state.on && !established && "border-amber-300 dark:border-amber-500/40"
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm font-semibold text-fg">Send runs to your annex only</p>
          <p className="mt-0.5 text-xs text-fgmuted">
            {state.on
              ? established
                ? "On. Runs planned from now go only to your annex's nodes."
                : "On, and no annex is up: runs planned from now wait until your nodes join. Turn it off when you are done."
              : "Off. Steps go to whichever is free first, Studio's pool or your annex, so an idle pool can finish a run before your nodes arrive."}
          </p>
        </div>
        <button
          type="button"
          role="switch"
          aria-checked={state.on}
          aria-label="Send runs to your annex only"
          disabled={save.isPending || !!state.foreign}
          onClick={() => save.mutate({ on: !state.on, dir })}
          className={cn(
            "relative mt-0.5 h-6 w-11 shrink-0 rounded-full transition-colors disabled:opacity-50",
            state.on ? "bg-pegasus-600" : "bg-muted ring-1 ring-inset ring-line"
          )}
        >
          <span
            aria-hidden="true"
            className={cn(
              "absolute top-0.5 h-5 w-5 rounded-full bg-white shadow transition-all",
              state.on ? "left-[1.375rem]" : "left-0.5"
            )}
          />
        </button>
      </div>

      {state.foreign && (
        <p className="mt-2 text-xs text-amber-700 dark:text-amber-400">
          You have your own <code className="font-mono">requirements</code> setting
          (<code className="font-mono">{state.foreign}</code>) under OSPool → Advanced, so
          this switch stays off rather than replace it.
        </p>
      )}

      {state.on && (
        <div className="mt-3 border-t border-line pt-3">
          <label className={LABEL}>
            Container runtime folder on the cluster{" "}
            <span className="font-normal text-fgsubtle">(only for workflows that run in containers)</span>
            <input
              value={dir}
              onChange={(e) => setDir(e.target.value)}
              placeholder="e.g. /cm/local/apps/singularitypro/3.11/bin"
              spellCheck={false}
              autoComplete="off"
              className={cn(FIELD, "max-w-xl font-mono")}
            />
          </label>
          <p className={cn("mt-1", HINT)}>
            Jobs on annex nodes do not see the cluster&apos;s modules. On the cluster, run{" "}
            <code className="font-mono">module load &lt;apptainer or singularity module&gt; &amp;&amp; which singularity</code>{" "}
            and paste the folder it prints.
          </p>
          {dirProblem && (
            <p className="mt-1 text-xs text-amber-700 dark:text-amber-400">
              That is not an absolute folder path; it will not be saved.
            </p>
          )}
          {dirDirty && !dirProblem && (
            <button
              type="button"
              disabled={save.isPending}
              onClick={() => save.mutate({ on: true, dir })}
              className="mt-2 rounded-md bg-pegasus-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-pegasus-700 disabled:opacity-50"
            >
              {save.isPending ? "Saving…" : "Save folder"}
            </button>
          )}
        </div>
      )}

      {save.isError && (
        <p className="mt-2 text-xs text-red-600 dark:text-rose-400">
          Couldn&apos;t save: {(save.error as Error).message}
        </p>
      )}
    </div>
  );
}
