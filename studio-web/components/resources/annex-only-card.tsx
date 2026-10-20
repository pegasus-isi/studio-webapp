"use client";

/**
 * "Send runs to your annex only" (#558), shown inside step 1 of the annex
 * checklist since #564: it only means anything while an annex is being set
 * up or used, and as a card of its own it read as a standing setting that
 * was easy to leave on. The rows it writes are lib/annex-pin.ts's, saved
 * through the same compute-site API as Job settings.
 *
 * The container runtime folder that used to sit here is gone: it was one
 * Studio-wide PATH for a value that differs per cluster. The cluster's
 * module now goes into the cluster command (lib/annex-command.ts). Turning
 * the switch off still removes a PATH row the old version wrote.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import * as api from "@/lib/api/client";
import { applyPin, pinState } from "@/lib/annex-pin";
import { cn } from "@/lib/utils";

export function useAnnexPin(name: string) {
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["compute-site"], queryFn: () => api.getComputeSite() });
  const site = data?.site ?? "compute";
  const state = pinState(data?.overrides ?? [], site, name);
  const save = useMutation({
    mutationFn: (on: boolean) =>
      api.updateComputeSite(data?.catalog ?? null, data?.env ?? {}, applyPin(data?.overrides ?? [], site, name, on)),
    onSuccess: (fresh) => {
      qc.setQueryData(["compute-site"], fresh);
      qc.invalidateQueries({ queryKey: ["project-params"] });
    },
  });
  return { ready: !!data, ...state, set: save.mutate, saving: save.isPending, error: save.error as Error | null };
}

export function AnnexOnlySwitch({ name, established }: { name: string; established: boolean }) {
  const pin = useAnnexPin(name);
  if (!pin.ready) return null;
  return (
    <div
      className={cn(
        "mt-2 rounded-lg border border-line bg-base p-3",
        pin.on && !established && "border-amber-300 dark:border-amber-500/40"
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm font-medium text-fg">Send runs to your annex only</p>
          <p className="mt-0.5 text-xs text-fgmuted">
            {pin.on
              ? established
                ? "On. Runs planned from now go only to your annex's nodes."
                : "On: runs planned from now wait for your nodes. It turns off when you shut the annex down."
              : "Off. Steps go to whichever is free first, Studio's pool or your annex, so an idle pool can finish a run before your nodes arrive."}
          </p>
        </div>
        <button
          type="button"
          role="switch"
          aria-checked={pin.on}
          aria-label="Send runs to your annex only"
          disabled={pin.saving || !!pin.foreign}
          onClick={() => pin.set(!pin.on)}
          className={cn(
            "relative mt-0.5 h-6 w-11 shrink-0 rounded-full transition-colors disabled:opacity-50",
            pin.on ? "bg-pegasus-600" : "bg-muted ring-1 ring-inset ring-line"
          )}
        >
          <span
            aria-hidden="true"
            className={cn(
              "absolute top-0.5 h-5 w-5 rounded-full bg-white shadow transition-all",
              pin.on ? "left-[1.375rem]" : "left-0.5"
            )}
          />
        </button>
      </div>
      {pin.foreign && (
        <p className="mt-2 text-xs text-amber-700 dark:text-amber-400">
          You have your own <code className="font-mono">requirements</code> setting
          (<code className="font-mono">{pin.foreign}</code>) under OSPool → Advanced, so
          this switch stays off rather than replace it.
        </p>
      )}
      {pin.error && (
        <p className="mt-2 text-xs text-red-600 dark:text-rose-400">Couldn&apos;t save: {pin.error.message}</p>
      )}
    </div>
  );
}
