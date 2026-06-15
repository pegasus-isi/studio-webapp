"use client";

import { useEffect } from "react";
import { ChatPanel } from "@/components/chat/chat-panel";
import { ChatInput } from "@/components/chat/chat-input";
import { PegasusLogo } from "@/components/ui/pegasus-logo";
import { useChatStore } from "@/lib/stores/chat-store";
import { useThemeStore, applyTheme } from "@/lib/stores/theme-store";
import { useNotebookContextStore } from "@/lib/stores/notebook-context-store";
import * as api from "@/lib/api/client";

/**
 * Full-screen PegasusAI Chat, no studio chrome — embedded as an iframe panel
 * inside JupyterLab (same origin, so the gateway session and /api calls work).
 * The labextension postMessages the focused notebook path so chat is
 * notebook-aware.
 */
export default function ChatEmbedPage() {
  const loadHistory = useChatStore((s) => s.loadHistory);
  const theme = useThemeStore((s) => s.theme);
  const activeNotebookPath = useNotebookContextStore((s) => s.activeNotebookPath);
  const include = useNotebookContextStore((s) => s.include);
  const setActiveNotebookPath = useNotebookContextStore(
    (s) => s.setActiveNotebookPath
  );
  const setInclude = useNotebookContextStore((s) => s.setInclude);

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  useEffect(() => {
    const onMessage = (e: MessageEvent) => {
      if (e.origin !== window.location.origin) return;
      if (e.data?.type === "pegasusai:active-notebook") {
        setActiveNotebookPath(e.data.path || null);
      }
    };
    window.addEventListener("message", onMessage);
    if (window.parent !== window) {
      window.parent.postMessage(
        { type: "pegasusai:ready" },
        window.location.origin
      );
    }
    return () => window.removeEventListener("message", onMessage);
  }, [setActiveNotebookPath]);

  useEffect(() => {
    api
      .getChatHistory()
      .then((data) => {
        const msgs = data.messages.map((m, i) => ({
          id: `hist-${i}`,
          role: m.role as "user" | "assistant",
          content: m.content,
          agentId: m.agent_id ?? undefined,
          createdAt: m.created_at ?? undefined,
        }));
        if (msgs.length > 0) loadHistory(msgs);
      })
      .catch(() => {});
  }, [loadHistory]);

  const notebookName = activeNotebookPath?.split("/").pop();

  return (
    <div className="flex h-screen flex-col bg-base">
      <div className="flex items-center gap-2 border-b border-line bg-surface px-4 py-2.5">
        <PegasusLogo size={20} />
        <span className="text-sm font-semibold text-fg">PegasusAI Chat</span>
      </div>
      <div className="flex-1 overflow-hidden">
        <ChatPanel />
      </div>
      {notebookName && (
        <div className="flex items-center gap-2 border-t border-line bg-muted px-4 py-1.5 text-xs">
          <input
            type="checkbox"
            checked={include}
            onChange={(e) => setInclude(e.target.checked)}
            className="accent-pegasus-500"
            title="Include this notebook as context"
          />
          <span className="text-fgmuted">Context:</span>
          <span
            className="truncate font-medium text-fg"
            title={activeNotebookPath ?? undefined}
          >
            📓 {notebookName}
          </span>
        </div>
      )}
      <ChatInput />
    </div>
  );
}
