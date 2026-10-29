export type ToolCall = {
  id: string;
  name: string;
  arguments: unknown;
};

export type ToolResult = {
  id: string;
  name: string;
  result: string;
};

export type ToolActivity = {
  id: string;
  name: string;
  arguments: unknown;
  result?: string;
  status: "running" | "ok" | "error";
};

export function isToolError(result: string): boolean {
  const text = result.trimStart();
  return (
    text.startsWith("Error") ||
    text.includes("Verification failed for") ||
    text.includes("denied by the user") ||
    text.includes("timed out")
  );
}

export function mergeToolActivity(
  calls: ToolCall[] | undefined,
  results: ToolResult[] | undefined
): ToolActivity[] {
  const byId = new Map<string, ToolActivity>();

  for (const call of calls ?? []) {
    byId.set(call.id, {
      id: call.id,
      name: call.name,
      arguments: call.arguments,
      status: "running",
    });
  }

  for (const result of results ?? []) {
    const existing = byId.get(result.id);
    const status = isToolError(result.result) ? "error" : "ok";
    if (existing) {
      existing.result = result.result;
      existing.status = status;
    } else {
      byId.set(result.id, {
        id: result.id,
        name: result.name,
        arguments: {},
        result: result.result,
        status,
      });
    }
  }

  return [...byId.values()];
}

export function toolActivityTarget(
  name: string,
  args: unknown
): string | null {
  if (!args || typeof args !== "object") return null;
  const a = args as Record<string, unknown>;

  if (name === "edit_blocks" && Array.isArray(a.edits)) {
    const paths = [
      ...new Set(
        a.edits
          .map((edit) =>
            edit && typeof edit === "object"
              ? (edit as { path?: unknown }).path
              : null
          )
          .filter((p): p is string => typeof p === "string")
      ),
    ];
    return paths.length ? paths.join(", ") : null;
  }

  for (const key of ["path", "command", "query", "project_id", "run_id"]) {
    const value = a[key];
    if (typeof value === "string" && value.trim()) return value.trim();
  }

  return null;
}

export function latestToolActivity(
  entries: ToolActivity[],
  limit = 5
): ToolActivity[] {
  return limit > 0 ? entries.slice(-limit) : entries;
}
