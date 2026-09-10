import type { QueryClient, QueryKey } from "@tanstack/react-query";
import type { ExecutionBackendStatus } from "@/lib/api/client";

export const executionBackendKey = ["execution-backend"] as const;

export function isBackendQuery(key: QueryKey): boolean {
  return ["workflows", "workflow-projects", "project-params", "dashboard", "provisioning"]
    .includes(String(key[0]));
}

/** Discard old responses before exposing a new selection to all consumers. */
export async function prepareBackendChange(qc: QueryClient, next: ExecutionBackendStatus, signal?: AbortSignal) {
  if (signal?.aborted) return;
  const previous = qc.getQueryData<ExecutionBackendStatus>(executionBackendKey);
  if (previous && previous.backend !== next.backend) {
    const filter = { predicate: (q: { queryKey: QueryKey }) => isBackendQuery(q.queryKey) };
    await qc.cancelQueries(filter);
    if (!signal?.aborted) qc.removeQueries(filter);
  }
}

export async function applyExecutionBackend(qc: QueryClient, next: ExecutionBackendStatus) {
  // A poll that started before the PUT must not restore the old selection.
  await qc.cancelQueries({ queryKey: executionBackendKey });
  await prepareBackendChange(qc, next);
  qc.setQueryData(executionBackendKey, next);
}
