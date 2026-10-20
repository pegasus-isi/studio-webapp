"use client";

/**
 * The standing "send runs to your annex only" row in ~/.pegasusrc, which
 * the Resources page used to write (#558, #564). It applied to every later
 * run and was easy to leave on, so runs waited for nodes that were not
 * coming. Since #572 the Run panel chooses the machines per run, and this
 * hook remains only to notice the old row and remove it (along with a PATH
 * row the first version wrote), via lib/annex-pin.ts.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import * as api from "@/lib/api/client";
import { applyPin, pinState } from "@/lib/annex-pin";

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
