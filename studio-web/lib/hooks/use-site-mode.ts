"use client";

import { useQuery } from "@tanstack/react-query";
import * as api from "@/lib/api/client";

/**
 * Hackathon kiosk flag (STUDIO_HACKATHON_MODE): long cache, it only changes
 * across restarts. Defaults to false so an unreachable API degrades to the
 * full product rather than a half-hidden one.
 */
export function useHackathonMode() {
  const query = useQuery({
    queryKey: ["site-mode"],
    queryFn: () => api.getSiteMode(),
    staleTime: 30 * 60 * 1000,
    refetchOnWindowFocus: false,
  });
  return query.data?.hackathon_mode ?? false;
}
