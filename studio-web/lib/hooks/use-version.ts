"use client";

import { useQuery } from "@tanstack/react-query";
import * as api from "@/lib/api/client";

/**
 * Studio release string for the sidebar, e.g. "v0.1.0 alpha".
 *
 * Sourced from /api/health (a constant-time endpoint — no subprocesses or
 * workflow scans, unlike /api/dashboard) and never refetched: the version only
 * changes when the container is replaced, which reloads the page anyway.
 * Returns null until loaded, or if an older backend omits the field.
 */
export function useStudioVersion(): string | null {
  const { data } = useQuery({
    queryKey: ["health"],
    queryFn: () => api.getHealth(),
    staleTime: Infinity,
    gcTime: Infinity,
    refetchOnWindowFocus: false,
  });
  if (!data?.version) return null;
  return `v${data.version}${data.channel ? ` ${data.channel}` : ""}`;
}
