"use client";

import { useNotificationStore } from "@/lib/stores/notification-store";

/**
 * Run an action (typically a `mutateAsync`) and surface a toast if it throws
 * (FE-6). Without this, `await mutateAsync(...)` in an onClick handler with no
 * try/catch just un-sticks the button on a 500/timeout with no message.
 *
 * Returns the resolved value on success, or `undefined` on failure (already
 * shown to the user), so callers gate their follow-up on the result:
 *
 *   const runAction = useActionToast();
 *   const res = await runAction("Analyze", () => analyze.mutateAsync(runId));
 *   if (res) setAnalysis(res.analysis);
 */
export function useActionToast() {
  const pushToast = useNotificationStore((s) => s.pushToast);
  return async function runAction<T>(
    label: string,
    fn: () => Promise<T>
  ): Promise<T | undefined> {
    try {
      return await fn();
    } catch (e) {
      pushToast({
        title: `${label} failed`,
        body: e instanceof Error ? e.message : String(e),
        severity: "error",
      });
      return undefined;
    }
  };
}
