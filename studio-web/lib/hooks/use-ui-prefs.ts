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
  // Matches the server default (#241). It has to: this value is what renders
  // between mount and the fetch landing, so a mismatch shows every user the
  // other layout for a beat and then swaps it under them.
  //
  // It is also what a *failed* fetch falls back to. Defaulting to the flow the
  // product now leads with is the right failure mode — someone whose
  // preference request failed should see the standard product, and can still
  // reach the individual steps through Advanced.
  simple_run: true,
  advanced_open: false,
  // Assume dismissed until we know otherwise: showing a first-run welcome to
  // a returning user because a fetch was slow is worse than showing it a
  // moment late to someone genuinely new.
  welcome_dismissed: true,
  nav_advanced_open: false,
  // Only read by the checkbox on the welcome page and in Settings; the
  // redirect itself is decided server-side from the stored value.
  skip_welcome: false,
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

const MUTATION_KEY = ["ui-preferences", "update"];

/**
 * Save a change to one or more preferences.
 *
 * Takes a *partial* and merges it into the latest cached preferences at call
 * time, rather than having callers spread the object they rendered with. The
 * endpoint is a wholesale PUT, so a caller sending `{ ...prefs, x }` from a
 * render that predates another toggle's response would put that other toggle
 * back the way it was — easy to hit with two checkboxes on one Settings tab.
 *
 * The cache is updated optimistically so a second toggle merges on top of the
 * first before the first has round-tripped, and a response only overwrites
 * the cache when it is the last request in flight; an earlier response landing
 * after a later optimistic update would otherwise undo it.
 */
export function useUpdateUIPreferences() {
  const qc = useQueryClient();
  return useMutation({
    mutationKey: MUTATION_KEY,
    mutationFn: (patch: Partial<api.UIPreferences>) => {
      const current = qc.getQueryData<api.UIPreferences>(KEY) ?? FALLBACK;
      return api.updateUIPreferences({ ...current, ...patch });
    },
    onMutate: (patch) => {
      const current = qc.getQueryData<api.UIPreferences>(KEY) ?? FALLBACK;
      qc.setQueryData<api.UIPreferences>(KEY, { ...current, ...patch });
    },
    onSuccess: (saved) => {
      // Seed the cache from the response rather than invalidating, so the
      // toggle doesn't visibly bounce while a refetch is in flight — but only
      // if nothing newer is pending, or this (older) response would clobber
      // the newer optimistic state. The last response in flight carries the
      // merged result of everything before it.
      if (qc.isMutating({ mutationKey: MUTATION_KEY }) <= 1) {
        qc.setQueryData(KEY, saved);
      }
    },
    onError: () => {
      // The optimistic value may be wrong now; fetch what the server holds.
      void qc.invalidateQueries({ queryKey: KEY });
    },
  });
}
