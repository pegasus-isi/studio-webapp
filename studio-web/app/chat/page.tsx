"use client";

import { useEffect } from "react";
import { ChatEngine } from "@/components/chat/chat-engine";
import { ConversationBar } from "@/components/chat/conversation-bar";
import { PegasusLogo } from "@/components/ui/pegasus-logo";
import { useThemeStore, applyTheme } from "@/lib/stores/theme-store";

/**
 * Full-screen PegasusAI Chat, no studio chrome — embedded as an iframe panel
 * inside JupyterLab (same origin, so the gateway session and /api calls work).
 */
export default function ChatEmbedPage() {
  const theme = useThemeStore((s) => s.theme);

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  return (
    <div className="flex h-screen flex-col bg-base">
      <div className="flex items-center gap-2 border-b border-line bg-surface px-3 py-2">
        <span className="flex shrink-0 items-center gap-1.5 text-sm font-semibold text-fg">
          <PegasusLogo size={20} />
          <span className="hidden sm:inline">PegasusAI</span>
        </span>
        <ConversationBar />
      </div>
      <ChatEngine />
    </div>
  );
}
