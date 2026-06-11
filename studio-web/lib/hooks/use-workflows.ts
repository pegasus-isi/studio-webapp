"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import * as api from "@/lib/api/client";

export function useWorkflows() {
  return useQuery({
    queryKey: ["workflows"],
    queryFn: () => api.getWorkflows(),
    refetchInterval: 5_000,
  });
}

export function useWorkflowProjects() {
  return useQuery({
    queryKey: ["workflow-projects"],
    queryFn: () => api.getWorkflowProjects(),
    refetchInterval: 5_000,
  });
}

export function useWorkflowJobs(runId: string) {
  return useQuery({
    queryKey: ["workflows", runId, "jobs"],
    queryFn: () => api.getWorkflowJobs(runId),
    enabled: !!runId,
    refetchInterval: 5_000,
  });
}

export function useAnalyzeWorkflow() {
  return useMutation({
    mutationFn: (runId: string) => api.analyzeWorkflow(runId),
  });
}

export function useDeleteWorkflow() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (runId: string) => api.deleteWorkflow(runId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["workflows"] }),
  });
}

export function useProjectParams(projectId: string) {
  return useQuery({
    queryKey: ["workflow-projects", projectId, "params"],
    queryFn: () => api.getProjectParams(projectId),
    enabled: !!projectId,
  });
}

export function useGenerateWorkflow() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ projectId, args }: { projectId: string; args: string[] }) =>
      api.generateWorkflow(projectId, args),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["workflow-projects"] }),
  });
}

export function usePlanWorkflow() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      projectId,
      opts,
    }: {
      projectId: string;
      opts: api.PlanOptions;
    }) => api.planWorkflow(projectId, opts),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["workflow-projects"] }),
  });
}

export function useSubmitWorkflowProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      projectId,
      opts,
    }: {
      projectId: string;
      opts: api.PlanOptions;
    }) => api.submitWorkflowProject(projectId, opts),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["workflow-projects"] });
      qc.invalidateQueries({ queryKey: ["workflows"] });
    },
  });
}
