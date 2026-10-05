"use client";

import { useEffect, useState } from "react";
import { usePathname } from "next/navigation";
import { apiUrl, stripAppBasePath } from "@/lib/urls";
import { feedbackFormUrl } from "@/lib/feedback";
import { useStudioVersion } from "@/lib/hooks/use-version";
import { useChatStore } from "@/lib/stores/chat-store";
import { useChatContextStore } from "@/lib/stores/chat-context-store";
import { useOpenCodeStore } from "@/lib/stores/opencode-store";
import { useWorkflowStore } from "@/lib/stores/workflow-store";

/**
 * "Feedback" cell for the left rail: opens the feedback form in a new tab with
 * the page, workflow, run, chat model, agent, version and user filled in.
 *
 * In the rail rather than only in the account menu because early users are
 * asked to report as they go, and a report filed two clicks deep is a report
 * not filed. Everything is read at click time, so it describes what the user
 * was looking at when they decided to write, not when the rail mounted.
 */
export function FeedbackButton({
  className,
  children,
}: {
  className: string;
  children: React.ReactNode;
}) {
  const pathname = usePathname();
  const version = useStudioVersion();
  // Fetched up front, not on click: window.open must run synchronously inside
  // the click handler, or the browser treats the new tab as a popup and
  // blocks it.
  const [user, setUser] = useState<string | null>(null);
  useEffect(() => {
    fetch(apiUrl("whoami"))
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => setUser(d?.user ?? null))
      .catch(() => setUser(null));
  }, []);

  const open = () => {
    const convId = useChatStore.getState().activeId;
    const scope = useChatContextStore.getState().scopeByConv[convId] ?? null;
    const { selectedRunId, selectedProjectId } = useWorkflowStore.getState();
    const { agentByConv, currentModel } = useOpenCodeStore.getState();
    const search = typeof window !== "undefined" ? window.location.search : "";
    const runFromUrl = new URLSearchParams(search).get("run");

    const url = feedbackFormUrl({
      page: stripAppBasePath(pathname) + search,
      workflow:
        selectedProjectId ?? (scope?.type === "workflow" ? scope.id : null),
      run:
        selectedRunId ?? runFromUrl ?? (scope?.type === "run" ? scope.id : null),
      // An empty pick means the deployment's configured default model, which
      // is worth saying rather than leaving the field blank.
      model: currentModel || "configured default",
      agent: agentByConv[convId] || "default",
      version,
      user,
    });
    window.open(url, "_blank", "noopener,noreferrer");
  };

  return (
    <button
      onClick={open}
      title="Send feedback — opens a short form in a new tab"
      data-tour="nav-feedback"
      className={className}
    >
      {children}
    </button>
  );
}
