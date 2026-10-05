"use client";

/**
 * "Did the AI help?" — the browser half of services/feedback_episodes.py.
 *
 * The server decides *whether* a prompt may be shown (one per browser session,
 * help cards only for the first three episodes, the dismissal snooze, the
 * `feedback` mute). The browser decides only *when*: never while the AI is
 * streaming, and in the chat panel if it is open, else on the dashboard.
 */

import { useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { create } from "zustand";
import * as api from "@/lib/api/client";
import { useChatStore } from "@/lib/stores/chat-store";
import { useChatContextStore } from "@/lib/stores/chat-context-store";
import { useOpenCodeStore } from "@/lib/stores/opencode-store";
import { usePanelStore } from "@/lib/stores/panel-store";

/** react-query key; the notification stream invalidates it on a poke. */
export const FEEDBACK_PENDING_KEY = ["feedback", "pending"] as const;

let sessionId: string | null = null;

/**
 * One id per tab load — module state, so a reload is a new session and two
 * tabs are two. The server's "one prompt per session" rule counts these.
 */
export function feedbackSessionId(): string {
  if (!sessionId) {
    sessionId =
      typeof crypto !== "undefined" && crypto.randomUUID
        ? crypto.randomUUID()
        : `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
  }
  return sessionId;
}

const promptKey = (p: api.FeedbackPrompt) => `${p.episode_id}:${p.prompt_type}`;

interface FeedbackStore {
  prompt: api.FeedbackPrompt | null;
  /** False when prompts are off (env, `feedback` muted, snoozed). */
  promptsEnabled: boolean;
  /**
   * Prompts answered in this tab. A poll can land between the click and the
   * answer reaching the server, and would otherwise put the card straight
   * back. Keyed by prompt type as well as episode, because a nudge answered
   * with "Ask the AI" legitimately returns later as that episode's help card.
   */
  handled: Set<string>;
  receive: (prompt: api.FeedbackPrompt | null, enabled: boolean) => void;
  handle: (prompt: api.FeedbackPrompt) => void;
}

export const useFeedbackStore = create<FeedbackStore>((set) => ({
  prompt: null,
  promptsEnabled: true,
  handled: new Set(),
  receive: (prompt, enabled) =>
    set((s) => ({
      promptsEnabled: enabled,
      prompt: prompt && !s.handled.has(promptKey(prompt)) ? prompt : null,
    })),
  handle: (prompt) =>
    set((s) => {
      const handled = new Set(s.handled);
      handled.add(promptKey(prompt));
      return { handled, prompt: null };
    }),
}));

/**
 * Keeps the store in step with the server. Mounted once in the studio shell.
 *
 * Polled as well as poked: the watch loop announces a newly due prompt over
 * the notification stream, but a stream that was reconnecting at that moment
 * would lose it, and a prompt that is due on a timer (the 10-minute nudge) is
 * worth asking about now and then regardless.
 */
export function useFeedbackPromptSync() {
  const receive = useFeedbackStore((s) => s.receive);
  const { data } = useQuery({
    queryKey: FEEDBACK_PENDING_KEY,
    queryFn: () => api.getPendingFeedbackPrompt(feedbackSessionId()),
    refetchInterval: 60_000,
    // A failed poll is not worth surfacing; the next one will do.
    retry: false,
  });
  useEffect(() => {
    if (data) receive(data.prompt, data.prompts_enabled);
  }, [data, receive]);
}

/**
 * Ask the AI about a failed run, the same way from every button that offers
 * it: scope the active conversation to the run, open the chat with a draft,
 * and tell the server the episode's AI was asked.
 *
 * The scope is what links the *turns* to the episode as well: every message
 * sent while the chat is scoped to a run is reported by `useOpenCodeChat`.
 */
export function askAboutFailedRun({
  runId,
  label,
  draft,
}: {
  runId: string;
  label: string;
  draft: string;
}) {
  const convId = useChatStore.getState().activeId;
  useChatContextStore
    .getState()
    .setScope(convId, { type: "run", id: runId, label });
  usePanelStore.getState().openChatWith(draft);
  const conversation =
    useOpenCodeStore.getState().sessionByConv[convId] ?? convId;
  // Fire and forget: a 404 means the run has no episode (it did not fail, or
  // failed before Studio was watching), and a failure here must not stop the
  // user getting their answer.
  void api
    .markEpisodeAsked(runId, { conversation_id: conversation, turn: false })
    .catch(() => undefined);
}
