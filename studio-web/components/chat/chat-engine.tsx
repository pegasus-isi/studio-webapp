"use client";

/**
 * Renders whichever chat engine this user has selected (#367).
 *
 * One component so the two places the panel is mounted — the studio sidebar
 * and the chrome-less page JupyterLab iframes — cannot end up on different
 * engines, which is exactly the drift that made running two chat backends
 * untenable the first time (#299).
 *
 * "Two places" is literal and matters: `app/chat/page.tsx` is a separate
 * document inside the JupyterLab iframe, with its own React tree and its own
 * TanStack cache. The preference query is deliberately long-lived and does not
 * refetch on focus, so a switch made in Settings would not reach that document
 * until it was torn down — leaving the builtin loop running in the notebook
 * chat, or an OpenCode event stream open after the user had switched away.
 * `ENGINE_CHANNEL` closes that gap: the Settings control announces the change
 * and every mounted panel re-reads the preference.
 *
 * The transcript and composer are separate for the builtin engine, whose state
 * lives in a Zustand store, and combined for OpenCode, whose state lives in
 * OpenCode. Both fill the same flex column, so the caller lays out neither.
 *
 * Nothing is rendered until the preference has loaded. Mounting an engine for
 * a beat is not free — the builtin one fetches history and can reattach to a
 * turn — so a late preference response would swap engines under a panel that
 * had already started working. The fallback is "opencode" since #403, which
 * only matters when the fetch fails outright; it must stay equal to the
 * server default in `settings.py`.
 */

import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ChatPanel } from "@/components/chat/chat-panel";
import { ChatInput } from "@/components/chat/chat-input";
import { OpenCodeChat } from "@/components/chat/opencode-chat";
import { useUIPreferences } from "@/lib/hooks/use-ui-prefs";
import { subscribeEngineChange } from "@/lib/chat-engine-channel";

export function ChatEngine() {
  const { prefs, isLoading } = useUIPreferences();
  const queryClient = useQueryClient();

  useEffect(
    () =>
      subscribeEngineChange(() => {
        void queryClient.invalidateQueries({ queryKey: ["ui-preferences"] });
      }),
    [queryClient]
  );

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
