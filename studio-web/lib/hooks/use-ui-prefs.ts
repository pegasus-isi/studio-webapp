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
 * One PUT at a time, app-wide. Module-level so every component's hook instance
 * shares it.
 *
 * Merging on the client fixes what each request *contains*, not the order the
 * server *applies* them in: two PUTs in flight can arrive reversed, and then
 * the older body (missing the newer toggle) is what gets stored. Chaining the
 * requests removes the concurrency — each waits for the previous to settle,
 * and by then the cache already holds every patch made in the meantime, so
 * the body it sends is the merged result of all of them. Whether the previous
 * request failed or not does not matter to the next one.
 */
let inFlight: Promise<unknown> = Promise.resolve();

/**
 * Saves requested and not yet settled.
 *
 * Counted from onMutate to onSettled — the whole life of the intent — rather
 * than from mutationFn: TanStack pauses a mutation while the browser is
 * offline *before* calling mutationFn, so a count that starts there misses
 * paused saves, and a resync or another save's response would then overwrite
 * their optimistic patch as if nothing were pending.
 *
 * Kept by hand rather than read from `isMutating`, which still reports a
 * mutation as pending while its own callbacks run and for a few ticks after,
 * so a check made from those callbacks cannot tell "me" from "someone else".
 * Here the accounting is explicit: inside onSuccess this save is still
 * counted (its onSettled has not run yet); inside anything scheduled from
 * onSettled, after the decrement, it is not.
 */
let queued = 0;

/**
 * Save a change to one or more preferences.
 *
 * Takes a *partial* and merges it into the latest cached preferences when the
 * request is actually sent, rather than having callers spread the object they
 * rendered with. The endpoint is a wholesale PUT, so a caller sending
 * `{ ...prefs, x }` from a render that predates another toggle's response
 * would put that other toggle back the way it was — easy to hit with two
 * checkboxes on one Settings tab.
 *
 * The cache is updated optimistically so a second toggle merges on top of the
 * first before the first has round-tripped, and a response only overwrites
 * the cache when nothing newer is queued; an earlier response landing while a
 * later patch is still pending would otherwise undo that patch.
 */
export function useUpdateUIPreferences() {
  const qc = useQueryClient();
  return useMutation({
    mutationKey: MUTATION_KEY,
    mutationFn: (patch: Partial<api.UIPreferences>) => {
      const send = () => {
        // Read the cache *now*, not when mutate() was called: it has absorbed
        // every optimistic patch queued behind the previous request. The patch
        // is re-applied on top in case a resync reset the cache meanwhile.
        const current = qc.getQueryData<api.UIPreferences>(KEY) ?? FALLBACK;
        return api.updateUIPreferences({ ...current, ...patch });
      };
      const request = inFlight.then(send, send);
      inFlight = request.catch(() => undefined);
      return request;
    },
    onMutate: (patch) => {
      queued += 1;
      const current = qc.getQueryData<api.UIPreferences>(KEY) ?? FALLBACK;
      qc.setQueryData<api.UIPreferences>(KEY, { ...current, ...patch });
    },
    onSuccess: (saved) => {
      // Seed the cache from the response rather than invalidating, so the
      // toggle doesn't visibly bounce while a refetch is in flight — but only
      // if nothing newer is queued (this save is still counted here), or this
      // older response would clobber the newer optimistic state. The last
      // response carries the merged result of everything before it.
      if (queued <= 1) {
        qc.setQueryData(KEY, saved);
      }
    },
    onSettled: (_saved, error) => {
      queued -= 1;
      if (!error) return;
      // The optimistic value may be wrong now, but a plain invalidate would
      // refetch outside the queue: a save started after the GET was issued
      // could succeed first, and the stale GET landing afterwards would
      // overwrite it. So the resync is itself a step in the chain — every
      // later save waits for it — and it gives up if any save is queued when
      // it runs or when the GET returns, since that save's response is the
      // fresher truth. Queued saves read the cache when they send, so the
      // failed patch travels with them as a retry of what the user asked for.
      const resync = async () => {
        if (queued > 0) return;
        const fresh = await api.getUIPreferences();
        if (queued === 0) qc.setQueryData(KEY, fresh);
      };
      inFlight = inFlight.then(resync, resync).catch(() => undefined);
    },
  });
}
