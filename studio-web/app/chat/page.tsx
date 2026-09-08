"use client";

import { useEffect } from "react";
import { ChatEngine } from "@/components/chat/chat-engine";
import { ConversationBar } from "@/components/chat/conversation-bar";
import { PegasusLogo } from "@/components/ui/pegasus-logo";
import { useThemeStore, applyTheme } from "@/lib/stores/theme-store";
import { useNotebookContextStore } from "@/lib/stores/notebook-context-store";

/**
 * Full-screen PegasusAI Chat, no studio chrome — embedded as an iframe panel
 * inside JupyterLab (same origin, so the gateway session and /api calls work).
 * The labextension postMessages the focused notebook path so chat is
 * notebook-aware.
 */
export default function ChatEmbedPage() {
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

  const notebookName = activeNotebookPath?.split("/").pop();

  return (
    <div className="flex h-screen flex-col bg-base">
      <div className="flex items-center gap-2 border-b border-line bg-surface px-3 py-2">
        <span className="flex shrink-0 items-center gap-1.5 text-sm font-semibold text-fg">
          <PegasusLogo size={20} />
          <span className="hidden sm:inline">PegasusAI</span>
        </span>
        <ConversationBar />
      </div>
      {/* Above the transcript, not between it and the composer: the engine now
          owns both halves (#367), so a strip rendered after it would sit under
          the composer instead of over it. */}
      {notebookName && (
        <div className="flex items-center gap-2 border-b border-line bg-muted px-4 py-1.5 text-xs">
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
      <ChatEngine />
    </div>
  );
}
