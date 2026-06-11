"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import * as api from "@/lib/api/client";

export function useTools() {
  return useQuery({
    queryKey: ["tools"],
    queryFn: () => api.getTools(),
    refetchInterval: 10_000,
  });
}

export function useTool(id: string) {
  return useQuery({
    queryKey: ["tools", id],
    queryFn: () => api.getTool(id),
  });
}

export function useInstallTool() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.installTool(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["tools"] }),
  });
}

export function useUninstallTool() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.uninstallTool(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["tools"] }),
  });
}

export function useStartTool() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.startTool(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["tools"] }),
  });
}

export function useStopTool() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.stopTool(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["tools"] }),
  });
}
