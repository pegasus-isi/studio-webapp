"use client";

import { useQuery } from "@tanstack/react-query";
import * as api from "@/lib/api/client";

export function useDashboard() {
  return useQuery({
    queryKey: ["dashboard"],
    queryFn: () => api.getDashboard(),
    refetchInterval: 10_000,
  });
}

export function useProvisioningStatus() {
  return useQuery({
    queryKey: ["provisioning"],
    queryFn: () => api.getProvisioningStatus(),
    refetchInterval: 10_000,
  });
}
