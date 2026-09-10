import { describe, expect, it } from "vitest";
import { QueryClient } from "@tanstack/react-query";
import { applyExecutionBackend, executionBackendKey, prepareBackendChange } from "./execution-backend-cache";
import type { ExecutionBackendStatus } from "./api/client";

const status = (backend: string): ExecutionBackendStatus => ({ backend, backends: [] });
const client = () => new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });

describe("compute selection cache", () => {
  it("updates shared selection and clears only backend-dependent data", async () => {
    const qc = client();
    qc.setQueryData(executionBackendKey, status("local"));
    qc.setQueryData(["project-params", "local", "same-name"], { site: "old-site" });
    qc.setQueryData(["workflow-projects", "local"], ["old-project"]);
    qc.setQueryData(["tools"], ["terminal"]);
    qc.setQueryData(["llm-config"], { provider: "saved" });
    await applyExecutionBackend(qc, status("fabric"));
    expect(qc.getQueryData(executionBackendKey)).toEqual(status("fabric"));
    expect(qc.getQueryData(["project-params", "local", "same-name"])).toBeUndefined();
    expect(qc.getQueryData(["workflow-projects", "local"])).toBeUndefined();
    expect(qc.getQueryData(["tools"])).toEqual(["terminal"]);
    expect(qc.getQueryData(["llm-config"])).toEqual({ provider: "saved" });
    qc.clear();
  });

  it("an old in-flight project response cannot repopulate data after a switch", async () => {
    const qc = client();
    qc.setQueryData(executionBackendKey, status("local"));
    let complete!: (value: string[]) => void;
    const late = qc.fetchQuery({
      queryKey: ["workflow-projects", "local"],
      queryFn: () => new Promise<string[]>((resolve) => { complete = resolve; }),
    }).catch(() => undefined);
    await applyExecutionBackend(qc, status("fabric"));
    complete(["old-project"]);
    await late;
    expect(qc.getQueryData(["workflow-projects", "local"])).toBeUndefined();
    expect(qc.getQueryData(executionBackendKey)).toEqual(status("fabric"));
    qc.clear();
  });

  it("polling the same selection preserves cached generator parameters", async () => {
    const qc = client();
    qc.setQueryData(executionBackendKey, status("local"));
    qc.setQueryData(["project-params", "local", "p"], { count: 12 });
    await prepareBackendChange(qc, status("local"));
    expect(qc.getQueryData(["project-params", "local", "p"])).toEqual({ count: 12 });
    // Another tab changing the server selection follows the same invalidation.
    await prepareBackendChange(qc, status("fabric"));
    expect(qc.getQueryData(["project-params", "local", "p"])).toBeUndefined();
    qc.clear();
  });
});

it("a cancelled backend poll cannot discard the new backend's data", async () => {
  const qc = client();
  qc.setQueryData(executionBackendKey, status("local"));
  let complete!: (value: ExecutionBackendStatus) => void;
  const poll = qc.fetchQuery({
    queryKey: executionBackendKey,
    queryFn: async ({ signal }) => {
      const response = await new Promise<ExecutionBackendStatus>((resolve) => { complete = resolve; });
      await prepareBackendChange(qc, response, signal);
      return response;
    },
  }).catch(() => undefined);
  await applyExecutionBackend(qc, status("fabric"));
  qc.setQueryData(["workflow-projects", "fabric"], ["remote-project"]);
  complete(status("local"));
  await poll;
  await Promise.resolve();
  expect(qc.getQueryData(executionBackendKey)).toEqual(status("fabric"));
  expect(qc.getQueryData(["workflow-projects", "fabric"])).toEqual(["remote-project"]);
  qc.clear();
});
