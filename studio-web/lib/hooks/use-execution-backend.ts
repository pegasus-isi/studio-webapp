"use client";

import { useQuery } from "@tanstack/react-query";
import * as api from "@/lib/api/client";
import type { ExecutionBackendInfo } from "@/lib/api/client";

/**
 * The compute backend workflow actions currently execute on.
 *
 * Sourced from /api/settings/execution-backend. A short staleTime keeps the
 * run panel honest without polling: backends are switched on the Settings
 * page, so the worst case is a 30s-old answer after switching there and
 * coming straight back. Returns null until loaded — callers must treat null
 * as "local", matching the server, where an unknown id falls back to local
 * rather than failing workflow calls.
 */
export function useExecutionBackend(): {
  backend: ExecutionBackendInfo | null;
  isRemote: boolean;
} {
  const { data } = useQuery({
    queryKey: ["execution-backend"],
    queryFn: () => api.getExecutionBackend(),
    staleTime: 30_000,
    refetchOnWindowFocus: false,
  });
  const backend = data?.backends.find((b) => b.id === data.backend) ?? null;
  // Same convention as the run-row badges: anything but "local" is remote.
  const isRemote = backend !== null && backend.id !== "local";
  return { backend, isRemote };
}
