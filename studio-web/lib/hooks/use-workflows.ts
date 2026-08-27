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

export function useWorkflowJobs(runId: string, active = true) {
  // A terminal run's jobs never change, so pass active=false to fetch once and
  // stop polling (FE-9). The list hooks keep polling regardless — they must
  // still discover newly-submitted runs.
  return useQuery({
    queryKey: ["workflows", runId, "jobs"],
    queryFn: () => api.getWorkflowJobs(runId),
    enabled: !!runId,
    refetchInterval: active ? 5_000 : false,
  });
}

export function useAnalyzeWorkflow() {
  return useMutation({
    mutationFn: (runId: string) => api.analyzeWorkflow(runId),
  });
}

export function useWorkflowEstimate(runId: string, enabled = true) {
  return useQuery({
    queryKey: ["workflows", runId, "estimate"],
    queryFn: () => api.getWorkflowEstimate(runId),
    enabled: !!runId && enabled,
    refetchInterval: 15_000,
  });
}

export function useCancelWorkflow() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (runId: string) => api.cancelWorkflow(runId),
    onSuccess: () => {
      // The run stays in the list, with a new status — refresh both places it
      // appears rather than removing it from either.
      qc.invalidateQueries({ queryKey: ["workflows"] });
      qc.invalidateQueries({ queryKey: ["workflow-projects"] });
    },
  });
}

export function useDeleteWorkflow() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (runId: string) => api.deleteWorkflow(runId),
    onSuccess: () => {
      // Runs surface both standalone and inside a project — refresh both.
      qc.invalidateQueries({ queryKey: ["workflows"] });
      qc.invalidateQueries({ queryKey: ["workflow-projects"] });
    },
  });
}

export function useProjectParams(projectId: string) {
  return useQuery({
    // Deliberately NOT under the "workflow-projects" key: a generator's params
    // don't change on generate/plan/run, and every such mutation invalidates
    // "workflow-projects". Sharing the prefix would re-introspect the generator
    // on each mutation — wasteful, and previously a submit-loop for generators
    // that plan+submit at import time. Params are static per generator.
    queryKey: ["project-params", projectId],
    queryFn: () => api.getProjectParams(projectId),
    enabled: !!projectId,
    staleTime: Infinity,
    refetchOnWindowFocus: false,
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

export function useDeleteWorkflowProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (projectId: string) => api.deleteWorkflowProject(projectId),
    onSuccess: () => {
      // Runs live inside the project, so both lists are stale after this.
      qc.invalidateQueries({ queryKey: ["workflow-projects"] });
      qc.invalidateQueries({ queryKey: ["workflows"] });
    },
  });
}
