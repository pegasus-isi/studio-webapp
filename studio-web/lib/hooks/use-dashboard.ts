"use client";

import { useQuery } from "@tanstack/react-query";
import * as api from "@/lib/api/client";
import { useExecutionBackend } from "./use-execution-backend";

export function useDashboard() {
  const { data: selection } = useExecutionBackend();
  return useQuery({
    queryKey: ["dashboard", selection?.backend],
    enabled: !!selection,
    queryFn: () => api.getDashboard(),
    refetchInterval: 10_000,
  });
}

export function useProvisioningStatus() {
  const { data: selection } = useExecutionBackend();
  return useQuery({
    queryKey: ["provisioning", selection?.backend],
    enabled: !!selection,
    queryFn: () => api.getProvisioningStatus(),
    refetchInterval: 10_000,
  });
}
