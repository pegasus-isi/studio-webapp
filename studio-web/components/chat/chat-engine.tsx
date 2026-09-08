"use client";

/**
 * Renders whichever chat engine this user has selected (#367).
 *
 * One component so the two places the panel is mounted — the studio sidebar
 * and the chrome-less page JupyterLab iframes — cannot end up on different
 * engines, which is exactly the drift that made running two chat backends
 * untenable the first time (#299).
 *
 * The transcript and composer are separate for the builtin engine, whose state
 * lives in a Zustand store, and combined for OpenCode, whose state lives in
 * OpenCode. Both fill the same flex column, so the caller lays out neither.
 *
 * Nothing is rendered until the preference has loaded. The fallback is
 * "builtin", and mounting that engine for a beat is not free: it fetches
 * history and can reattach to a turn, so a late preference response would
 * swap engines under a panel that had already started working.
 */

import { ChatPanel } from "@/components/chat/chat-panel";
import { ChatInput } from "@/components/chat/chat-input";
import { OpenCodeChat } from "@/components/chat/opencode-chat";
import { useUIPreferences } from "@/lib/hooks/use-ui-prefs";

export function ChatEngine() {
  const { prefs, isLoading } = useUIPreferences();

  if (isLoading) {
    return (
      <div className="flex flex-1 items-center justify-center text-xs text-fgsubtle">
        Loading chat…
      </div>
    );
  }

  if (prefs.chat_engine === "opencode") return <OpenCodeChat />;

  return (
    <>
      <div className="flex-1 overflow-hidden">
        <ChatPanel />
      </div>
      <ChatInput />
    </>
  );
}
