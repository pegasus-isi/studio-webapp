"use client";

/**
 * The chat panel, backed by the OpenCode agent server (#367).
 *
 * Transcript and composer in one component because the state they share lives
 * in `useOpenCodeChat`, not in a store: OpenCode is the source of truth and
 * this is a view of it.
 *
 * What this component decides is *how to draw* OpenCode's data. What it must
 * never do is interpret it: parts go to the registry in opencode-parts.tsx,
 * which has a fallback, so an OpenCode release that adds a part type or an
 * event type still renders and still completes the turn.
 */

import { memo, useEffect, useMemo, useRef, useState } from "react";
import type { AssistantMessage } from "@opencode-ai/sdk";
import { ArrowDown } from "lucide-react";
import { formatTokens, outputLimitInfo } from "@/lib/opencode/output-limit";
import { nextFollowing } from "@/lib/opencode/scroll-follow";
import {
  MessageLiveContext,
  PartView,
  formatElapsed,
  useElapsed,
} from "@/components/chat/opencode-parts";
import { ChatContextChip } from "@/components/chat/context-chip";
import { PegasusLogo } from "@/components/ui/pegasus-logo";
import {
  errorMessage,
  isAbortError,
  useOpenCodeChat,
  type RenderedMessage,
} from "@/lib/hooks/use-opencode-chat";
import * as api from "@/lib/api/client";
import * as oc from "@/lib/opencode/client";
import type {
  PermissionRequest,
  QuestionRequest,
} from "@/lib/opencode/client";
import { useChatStore } from "@/lib/stores/chat-store";
import { useOpenCodeStore } from "@/lib/stores/opencode-store";
import { usePanelStore } from "@/lib/stores/panel-store";
import { useChatContextStore } from "@/lib/stores/chat-context-store";
import { useFeedbackStore } from "@/lib/feedback/feedback";
import { FeedbackPromptCard } from "@/components/feedback/feedback-prompt-card";
import { GiveUpRow } from "@/components/feedback/give-up-row";
import { cn } from "@/lib/utils";
import { AppLink } from "@/components/layout/app-link";

const STARTERS = [
  "Build me a workflow that",
  "Why did my run fail",
  "Explain what this workflow does",
];

interface ModelChoice {
  providerID: string;
  providerName: string;
  modelID: string;
  label: string;
}

