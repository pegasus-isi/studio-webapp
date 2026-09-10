"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import * as api from "@/lib/api/client";
import { applyExecutionBackend, executionBackendKey, prepareBackendChange } from "@/lib/execution-backend-cache";

/** Shared selection for Settings, workflow forms and the terminal menu. */
export function useExecutionBackend() {
  const qc = useQueryClient();
  const query = useQuery({
    queryKey: executionBackendKey,
    queryFn: async ({ signal }) => {
      const status = await api.getExecutionBackend();
      await prepareBackendChange(qc, status, signal);
      return status;
    },
    staleTime: 5_000,
    refetchInterval: 10_000,
    refetchOnWindowFocus: true,
  });
  const backend = query.data?.backends.find((b) => b.id === query.data.backend) ?? null;
  return { ...query, backend, isRemote: backend !== null && backend.id !== "local" };
}

export function useUpdateExecutionBackend() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.updateExecutionBackend,
    onSuccess: (status) => applyExecutionBackend(qc, status),
  });
}
