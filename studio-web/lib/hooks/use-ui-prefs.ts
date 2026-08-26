/**
 * Per-user UI preferences — the Phase 1 rollout flag and the Advanced
 * disclosure state (#241).
 *
 * Server-backed rather than localStorage so a preference follows the user
 * rather than the browser: someone who opens Studio on a laptop and then a lab
 * machine must not silently get two different products, and a staged rollout is
 * uninterpretable if the flag means different things per device.
 */

"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import * as api from "@/lib/api/client";

const KEY = ["ui-preferences"];

/** Defaults used before the fetch lands, and if the request fails entirely. */
const FALLBACK: api.UIPreferences = {
  // Off by default — see routers/settings.py. The unified Run flow is opt-in
  // until validation, and a failed fetch must land on the *current* UI rather
  // than flipping users into the new one by accident.
  simple_run: false,
  advanced_open: false,
};

export function useUIPreferences() {
  const query = useQuery({
    queryKey: KEY,
    queryFn: () => api.getUIPreferences(),
    // Preferences change rarely and only from this app, so don't refetch on
    // every window focus — a mid-run refetch that flipped the layout would be
    // startling.
    staleTime: 5 * 60 * 1000,
    refetchOnWindowFocus: false,
  });

  return {
    prefs: query.data ?? FALLBACK,
    isLoading: query.isLoading,
  };
}

export function useUpdateUIPreferences() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (prefs: api.UIPreferences) => api.updateUIPreferences(prefs),
    onSuccess: (saved) => {
      // Seed the cache from the response rather than invalidating, so the
      // toggle doesn't visibly bounce while a refetch is in flight.
      qc.setQueryData(KEY, saved);
    },
  });
}