export function OpenCodeChat() {
  const chat = useOpenCodeChat();
  const [input, setInput] = useState("");
  const [models, setModels] = useState<ModelChoice[] | null>(null);
  const [agents, setAgents] = useState<oc.AgentInfo[] | null>(null);
  const [showCapabilities, setShowCapabilities] = useState(false);
  const [picked, setPicked] = useState<string>("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const [engineDown, setEngineDown] = useState<oc.EngineStatus | null>(null);
  const [restarting, setRestarting] = useState(false);

  // Bumped by the recovery button so the catalog is re-fetched after a restart;
  // without it the picker stayed on "Configured default" alone for the life of
  // the page once the first fetch had failed.
  const [catalogAttempt, setCatalogAttempt] = useState(0);

  // What "Configured default" resolves to: the provider and model activated in
  // Settings → AI providers, which the propagator writes into opencode.json as
  // the engine's `model`. Shown on the option itself, because a picker that
  // says only "Configured default" leaves the user guessing which model is
  // answering — the question "what is the configured default?" came up on
  // the first day of early-user testing.
  const [configured, setConfigured] = useState<api.LLMConfig | null>(null);
  useEffect(() => {
    let cancelled = false;
    api
      .getLLMConfig()
      .then((cfg) => {
        if (!cancelled) setConfigured(cfg);
      })
      .catch(() => {
        if (!cancelled) setConfigured(null);
      });
    return () => {
      cancelled = true;
    };
  }, [catalogAttempt]);

  const defaultLabel = useMemo(() => {
    if (!configured) return "Configured default";
    if (!configured.provider) return "No provider configured";
    const providerName =
      models?.find((m) => m.providerID === configured.provider)?.providerName ??
      configured.provider;
    // No model saved means the provider's own default answers; name the
    // provider so at least that much is visible.
    const model = configured.model?.split("/").pop() || "provider default";
    return `Configured default (${providerName} / ${model})`;
  }, [configured, models]);

  // Model list from the engine's own provider catalog, so the picker offers
  // exactly what the generated config declared — no second source of truth
  // about which models exist.
  useEffect(() => {
    let cancelled = false;
    oc.getProviders()
      .then((catalog) => {
        if (cancelled) return;
        const choices: ModelChoice[] = [];
        for (const provider of catalog.providers ?? []) {
          for (const [modelID, model] of Object.entries(provider.models ?? {})) {
            choices.push({
              providerID: provider.id,
              providerName: provider.name || provider.id,
              modelID,
              label: `${provider.name || provider.id} / ${model?.name || modelID}`,
            });
          }
        }
        setModels(choices);
      })
      .catch(() => setModels([]));
    return () => {
      cancelled = true;
    };
  }, [catalogAttempt]);

  // The agents this engine offers. Read from the server rather than hardcoded,
  // so an agent added by a marketplace update or by the user's own config
  // appears without a change here.
  useEffect(() => {
    let cancelled = false;
    oc.listAgents(chat.sessionDirectory)
      .then((list) => {
        if (!cancelled) setAgents(list);
      })
      .catch(() => {
        if (!cancelled) setAgents([]);
      });
    return () => {
      cancelled = true;
    };
    // Re-read when the session moves: a workflow project can define its own
    // agents under .opencode/, and the picker should offer them.
  }, [catalogAttempt, chat.sessionDirectory]);

  // Why the panel is empty, when it is. A failed fetch alone leaves the user
  // looking at a blank transcript with nothing to act on; "the engine isn't
  // running, here is the button" is actionable.
  useEffect(() => {
    if (chat.connection !== "retrying" && !chat.error) {
      setEngineDown(null);
      return;
    }
    let cancelled = false;
    oc.getEngineStatus()
      .then((status) => {
        if (!cancelled) setEngineDown(status.running ? null : status);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [chat.connection, chat.error]);

  // Follow the tail only while the reader is following it. Whether they are
  // is decided from their scroll events (lib/opencode/scroll-follow.ts), not
  // from distance-to-bottom on each streamed update: that used to scroll a
  // reader who had just moved up one wheel notch straight back down.
  const followingRef = useRef(true);
  const lastScrollTopRef = useRef(0);
  const [following, setFollowing] = useState(true);
  const onScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    const next = nextFollowing(followingRef.current, lastScrollTopRef.current, el);
    lastScrollTopRef.current = el.scrollTop;
    followingRef.current = next;
    setFollowing(next);
  };
  const jumpToLatest = () => {
    const el = scrollRef.current;
    followingRef.current = true;
    setFollowing(true);
    if (el) el.scrollTop = el.scrollHeight;
  };
  // A new conversation, or a message just sent, starts at the bottom.
  useEffect(() => {
    followingRef.current = true;
    setFollowing(true);
  }, [chat.sessionID]);

  // Coalesced to one scroll per frame, and instant rather than smooth while a
  // reply streams: a long answer produces hundreds of state updates, and
  // restarting a smooth animation on each one meant the proximity test below
  // was reading a scrollTop that the previous animation had not finished
  // moving — so auto-follow would quietly give up partway through.
  const followRef = useRef<number | null>(null);
  useEffect(() => {
    if (followRef.current !== null) return;
    followRef.current = requestAnimationFrame(() => {
      followRef.current = null;
      const el = scrollRef.current;
      if (!el) return;
      if (followingRef.current) el.scrollTop = el.scrollHeight;
    });
  }, [chat.messages, chat.busy]);
  useEffect(
    () => () => {
      if (followRef.current !== null) cancelAnimationFrame(followRef.current);
    },
    []
  );

  // A one-shot draft handed over from elsewhere in the app (a dashboard card).
  const chatDraft = usePanelStore((s) => s.chatDraft);
  const setChatDraft = usePanelStore((s) => s.setChatDraft);
  useEffect(() => {
    if (chatDraft == null) return;
    setInput(chatDraft);
    setChatDraft(null);
    const el = textareaRef.current;
    if (el) {
      el.focus();
      requestAnimationFrame(() => {
        el.selectionStart = el.selectionEnd = el.value.length;
      });
    }
  }, [chatDraft, setChatDraft]);

  // Mirrored for the feedback link, which reports the model in use.
  const setCurrentModel = useOpenCodeStore((s) => s.setCurrentModel);
  useEffect(() => setCurrentModel(picked), [picked, setCurrentModel]);

  const model = useMemo(() => {
    if (!picked) return undefined;
    const [providerID, ...rest] = picked.split("/");
    return { providerID, modelID: rest.join("/") };
  }, [picked]);

  const submit = () => {
    const text = input.trim();
    if (!text || chat.busy) return;
    setInput("");
    jumpToLatest();
    void chat.send(
      text,
      model,
      selectable.map((a) => a.name)
    );
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (
      e.key === "Enter" &&
      !e.shiftKey &&
      !(e.nativeEvent as { isComposing?: boolean }).isComposing
    ) {
      e.preventDefault();
      submit();
    }
  };

  const restart = async () => {
    setRestarting(true);
    try {
      await oc.restartEngine();
      // Give supervisord time to bring it back before anything re-probes, so
      // the retry below doesn't just fail against a process still starting.
      await new Promise((resolve) => setTimeout(resolve, 1500));
    } catch {
      /* the status line reports the outcome on the next probe */
    } finally {
      setRestarting(false);
      // Restarting the engine is only half a recovery: if this panel never got
      // a session (the engine was down when it mounted), nothing else would
      // ever try again and the card would keep offering a button that fixed
      // the server and left the panel dead.
      chat.retry();
      setCatalogAttempt((n) => n + 1);
    }
  };

  // Keyed on whether anything is actually rendered yet, not on the last
  // message's role: an assistant message exists as soon as the turn starts, so
  // a role test hid the indicator while the only parts were an invisible
  // step-start and an empty text part — several seconds of a blank bubble
  // where the "Thinking" dots had been, on any model that pauses before its
  // first token.
  const selectable = useMemo(
    () => (agents ? oc.selectableAgents(agents) : []),
    [agents]
  );
  const activeAgent = useMemo(
    () => selectable.find((a) => a.name === chat.agent),
    [selectable, chat.agent]
  );
  const turns = useMemo(() => groupTurns(chat.messages), [chat.messages]);

  // Give-up row (#D): under a turn that was stopped or died in an engine
  // error — but only one this view watched happen. A reply first seen already
  // errored is history (a reload, another conversation), and asking about it
  // would be asking about something the user may not even remember. So a
  // message qualifies once it has been seen *without* an error first.
  const convId = useChatStore((s) => s.activeId);
  const scope = useChatContextStore((s) => s.scopeByConv[convId]);
  const promptsEnabled = useFeedbackStore((s) => s.promptsEnabled);
  const seenLive = useRef(new Set<string>());
  const [giveUpDone, setGiveUpDone] = useState<Set<string>>(() => new Set());
  useEffect(() => {
    for (const m of chat.messages) {
      if (m.info.role === "assistant" && !assistantError(m)) {
        seenLive.current.add(m.info.id);
      }
    }
  }, [chat.messages]);
  const giveUp = useMemo(() => {
    if (chat.busy || !promptsEnabled) return null;
    const last = chat.messages[chat.messages.length - 1];
    if (!last || last.info.role !== "assistant") return null;
    const err = assistantError(last);
    if (!err) return null;
    if (!seenLive.current.has(last.info.id) || giveUpDone.has(last.info.id)) {
      return null;
    }
    return {
      messageID: last.info.id,
      reason: (isAbortError(err) ? "stop" : "error") as "stop" | "error",
    };
  }, [chat.messages, chat.busy, promptsEnabled, giveUpDone]);
  const closeGiveUp = (messageID: string) =>
    setGiveUpDone((prev) => new Set(prev).add(messageID));
  const lastMessage = chat.messages[chat.messages.length - 1];
  // Shown for the whole of a busy turn, not only until the first part lands:
  // once reasoning or a tool call is on screen the bubble above may not change
  // for minutes, and with the dots gone there was nothing left that moved.
  // Hidden while a permission or question card is up, since then the turn is
  // waiting on the user, not working.
  const showThinking =
    chat.busy && chat.permissions.length === 0 && chat.questions.length === 0;
  const thinkingBare =
    lastMessage?.info.role !== "assistant" || !hasRenderableContent(lastMessage);
  const turnStart = useMemo(() => {
    for (let i = chat.messages.length - 1; i >= 0; i--) {
      const info = chat.messages[i].info;
      if (info.role === "user") return info.time.created;
    }
    return undefined;
  }, [chat.messages]);

  return (
    <>
      <div ref={scrollRef} onScroll={onScroll} className="flex-1 overflow-auto p-4">
        {engineDown && (
          <div className="mx-auto mb-3 max-w-3xl rounded-md border border-amber-500/50 bg-amber-500/10 p-3 text-xs">
            <div className="font-medium text-amber-700 dark:text-amber-400">
              The chat engine is not responding
            </div>
            <p className="mt-1 text-fgmuted">
              {engineDown.detail ||
                (engineDown.reason === "not_listening"
                  ? "It is not listening yet — it may still be starting."
                  : "OpenCode is unreachable in this session.")}
            </p>
            <button
              onClick={restart}
              disabled={restarting}
              className="mt-2 rounded-md border border-line px-2.5 py-1 font-medium text-fgmuted hover:bg-muted disabled:opacity-50"
            >
              {restarting ? "Restarting…" : "Restart the engine"}
            </button>
          </div>
        )}

        {/* A failure that leaves the panel with no session, while the engine
            itself reports healthy — a transient 500 on create, a network blip.
            The engine-down card above does not apply, the transcript is empty
            so the error box below never renders, and Send is disabled for want
            of a session: without this the panel is simply dead and silent. */}
        {!engineDown && chat.error && !chat.sessionID && (
          <div className="mx-auto mb-3 max-w-3xl rounded-md border border-rose-500/40 bg-rose-500/10 p-3 text-xs">
            <div className="font-medium text-rose-700 dark:text-rose-400">
              Could not start a chat session
            </div>
            <p className="mt-1 text-fgmuted [overflow-wrap:anywhere]">
              {chat.error}
            </p>
            <button
              onClick={() => {
                // Whatever stopped the session from being created stopped the
                // catalogs too, so recovery reloads both rather than leaving
                // the capability lists stale behind a working chat.
                chat.retry();
                setCatalogAttempt((n) => n + 1);
              }}
              className="mt-2 rounded-md border border-line px-2.5 py-1 font-medium text-fgmuted hover:bg-muted"
            >
              Try again
            </button>
          </div>
        )}

        {chat.messages.length === 0 && !engineDown ? (
          <div className="flex h-full items-center justify-center">
            <div className="text-center text-fgsubtle">
              <div className="mb-3 flex justify-center">
                <PegasusLogo size={48} />
              </div>
              <p className="text-lg">Welcome to PegasusAI Chat</p>
              <p className="mt-1 text-sm">
                Describe what you want to do and I&apos;ll build or fix it for
                you.
              </p>
              <div className="mt-4 space-y-1.5">
                {STARTERS.map((starter) => (
                  <button
                    key={starter}
                    onClick={() => setInput(starter + " ")}
                    className="block w-full rounded-md border border-line bg-surface px-3 py-2 text-left text-sm text-fgmuted transition-colors hover:border-pegasus-400 hover:text-fg"
                  >
                    {starter}…
                  </button>
                ))}
              </div>
            </div>
          </div>
        ) : (
          <div className="mx-auto max-w-3xl space-y-4">
            {turns.map((turn, i) => (
              <TurnBubble
                key={turn.key}
                turn={turn}
                // Only the last turn can still be growing. While the session
                // is busy its final step may finish (finish: "tool-calls")
                // moments before the next step's message arrives, and thumbs
                // flashing in and out between steps would invite rating half
                // an answer.
                settled={!chat.busy || i < turns.length - 1}
              />
            ))}
            {giveUp && (
              <GiveUpRow
                reason={giveUp.reason}
                onClose={() => closeGiveUp(giveUp.messageID)}
                onPick={(tag) => {
                  closeGiveUp(giveUp.messageID);
                  // Against the episode when the chat is about a run; the
                  // server files it standalone if that run has none.
                  void api
                    .recordGiveUp({
                      conversation_id: chat.sessionID ?? convId,
                      episode_id: scope?.type === "run" ? scope.id : undefined,
                      reason: giveUp.reason,
                      tag,
                    })
                    .catch(() => undefined);
                }}
              />
            )}

            {chat.permissions.map((request) => (
              <PermissionCard
                key={request.id}
                request={request}
                onReply={chat.answerPermission}
              />
            ))}
            {chat.questions.map((request) => (
              <QuestionCard
                key={request.id}
                request={request}
                onAnswer={chat.answerQuestion}
                onReject={chat.rejectQuestion}
              />
            ))}

            {configured && !configured.provider && (
              // No provider at all: an Open OnDemand site without an LLM
              // gateway (#498). Without this the first message fails with an
              // engine error naming a host the user has never heard of.
              <div className="rounded border border-amber-500/40 bg-amber-500/10 px-2 py-1.5 text-xs text-amber-700 dark:text-amber-300">
                No AI provider is set up yet. Add one with your own API key in{" "}
                <AppLink href="/settings?tab=providers" className="font-semibold underline">
                  Application Settings → LLM Providers
                </AppLink>
                , then start a new chat.
              </div>
            )}
            {chat.error && (
              <div className="flex items-start gap-1.5 rounded border border-rose-500/40 bg-rose-500/10 px-2 py-1.5 text-xs text-rose-600 dark:text-rose-400">
                <span className="font-semibold">Error:</span>
                <span className="[overflow-wrap:anywhere]">{chat.error}</span>
              </div>
            )}
            {showThinking && (
              <ThinkingIndicator
                bare={thinkingBare}
                activity={
                  lastMessage?.info.role === "assistant"
                    ? currentActivity(lastMessage)
                    : "Thinking"
                }
                start={turnStart}
              />
            )}
            <div ref={bottomRef} />
          </div>
        )}
        {!following && chat.messages.length > 0 && (
          // Sticky inside the scroller, so it sits at the bottom of the
          // visible area without a positioned wrapper around the panel.
          <div className="pointer-events-none sticky bottom-0 flex justify-center">
            <button
              type="button"
              onClick={jumpToLatest}
              className="pointer-events-auto flex items-center gap-1 rounded-full border border-line bg-surface px-3 py-1 text-xs text-fgmuted shadow-md hover:text-fg"
            >
              <ArrowDown className="h-3 w-3" />
              {chat.busy ? "Follow the reply" : "Jump to latest"}
            </button>
          </div>
        )}
      </div>

      <div className="border-t border-line bg-surface p-4">
        <div className="mx-auto max-w-3xl space-y-2">
          {/* Above the composer rather than in the transcript: it is about
              the conversation, not part of it, and must be visible without
              scrolling to the bottom of a long reply. */}
          <FeedbackPromptCard placement="chat" busy={chat.busy} />
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <ChatContextChip />

            {/* Plan mode is an agent, and OpenCode enforces it: the `plan`
                agent denies every edit tool server-side, so this is a real
                guarantee rather than an instruction the model may ignore. */}
            {selectable.length > 1 && (
              <>
                <label className="text-fgmuted" htmlFor="opencode-agent">
                  Mode:
                </label>
                <select
                  id="opencode-agent"
                  value={chat.agent ?? ""}
                  onChange={(e) => chat.setAgent(e.target.value)}
                  title={activeAgent?.description || AUTO_DESCRIPTION}
                  className="rounded border border-line px-2 py-1 text-xs text-fg focus:border-pegasus-500 focus:ring-pegasus-500"
                >
                  {/* Empty is Auto, so every existing conversation that
                      never picked a mode gets routing rather than the
                      engine's generic agent. */}
                  <option value="">Auto</option>
                  {selectable.map((a) => (
                    <option key={a.name} value={a.name}>
                      {a.name}
                    </option>
                  ))}
                </select>
              </>
            )}

            <label className="text-fgmuted" htmlFor="opencode-model">
              Model:
            </label>
            <select
              id="opencode-model"
              value={picked}
              onChange={(e) => setPicked(e.target.value)}
              className="max-w-[18rem] rounded border border-line px-2 py-1 text-xs text-fg focus:border-pegasus-500 focus:ring-pegasus-500"
            >
              {/* Empty means "whatever the generated config says", which is the
                  provider the user activated in Settings — named here so the
                  user can see it without opening Settings. */}
              <option value="" title="Set in Application Settings → AI providers">
                {defaultLabel}
              </option>
              {(models ?? []).map((m) => (
                <option
                  key={`${m.providerID}/${m.modelID}`}
                  value={`${m.providerID}/${m.modelID}`}
                >
                  {m.label}
                </option>
              ))}
            </select>
            <button
              onClick={() => setShowCapabilities((v) => !v)}
              title="Skills, subagents and commands this session can use"
              aria-expanded={showCapabilities}
              className={cn(
                "ml-auto rounded border px-2 py-1 transition-colors",
                showCapabilities
                  ? "border-pegasus-400 text-pegasus-700 dark:text-pegasus-300"
                  : "border-line text-fgmuted hover:border-pegasus-300 hover:text-fg"
              )}
            >
              What it can do
            </button>
            {chat.connection === "retrying" && (
              <span className="w-full text-amber-600 dark:text-amber-400">
                Reconnecting…
              </span>
            )}
          </div>

          {/* Why this is a panel and not a set of controls: skills and
              subagents are not chosen, they are *matched* — the agent picks a
              skill by its description and delegates to a subagent through its
              task tool. So the useful thing to show is what is loaded and
              where it came from, which is also the fastest way to answer "are
              my Pegasus skills actually here?". */}
          {showCapabilities && (
            <CapabilitiesPanel
              agents={agents}
              directory={chat.sessionDirectory}
              directoryStatus={chat.directoryStatus}
              attempt={catalogAttempt}
              onRetry={() => {
                // Re-resolves the session's directory as well as the lists:
                // if that lookup was what failed, refetching the catalogs
                // alone would just ask about the wrong directory again.
                chat.retry();
                setCatalogAttempt((n) => n + 1);
              }}
            />
          )}

          {selectable.length > 1 && (
            <p className="text-xs text-fgmuted">
              {activeAgent?.description || AUTO_DESCRIPTION}
            </p>
          )}

          <div className="flex items-end gap-2">
            <textarea
              ref={textareaRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={onKeyDown}
              placeholder="Ask about Pegasus workflows... (Shift+Enter for newline)"
              rows={3}
              className="min-h-[72px] max-h-56 flex-1 resize-none rounded-md border border-line px-3 py-2 text-sm shadow-sm focus:border-pegasus-500 focus:ring-pegasus-500"
            />
            {chat.busy ? (
              <button
                onClick={() => void chat.stop()}
                title="Stop"
                aria-label="Stop"
                className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-red-600 text-white hover:bg-red-700"
              >
                <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
                  <rect x="6" y="6" width="12" height="12" rx="1.5" />
                </svg>
              </button>
            ) : (
              <button
                onClick={submit}
                disabled={!input.trim() || !chat.sessionID}
                title="Send"
                aria-label="Send"
                className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-pegasus-600 text-white hover:bg-pegasus-700 disabled:opacity-50"
              >
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                  <line x1="12" y1="19" x2="12" y2="5" />
                  <polyline points="5 12 12 5 19 12" />
                </svg>
              </button>
            )}
          </div>
        </div>
      </div>
    </>
  );
}

/**
 * Whether a message has anything to draw yet.
 *
 * Turn bookkeeping (`step-start`, `step-finish`, `snapshot`) is invisible, and
 * a text part exists before its first delta lands, so "has parts" is not the
 * same question as "has content".
 */
const AUTO_DESCRIPTION =
  "Auto: picks workflow-architect, data-engineer or pipeline-debugger for each message";

function hasRenderableContent(message: RenderedMessage): boolean {
  return message.parts.some((part) => {
    if (
      part.type === "step-start" ||
      part.type === "step-finish" ||
      part.type === "snapshot"
    ) {
      return false;
    }
    if (part.type === "text" || part.type === "reasoning") {
      return part.text.trim().length > 0;
    }
    return true;
  });
}

/**
 * What the engine has loaded: subagents, skills, commands.
 *
 * Read-only on purpose. None of these are things a user invokes from here — a
 * skill is matched by the agent against its description, and a subagent is
 * delegated to through the task tool — so a picker would misrepresent how they
 * work. What is genuinely useful is knowing they are *there*, and where they
 * came from: Studio's own skills arrive from a marketplace checkout that has
 * gone missing before (#179), and "is my Pegasus skill loaded?" was previously
 * only answerable from a terminal.
 */
type Catalog<T> =
  | { status: "loading" }
  | { status: "ok"; items: T[] }
  | { status: "error" };

function CapabilitiesPanel({
  agents,
  directory,
  directoryStatus,
  attempt,
  onRetry,
}: {
  agents: oc.AgentInfo[] | null;
  directory?: string;
  directoryStatus: "none" | "loading" | "ok" | "error";
  attempt: number;
  onRetry: () => void;
}) {
  const [skills, setSkills] = useState<Catalog<oc.SkillInfo>>({
    status: "loading",
  });
  const [commands, setCommands] = useState<Catalog<oc.CommandInfo>>({
    status: "loading",
  });

  useEffect(() => {
    let cancelled = false;
    setSkills({ status: "loading" });
    setCommands({ status: "loading" });
    void oc
      .listSkills(directory)
      .then((items) => !cancelled && setSkills({ status: "ok", items }))
      // A failed lookup is not an empty one. This panel exists to answer "are
      // my skills loaded?", so reporting "none" when we could not ask is the
      // one wrong answer it must never give.
      .catch(() => !cancelled && setSkills({ status: "error" }));
    void oc
      .listCommands(directory)
      .then((items) => !cancelled && setCommands({ status: "ok", items }))
      .catch(() => !cancelled && setCommands({ status: "error" }));
    return () => {
      cancelled = true;
    };
    // `attempt` as well as `directory`: recovering the engine has to clear an
    // error state here too, or the panel keeps saying it could not check long
    // after it could.
  }, [directory, attempt]);

  const delegates = agents ? oc.subagents(agents) : [];
  // A skill is also offered as a command of the same name, so listing both in
  // full would show everything twice.
  const skillNames = new Set(
    skills.status === "ok" ? skills.items.map((s) => s.name) : []
  );
  const ownCommands =
    commands.status === "ok"
      ? commands.items.filter((c) => !skillNames.has(c.name))
      : [];

  // A failed *directory* lookup counts as a failure even when every list came
  // back, because those lists then describe the engine's default directory
  // rather than this session's — the case that reads as perfectly normal and
  // is simply wrong.
  const failed =
    directoryStatus === "error" ||
    skills.status === "error" ||
    commands.status === "error" ||
    (agents?.length ?? 0) === 0;

  return (
    <div className="max-h-56 space-y-3 overflow-auto rounded-md border border-line bg-muted p-2.5 text-xs">
      {directoryStatus === "error" && (
        <p className="rounded border border-amber-500/40 bg-amber-500/10 px-2 py-1.5 text-amber-700 dark:text-amber-400">
          Could not tell which folder this chat is working in, so this list may
          be for the wrong one — anything the workflow itself defines would be
          missing.
        </p>
      )}
      {failed && (
        <button
          onClick={onRetry}
          className="w-full rounded border border-line bg-surface px-2 py-1 font-medium text-fgmuted hover:text-fg"
        >
          Check again
        </button>
      )}
      <Capability
        title="Subagents"
        status={agents === null ? "loading" : "ok"}
        empty="None — this engine runs every step itself."
        items={delegates.map((a) => ({
          name: a.name,
          detail: a.description,
        }))}
        note="The assistant delegates to these on its own; you don't pick one."
      />
      <Capability
        title="Skills"
        status={skills.status}
        empty="No skills loaded."
        items={
          skills.status === "ok"
            ? skills.items.map((s) => ({
                name: s.name,
                detail: s.description,
                // "<built-in>" or the SKILL.md path — the answer to "is the
                // marketplace checkout actually being read?".
                source: s.location,
              }))
            : []
        }
        note="Matched by description when they apply; ask in plain language."
      />
      <Capability
        title="Commands"
        status={commands.status}
        empty="No commands."
        items={ownCommands.map((c) => ({ name: c.name, detail: c.description }))}
      />
    </div>
  );
}

function Capability({
  title,
  items,
  empty,
  note,
  status,
}: {
  title: string;
  items: { name: string; detail?: string; source?: string }[];
  empty: string;
  note?: string;
  status: "loading" | "ok" | "error";
}) {
  const loading = status === "loading";
  return (
    <div>
      <div className="flex items-baseline gap-2">
        <h4 className="text-2xs font-semibold uppercase tracking-wider text-fgsubtle">
          {title}
        </h4>
        {status === "ok" && (
          <span className="tabular-nums text-fgsubtle">{items.length}</span>
        )}
      </div>
      {note && <p className="mt-0.5 text-fgsubtle">{note}</p>}
      {status === "error" ? (
        <p className="mt-1 text-amber-600 dark:text-amber-400">
          Could not check — the engine did not answer.
        </p>
      ) : loading ? (
        <p className="mt-1 text-fgsubtle">Loading…</p>
      ) : items.length === 0 ? (
        <p className="mt-1 text-fgsubtle">{empty}</p>
      ) : (
        <ul className="mt-1 space-y-1">
          {items.map((item) => (
            <li key={item.name}>
              <span className="font-mono font-medium text-fg">{item.name}</span>
              {item.detail && (
                <span className="ml-1.5 text-fgmuted">
                  {item.detail.length > 140
                    ? `${item.detail.slice(0, 140)}…`
                    : item.detail}
                </span>
              )}
              {item.source && (
                <span
                  className="ml-1.5 block truncate text-xs text-fgsubtle"
                  title={item.source}
                >
                  {item.source}
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/**
 * One visual turn: a user prompt, or every assistant step that answered it.
 *
 * OpenCode starts a **new assistant message per step**, so a reply that calls
 * three tools arrives as four messages. Rendered one bubble each, a single
 * answer became a stack of cards that each repeated the "AI" label and its own
 * token footer — the transcript read as four separate replies rather than one
 * train of thought. Consecutive user messages are *not* merged: those are
 * separate things the person said.
 */
interface Turn {
  key: string;
  role: "user" | "assistant";
  messages: RenderedMessage[];
}

function groupTurns(messages: RenderedMessage[]): Turn[] {
  const turns: Turn[] = [];
  for (const message of messages) {
    const role = message.info.role === "user" ? "user" : "assistant";
    const open = turns[turns.length - 1];
    if (open && role === "assistant" && open.role === "assistant") {
      open.messages.push(message);
    } else {
      turns.push({ key: message.info.id, role, messages: [message] });
    }
  }
  return turns;
}

function assistantError(message: RenderedMessage) {
  return message.info.role === "assistant"
    ? (message.info as AssistantMessage).error
    : undefined;
}

function outputLimit(message: RenderedMessage) {
  return outputLimitInfo(message.info as AssistantMessage, message.parts);
}

/**
 * Says that a step stopped at the provider's output-token cap (#665).
 *
 * Not an error box: nothing failed, and `info.error` is unset. Amber because
 * the user has to do something (retry, or pick a model with more room), which
 * is the difference between this and a silent empty bubble.
 */
function OutputLimitNotice({ message }: { message: RenderedMessage }) {
  const limit = outputLimit(message);
  if (!limit) return null;
  const spent = limit.reasoning + limit.output;
  const budget = spent > 0 ? ` (${formatTokens(spent)} tokens)` : "";
  return (
    <div
      role="status"
      className="rounded border border-amber-500/40 bg-amber-500/10 px-2 py-1.5 text-xs text-amber-700 dark:text-amber-300"
    >
      {limit.kind === "empty" ? (
        <>
          <span className="font-semibold">No reply:</span> the model used its
          whole output budget{budget}
          {limit.reasoning > 0 && limit.output === 0 ? " thinking" : ""} and was
          cut off before answering. Send the message again, or pick another
          model.
        </>
      ) : (
        <>
          <span className="font-semibold">Cut off:</span> this reply reached the
          model&apos;s output limit{budget}, so it may be incomplete. Ask it to
          continue.
        </>
      )}
    </div>
  );
}

/**
 * True when two turns hold the same messages and the same parts.
 *
 * Needed because the transcript selector rebuilds its wrapper objects on every
 * state change — and a delta is a state change — so an identity check on the
 * prop would never match and the memo would never hit. The `info` and `part`
 * objects inside *are* stable (the reducer replaces only what changed), which
 * is what makes this comparison both cheap and effective.
 */
function sameTurn(
  a: { turn: Turn; settled: boolean },
  b: { turn: Turn; settled: boolean }
): boolean {
  if (a.settled !== b.settled) return false;
  const left = a.turn.messages;
  const right = b.turn.messages;
  if (left.length !== right.length) return false;
  for (let i = 0; i < left.length; i += 1) {
    if (left[i].info !== right[i].info) return false;
    const lp = left[i].parts;
    const rp = right[i].parts;
    if (lp.length !== rp.length) return false;
    for (let j = 0; j < lp.length; j += 1) {
      if (lp[j] !== rp[j]) return false;
    }
  }
  return true;
}

const TurnBubble = memo(function TurnBubble({
  turn,
  settled,
}: {
  turn: Turn;
  settled: boolean;
}) {
  const isUser = turn.role === "user";
  const shown = turn.messages.filter(
    (m) => hasRenderableContent(m) || assistantError(m) || outputLimit(m)
  );
  if (shown.length === 0) return null;

  const assistants = turn.messages
    .map((m) => m.info)
    .filter((info): info is AssistantMessage => info.role === "assistant");

  // A turn is rated through its last step: that is the message carrying the
  // answer, and the one whose model/agent produced it. Not rateable until it
  // has completed, nor when it ended in a real error (there is no reply to
  // judge, and the error box already says what happened). A user-stopped turn
  // is still rateable: what it got to say may well have been the answer.
  const final = assistants[assistants.length - 1];
  const rateable =
    settled &&
    final !== undefined &&
    final.time.completed !== undefined &&
    (!final.error || isAbortError(final.error));

  return (
    <div className={cn("flex flex-col", isUser ? "items-end" : "items-start")}>
      <span className="mb-1 px-1 text-2xs font-semibold uppercase tracking-wider text-fgsubtle">
        {isUser ? "You" : "AI"}
        {/* Which agent answered. In Auto mode this is the only place the
            routing decision shows, and a wrong pick has to be visible to be
            overridden. */}
        {!isUser && assistants[0]?.mode && (
          <span className="normal-case tracking-normal"> · {assistants[0].mode}</span>
        )}
      </span>
      <div
        className={cn(
          "max-w-[85%] space-y-2 rounded-lg px-4 py-3 text-sm",
          isUser
            ? "bg-pegasus-600 text-white"
            : "border border-line bg-surface text-fg"
        )}
      >
        {shown.map((message) => (
          <div key={message.info.id} className="space-y-2">
            <MessageLiveContext.Provider
              value={
                !settled &&
                !assistantError(message) &&
                !(
                  message.info.role === "assistant" &&
                  message.info.time.completed !== undefined
                )
              }
            >
              {message.parts.map((part) => (
                <PartView key={part.id} part={part} />
              ))}
            </MessageLiveContext.Provider>
            {/* A stop the user asked for is not a failure. OpenCode records it
                as MessageAbortedError, so without this a red "Error:" box is
                the reward for pressing the Stop button. */}
            <OutputLimitNotice message={message} />
            {assistantError(message) && isAbortError(assistantError(message)) && (
              <div className="text-xs text-fgsubtle">Stopped</div>
            )}
            {assistantError(message) && !isAbortError(assistantError(message)) && (
              <div className="flex items-start gap-1.5 rounded border border-rose-500/40 bg-rose-500/10 px-2 py-1.5 text-xs text-rose-600 dark:text-rose-400">
                <span className="font-semibold">Error:</span>
                <span className="[overflow-wrap:anywhere]">
                  {errorMessage(assistantError(message))}
                </span>
              </div>
            )}
          </div>
        ))}

        {/* One footer for the whole turn, summed across its steps. Per-step
            footers turned a three-tool answer into three token counts. */}
        {assistants.length > 0 && <UsageFooter messages={assistants} />}
      </div>

      {rateable && <ReplyRating message={final} />}
    </div>
  );
}, sameTurn);

/**
 * Per-message request chains, so two quick clicks on the same reply reach the
 * server in the order they were made. Without this, 👍 then un-rate could
 * land as un-rate then 👍, leaving the backend with the opposite of what the
 * thumb shows. Module-level because a rating outlives any one mount of the
 * bubble (switching conversation and back re-mounts it mid-request).
 */
const ratingQueue = new Map<string, Promise<unknown>>();
/** Bumped per click; a failure reverts only if no later click superseded it. */
const ratingSeq = new Map<string, number>();

function enqueueRating(messageID: string, run: () => Promise<unknown>) {
  const prev = ratingQueue.get(messageID) ?? Promise.resolve();
  const next = prev.catch(() => undefined).then(run);
  ratingQueue.set(messageID, next);
  void next
    .catch(() => undefined)
    .finally(() => {
      if (ratingQueue.get(messageID) === next) ratingQueue.delete(messageID);
    });
  return next;
}

/**
 * 👍/👎 on one finished reply.
 *
 * Optimistic: the thumb flips at once and the store copy keeps it across
 * reloads. A failed save puts back whatever was shown before the click and
 * says so briefly, rather than leaving a thumb lit that the server never saw.
 * Clicking the lit thumb again clears the rating.
 */
function ReplyRating({ message }: { message: AssistantMessage }) {
  const rating = useOpenCodeStore((s) => s.ratingByMessage[message.id]);
  const setRating = useOpenCodeStore((s) => s.setRating);
  const conversationID = useChatStore((s) => s.activeId);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (!failed) return;
    const timer = setTimeout(() => setFailed(false), 4000);
    return () => clearTimeout(timer);
  }, [failed]);

  const rate = (value: 1 | -1) => {
    const before = rating ?? 0;
    const next = before === value ? 0 : value;
    const seq = (ratingSeq.get(message.id) ?? 0) + 1;
    ratingSeq.set(message.id, seq);
    setFailed(false);
    setRating(message.id, next);

    const model =
      message.providerID && message.modelID
        ? `${message.providerID}/${message.modelID}`
        : message.modelID || undefined;
    enqueueRating(message.id, () =>
      api.setMessageFeedback({
        session_id: message.sessionID,
        message_id: message.id,
        conversation_id: conversationID || undefined,
        model,
        // `mode` is the agent that actually produced this reply, which can
        // differ from the picker's current value if it was switched since.
        agent: message.mode || undefined,
        rating: next,
      })
    ).catch(() => {
      if (ratingSeq.get(message.id) !== seq) return;
      setRating(message.id, before);
      setFailed(true);
    });
  };

  const btn = (active: boolean) =>
    cn(
      "rounded p-1 text-fgsubtle transition-colors hover:bg-muted hover:text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-pegasus-500",
      active && "bg-muted text-pegasus-600 dark:text-pegasus-300"
    );

  return (
    <div className="mt-1 flex items-center gap-1 px-1">
      <button
        type="button"
        onClick={() => rate(1)}
        title="Helpful"
        aria-label="Helpful"
        aria-pressed={rating === 1}
        className={btn(rating === 1)}
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3zM7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3" />
        </svg>
      </button>
      <button
        type="button"
        onClick={() => rate(-1)}
        title="Not helpful"
        aria-label="Not helpful"
        aria-pressed={rating === -1}
        className={btn(rating === -1)}
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M10 15v4a3 3 0 0 0 3 3l4-9V2H5.72a2 2 0 0 0-2 1.7l-1.38 9a2 2 0 0 0 2 2.3zm7-13h2.67A2.31 2.31 0 0 1 22 4v7a2.31 2.31 0 0 1-2.33 2H17" />
        </svg>
      </button>
      <span role="status" aria-live="polite" className="text-xs text-fgsubtle">
        {failed ? "Couldn't save rating" : ""}
      </span>
    </div>
  );
}

function UsageFooter({ messages }: { messages: AssistantMessage[] }) {
  const summary = formatTurnUsage(messages);
  if (!summary) return null;
  return (
    <div className="border-t border-line pt-1.5 text-xs text-fgmuted">
      {summary}
    </div>
  );
}

/**
 * Tokens, cost and wall-clock for a whole turn.
 *
 * Tokens are summed across steps, which is what the turn actually cost; the
 * elapsed time spans the first step's start to the last one's completion, so it
 * includes the gaps where a tool was running rather than only model time.
 */
function formatTurnUsage(messages: AssistantMessage[]): string | null {
  let tokens = 0;
  let cost = 0;
  for (const message of messages) {
    const { input, output, cache } = message.tokens;
    tokens += input + output + (cache?.read ?? 0) + (cache?.write ?? 0);
    cost += message.cost;
  }
  if (tokens === 0) return null;

  const parts = [`~${tokens.toLocaleString()} tokens`];
  if (messages.length > 1) {
    parts.push(`${messages.length.toLocaleString()} steps`);
  }
  if (cost > 0) parts.push(`$${cost.toFixed(4)}`);

  const started = messages[0].time.created;
  const finished = messages[messages.length - 1].time.completed;
  if (finished && finished > started) {
    parts.push(`${((finished - started) / 1000).toFixed(1)}s`);
  }

  const model = messages[messages.length - 1].modelID;
  if (model) parts.push(model);
  return parts.join(", ");
}

/**
 * What the model is doing right now, from the newest part of the reply.
 * Coarse on purpose: it is a status line, and the parts above carry the detail.
 */
function currentActivity(message: RenderedMessage): string {
  for (let i = message.parts.length - 1; i >= 0; i--) {
    const part = message.parts[i];
    if (part.type === "tool") {
      const st = part.state;
      if (st.status === "running") return `Running ${st.title || part.tool}`;
      if (st.status === "pending") return `Starting ${part.tool}`;
      // A finished tool means the model is deciding what to do with its output.
      return "Thinking";
    }
    if (part.type === "reasoning" && part.text.trim()) {
      return part.time?.end === undefined ? "Thinking" : "Working";
    }
    if (part.type === "text" && part.text.trim() && !part.synthetic) {
      return "Writing";
    }
  }
  return "Thinking";
}

function ThinkingIndicator({
  bare,
  activity,
  start,
}: {
  /** Nothing of the reply is on screen yet: show the full placeholder bubble. */
  bare: boolean;
  activity: string;
  start: number | undefined;
}) {
  const elapsed = useElapsed(start, true);
  const dots = (
    <span className="flex shrink-0 gap-1">
      {[0, 150, 300].map((d) => (
        <span
          key={d}
          className="inline-block h-1.5 w-1.5 animate-bounce rounded-full bg-fgsubtle"
          style={{ animationDelay: `${d}ms` }}
        />
      ))}
    </span>
  );
  const time =
    start !== undefined && elapsed >= 1000 ? (
      <span className="shrink-0 tabular-nums text-fgsubtle" aria-hidden>
        {formatElapsed(elapsed)}
      </span>
    ) : null;
  if (bare) {
    return (
      <div className="flex justify-start" role="status" aria-live="polite">
        <div className="mr-2 mt-1 shrink-0">
          <PegasusLogo size={24} />
        </div>
        <div className="flex items-center gap-2 rounded-lg border border-line bg-surface px-4 py-3 text-xs">
          <span className="text-fgmuted">{activity}</span>
          {dots}
          {time}
        </div>
      </div>
    );
  }
  // The reply has started: a slim line under it rather than a second bubble,
  // so it reads as the status of that reply.
  return (
    <div
      className="flex min-w-0 items-center gap-2 px-1 text-xs"
      role="status"
      aria-live="polite"
    >
      {dots}
      <span className="min-w-0 truncate text-fgmuted">{activity}</span>
      {time}
    </div>
  );
}

/**
 * One dialog for every permission OpenCode can ask about, now and later.
 *
 * The request names what it wants (`permission`), what it wants it for
 * (`patterns`, the command itself for bash) and what an "always" would allow
 * (`always`), so nothing here is per-tool and a new gated tool needs no new
 * component.
 */
function PermissionCard({
  request,
  onReply,
}: {
  request: PermissionRequest;
  onReply: (id: string, reply: oc.PermissionReply) => Promise<void>;
}) {
  const [busy, setBusy] = useState<oc.PermissionReply | null>(null);
  const decide = (reply: oc.PermissionReply) => {
    setBusy(reply);
    void onReply(request.id, reply);
  };
  const detail =
    (typeof request.metadata?.command === "string"
      ? request.metadata.command
      : null) ?? request.patterns.join("\n");
  const alwaysPattern = request.always?.[0];

  return (
    <div className="rounded-md border border-amber-500/50 bg-amber-500/10 p-2.5 text-xs">
      <div className="flex items-center gap-1.5 font-medium text-amber-700 dark:text-amber-400">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
          <line x1="12" y1="9" x2="12" y2="13" />
          <line x1="12" y1="17" x2="12.01" y2="17" />
        </svg>
        Allow {request.permission}?
      </div>
      {/* Full contrast, deliberately: this is the command being authorised, and
          it is the one string in the app that must not be a muted grey. */}
      {detail && (
        <pre className="mt-1.5 max-h-32 overflow-auto whitespace-pre-wrap rounded border border-line bg-surface px-2 py-1 font-mono text-fg [overflow-wrap:anywhere]">
          {detail}
        </pre>
      )}
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <button
          onClick={() => decide("once")}
          disabled={busy !== null}
          className="rounded-md bg-pegasus-600 px-3 py-1 font-medium text-white hover:bg-pegasus-700 disabled:opacity-50"
        >
          Allow once
        </button>
        {alwaysPattern && (
          <button
            onClick={() => decide("always")}
            disabled={busy !== null}
            title={`Allow ${alwaysPattern} for the rest of this session`}
            className="rounded-md border border-line px-3 py-1 font-medium text-fgmuted hover:bg-muted disabled:opacity-50"
          >
            Always allow <code className="font-mono">{alwaysPattern}</code>
          </button>
        )}
        <button
          onClick={() => decide("reject")}
          disabled={busy !== null}
          className="rounded-md border border-line px-3 py-1 font-medium text-fgmuted hover:bg-muted disabled:opacity-50"
        >
          Deny
        </button>
      </div>
    </div>
  );
}

/** The agent asking the user something mid-turn. Same shape for every tool. */
function QuestionCard({
  request,
  onAnswer,
  onReject,
}: {
  request: QuestionRequest;
  onAnswer: (id: string, answers: string[][]) => Promise<void>;
  onReject: (id: string) => Promise<void>;
}) {
  const [chosen, setChosen] = useState<string[][]>(
    request.questions.map(() => [])
  );
  // Free-text answers, for a question flagged `custom`. Without these a
  // custom question with no preset options could not be answered at all: the
  // only enabled control was Skip, which rejects the question and derails the
  // turn the agent was asking about.
  const [typed, setTyped] = useState<string[]>(request.questions.map(() => ""));
  const [busy, setBusy] = useState(false);

  const toggle = (index: number, label: string, multiple?: boolean) => {
    setChosen((current) =>
      current.map((answer, i) => {
        if (i !== index) return answer;
        if (!multiple) return [label];
        return answer.includes(label)
          ? answer.filter((l) => l !== label)
          : [...answer, label];
      })
    );
  };

  // An answer is whatever was picked, plus anything typed for a custom
  // question.
  const answers = chosen.map((picked, index) => {
    const extra = typed[index]?.trim();
    return extra ? [...picked, extra] : picked;
  });
  const complete =
    request.questions.length > 0 && answers.every((answer) => answer.length > 0);

  return (
    <div className="rounded-md border border-pegasus-400/60 bg-pegasus-50 p-2.5 text-xs dark:bg-pegasus-400/10">
      {request.questions.map((question, index) => (
        <div key={question.header || index} className="mb-2">
          <div className="font-medium text-fg">{question.question}</div>
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {question.options.map((option) => (
              <button
                key={option.label}
                onClick={() => toggle(index, option.label, question.multiple)}
                title={option.description}
                className={cn(
                  "rounded-md border px-2.5 py-1 text-left",
                  chosen[index]?.includes(option.label)
                    ? "border-pegasus-500 bg-pegasus-600 text-white"
                    : "border-line bg-surface text-fgmuted hover:border-pegasus-300 hover:text-fg"
                )}
              >
                {option.label}
              </button>
            ))}
          </div>
          {question.custom && (
            <input
              type="text"
              value={typed[index] ?? ""}
              onChange={(e) =>
                setTyped((current) =>
                  current.map((v, i) => (i === index ? e.target.value : v))
                )
              }
              placeholder="Or type your own answer"
              className="mt-1.5 w-full rounded border border-line px-2 py-1 text-xs text-fg focus:border-pegasus-500 focus:ring-pegasus-500"
            />
          )}
        </div>
      ))}
      <div className="flex items-center gap-2">
        <button
          onClick={() => {
            setBusy(true);
            void onAnswer(request.id, answers);
          }}
          disabled={busy || !complete}
          className="rounded-md bg-pegasus-600 px-3 py-1 font-medium text-white hover:bg-pegasus-700 disabled:opacity-50"
        >
          Answer
        </button>
        <button
          onClick={() => {
            setBusy(true);
            void onReject(request.id);
          }}
          disabled={busy}
          className="rounded-md border border-line px-3 py-1 font-medium text-fgmuted hover:bg-muted disabled:opacity-50"
        >
          Skip
        </button>
      </div>
    </div>
  );
}
