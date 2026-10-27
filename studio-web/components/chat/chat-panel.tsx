"use client";

import {
  useChatStore,
  genId,
  type ChatMsg,
  type PendingApproval,
} from "@/lib/stores/chat-store";
import { PegasusLogo } from "@/components/ui/pegasus-logo";
import { usePanelStore } from "@/lib/stores/panel-store";
import { useConsumeChatStream } from "@/lib/hooks/use-chat-stream";
import { apiUrl } from "@/lib/urls";
import { ChatMarkdown } from "@/components/chat/markdown";
import * as api from "@/lib/api/client";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { cn } from "@/lib/utils";

// Openers phrased as a scientist would say them. Each maps to a skill the
// model already has; naming the skill is our problem, not theirs.
const CHAT_STARTERS = [
  "Build me a workflow that",
  "Why did my run fail",
  "Explain what this workflow does",
];

export function ChatPanel() {
  const messages = useChatStore((s) => s.messages);
  const setChatDraft = usePanelStore((s) => s.setChatDraft);
  const consumeStream = useConsumeChatStream();
  const setStreaming = useChatStore((s) => s.setStreaming);
  const dropLastAssistant = useChatStore((s) => s.dropLastAssistant);
  const isStreaming = useChatStore((s) => s.isStreaming);
  const activeId = useChatStore((s) => s.activeId);

  const reattached = useRef(false);

  // Reattach to a turn that is still running (#282).
  //
  // Studio's left nav is plain <a> links, so switching pages is a full page
  // load. Before the server-side broker that killed the answer mid-sentence;
  // now the turn keeps going without us, and this picks it back up.
  //
  // The stream replays from the turn's start, so the partial assistant message
  // restored from local storage has to go first — otherwise the replay appends
  // to it and the text appears twice.
  useEffect(() => {
    if (reattached.current) return;
    reattached.current = true;
    let cancelled = false;

    (async () => {
      try {
        const res = await fetch(apiUrl("chat/turn"));
        if (!res.ok) return;
        const turn = await res.json();
        if (cancelled || !turn.active) return;

        dropLastAssistant(turn.conversation_id ?? activeId);
        setStreaming(true);
        const events = await fetch(apiUrl("chat/turn/events"));
        if (cancelled || !events.ok || !events.body) return;
        await consumeStream(events.body, turn.conversation_id ?? activeId);
      } catch {
        /* nothing in flight, or the backend predates this — show history */
      } finally {
        if (!cancelled) setStreaming(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [consumeStream, setStreaming, dropLastAssistant, activeId]);
  const loadHistory = useChatStore((s) => s.loadHistory);
  const scrollRef = useRef<HTMLDivElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const msgRefs = useRef<Map<string, HTMLDivElement>>(new Map());
  const [isAtBottom, setIsAtBottom] = useState(true);
  const prevLenRef = useRef(messages.length);
  const prevStreamingRef = useRef(isStreaming);
  // Conversations we've already tried to hydrate from the server this session.
  const syncedRef = useRef<Set<string>>(new Set());

  // Hydrate an empty conversation from the DB (fresh device / cleared local
  // storage). Client localStorage is the live store, so only fetch when this
  // conversation has no local messages and we're not mid-stream — never clobber
  // live state. Server text becomes the source of truth for restored turns.
  const messagesCount = messages.length;
  useEffect(() => {
    if (isStreaming || messagesCount > 0 || syncedRef.current.has(activeId)) {
      return;
    }
    syncedRef.current.add(activeId);
    let cancelled = false;
    (async () => {
      try {
        const { messages: rows } = await api.getChatHistory(200, activeId);
        if (cancelled || rows.length === 0) return;
        const st = useChatStore.getState();
        if (
          st.activeId === activeId &&
          (st.messagesById[activeId]?.length ?? 0) === 0
        ) {
          loadHistory(
            rows.map((r) => ({
              id: genId(),
              role: r.role as ChatMsg["role"],
              content: r.content,
              agentId: r.agent_id ?? undefined,
              createdAt: r.created_at ?? undefined,
              serverId: r.id ?? undefined,
              feedback: (r.feedback ?? undefined) as ChatMsg["feedback"],
            }))
          );
        }
      } catch {
        /* offline / no history — keep the empty state */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [activeId, messagesCount, isStreaming, loadHistory]);

  // Show a "thinking" indicator while streaming but before the assistant's
  // first token (no assistant message yet — the model is reasoning / a tool
  // is running). Once content arrives, the last message is the assistant's.
  const last = messages[messages.length - 1];
  const showThinking = isStreaming && (!last || last.role === "user");

  // Keep isAtBottom in sync with manual scroll — respected at end-of-turn
  const handleScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    setIsAtBottom(atBottom);
  };

  // Instant bottom on mount / conversation switch (most recent visible)
  useLayoutEffect(() => {
    setIsAtBottom(true);
    bottomRef.current?.scrollIntoView({ behavior: "instant" as ScrollBehavior, block: "end" });
    prevLenRef.current = messages.length;
    prevStreamingRef.current = isStreaming;
  }, [activeId]);

  // Instant bottom when history hydrates after mount (iframe open / fresh
  // device). The [activeId] effect above fires on empty messages=[] (no
  // sentinel), so trigger on the 0→N transition instead; isAtBottom guard
  // respects a user who scrolled during the fetch.
  const hydratedRef = useRef<Set<string>>(new Set());
  useLayoutEffect(() => {
    if (messages.length === 0 || hydratedRef.current.has(activeId)) return;
    hydratedRef.current.add(activeId);
    if (!isAtBottom) return;
    bottomRef.current?.scrollIntoView({ behavior: "instant" as ScrollBehavior, block: "end" });
    // Layout can still shift after this (webfonts, markdown code blocks move
    // the sentinel): re-assert once settled. One-shot per conversation.
    let raf1 = 0;
    let raf2 = 0;
    raf1 = requestAnimationFrame(() => {
      raf2 = requestAnimationFrame(() => {
        bottomRef.current?.scrollIntoView({ behavior: "instant" as ScrollBehavior, block: "end" });
      });
    });
    return () => {
      cancelAnimationFrame(raf1);
      cancelAnimationFrame(raf2);
    };
  }, [activeId, messages.length, isAtBottom]);

  // Re-assert bottom when the viewport resizes (e.g. JupyterLab sidebar
  // dragged, changing the iframe size) — only while following.
  useEffect(() => {
    const onResize = () => {
      if (isAtBottom) {
        bottomRef.current?.scrollIntoView({ behavior: "instant" as ScrollBehavior, block: "end" });
      }
    };
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [isAtBottom]);

  // On user send — even if not at bottom, go to bottom for incoming assistant
  useEffect(() => {
    if (messages.length > prevLenRef.current) {
      const last = messages[messages.length - 1];
      if (last?.role === "user") {
        setIsAtBottom(true);
        bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
      }
    }
    prevLenRef.current = messages.length;
  }, [messages]);

  // On assistant finished — if still at bottom, scroll to top of new assistant bubble
  useEffect(() => {
    const wasStreaming = prevStreamingRef.current;
    const nowStreaming = isStreaming;
    prevStreamingRef.current = nowStreaming;
    if (wasStreaming && !nowStreaming && isAtBottom) {
      const last = messages[messages.length - 1];
      if (last?.role === "assistant") {
        msgRefs.current.get(last.id)?.scrollIntoView({ behavior: "smooth", block: "start" });
      }
    }
  }, [isStreaming, messages, isAtBottom]);

  return (
    <div ref={scrollRef} onScroll={handleScroll} className="h-full overflow-auto p-4">
      {messages.length === 0 ? (
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
            {/* Plain-language starters, not slash commands (#241, Phase 6).
                Slashes are a programmer idiom; a scientist shouldn't have to
                know one to begin. They still work, and clicking a starter
                sends ordinary prose — the model routes to the right skill. */}
            <div className="mt-4 space-y-1.5">
              {CHAT_STARTERS.map((starter) => (
                <button
                  key={starter}
                  onClick={() => setChatDraft(starter + " ")}
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
          {messages.map((msg) =>
            msg.role === "system" ? (
              <ContextDivider key={msg.id} label={msg.content} />
            ) : (
              <div
                key={msg.id}
                ref={(el) => {
                  if (el) msgRefs.current.set(msg.id, el);
                  else msgRefs.current.delete(msg.id);
                }}
              >
                <MessageBubble msg={msg} />
              </div>
            )
          )}
          {showThinking && <ThinkingIndicator />}
          <div ref={bottomRef} />
        </div>
      )}
    </div>
  );
}

/**
 * A context-switch marker (from the context chip). Rendered as a divider, not
 * a bubble: answers above it were given under the previous scope, and a
 * scroll-back should read that way. Display-only — never sent to the model.
 */
function ContextDivider({ label }: { label: string }) {
  return (
    <div className="flex items-center gap-3 py-1" role="separator">
      <div className="h-px flex-1 bg-line" />
      <span className="text-[10px] font-medium uppercase tracking-wider text-fgsubtle">
        {label}
      </span>
      <div className="h-px flex-1 bg-line" />
    </div>
  );
}

function ThinkingIndicator() {
  return (
    <div className="flex justify-start">
      <div className="mr-2 mt-1 shrink-0">
        <PegasusLogo size={24} />
      </div>
      <div className="flex items-center gap-1 rounded-lg border border-line bg-surface px-4 py-3">
        <span className="text-xs text-fgmuted">Thinking</span>
        <span className="flex gap-1">
          {[0, 150, 300].map((d) => (
            <span
              key={d}
              className="inline-block h-1.5 w-1.5 animate-bounce rounded-full bg-fgsubtle"
              style={{ animationDelay: `${d}ms` }}
            />
          ))}
        </span>
      </div>
    </div>
  );
}

function MessageBubble({ msg }: { msg: ChatMsg }) {
  const isUser = msg.role === "user";
  const meta = msg.meta;

  return (
    <div className={cn("flex flex-col", isUser ? "items-end" : "items-start")}>
      {/* Role label */}
      <span className="mb-1 px-1 text-[10px] font-semibold uppercase tracking-wider text-fgsubtle">
        {isUser ? "You" : "AI"}
      </span>
      <div
        className={cn(
          "max-w-[85%] rounded-lg px-4 py-3 text-sm",
          isUser
            ? "bg-pegasus-600 text-white"
            : "bg-surface border border-line text-fg"
        )}
      >
        {/* Main content: render assistant output as markdown (code blocks,
            lists, tables); keep user text verbatim so it isn't reformatted. */}
        {isUser ? (
          <div className="whitespace-pre-wrap [overflow-wrap:anywhere]">
            {msg.content}
          </div>
        ) : msg.content ? (
          <ChatMarkdown content={msg.content} />
        ) : null}

        {/* Transient turn notices (e.g. the tool-limit message). */}
        {msg.notices?.map((n, i) => (
          <div
            key={`${n.kind}-${i}`}
            role="status"
            className="mt-2 rounded border border-amber-500/50 bg-amber-500/10 px-2 py-1.5 text-xs text-amber-700 dark:text-amber-400"
          >
            <div className="whitespace-pre-wrap [overflow-wrap:anywhere]">
              {n.message}
            </div>
          </div>
        ))}

        {/* Transient error for this turn (shown, but never sent to the model). */}
        {msg.error && (
          <div className="mt-2 flex items-start gap-1.5 rounded border border-rose-500/40 bg-rose-500/10 px-2 py-1.5 text-xs text-rose-600 dark:text-rose-400">
            <span className="font-semibold">Error:</span>
            <span className="[overflow-wrap:anywhere]">{msg.error}</span>
          </div>
        )}

        {/* Pending shell-command approvals (SEC-4) */}
        {msg.pendingApprovals && msg.pendingApprovals.length > 0 && (
          <div className="mt-2 space-y-2 border-t border-line pt-2">
            {msg.pendingApprovals.map((p) => (
              <ApprovalCard key={p.id} req={p} />
            ))}
          </div>
        )}

        {/* Tool calls */}
        {msg.toolCalls && msg.toolCalls.length > 0 && (
          <div className="mt-2 space-y-1 border-t border-line pt-2">
            {msg.toolCalls.map((tc) => (
              <div
                key={tc.id}
                className="rounded border border-line bg-muted px-2 py-1 text-xs text-fgmuted"
              >
                <span className="font-mono font-medium text-fg">{tc.name}</span>
                <span className="ml-1">called</span>
              </div>
            ))}
          </div>
        )}

        {/* Tool results */}
        {msg.toolResults && msg.toolResults.length > 0 && (
          <div className="mt-2 space-y-1 border-t border-line pt-2">
            {msg.toolResults.map((tr) => (
              <details
                key={tr.id}
                className="rounded border border-line bg-muted text-xs"
              >
                <summary className="cursor-pointer px-2 py-1 text-fgmuted">
                  <span className="font-mono font-medium text-fg">
                    {tr.name}
                  </span>{" "}
                  result
                </summary>
                <pre className="max-h-40 overflow-auto px-2 pb-1 text-fgmuted">
                  {tr.result}
                </pre>
              </details>
            ))}
          </div>
        )}

        {/* Per-turn metadata footer (tokens, tool calls, duration) */}
        {!isUser && meta && (meta.tokens || meta.durationS) ? (
          <div className="mt-2 border-t border-line pt-1.5 text-xs text-fgmuted">
            {formatMeta(meta)}
          </div>
        ) : null}
      </div>

      {/* Usefulness feedback — assistant replies with real content only. */}
      {!isUser && msg.content && !msg.error && <FeedbackButtons msg={msg} />}
    </div>
  );
}

function ApprovalCard({ req }: { req: PendingApproval }) {
  const requestId = useChatStore((s) => s.requestId);
  const clearPermissionRequest = useChatStore((s) => s.clearPermissionRequest);
  const [busy, setBusy] = useState<null | "approve" | "deny">(null);
  const [error, setError] = useState<string | null>(null);

  const decide = async (decision: "approve" | "deny") => {
    if (busy || !requestId) return;
    setBusy(decision);
    setError(null);
    try {
      await api.sendChatPermission(requestId, req.id, decision);
      // Clear on denial now; an approval is also cleared by the follow-up
      // tool_call event, but clearing here keeps the UI snappy either way.
      clearPermissionRequest(req.id);
    } catch (e) {
      setError((e as Error).message || "Failed to send decision");
      setBusy(null);
    }
  };

  return (
    <div className="rounded-md border border-amber-500/50 bg-amber-500/10 p-2.5 text-xs">
      <div className="flex items-center gap-1.5 font-medium text-amber-700 dark:text-amber-400">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
          <line x1="12" y1="9" x2="12" y2="13" />
          <line x1="12" y1="17" x2="12.01" y2="17" />
        </svg>
        {req.kind === "submit"
          ? "Approve workflow submission?"
          : "Approve shell command?"}
      </div>
      {/* Full contrast, deliberately. This is the command you are about to
          authorise — the one string in the app that must not be rendered in a
          muted grey. */}
      {req.summary && (
        <pre className="mt-1.5 max-h-32 overflow-auto whitespace-pre-wrap rounded border border-line bg-surface px-2 py-1 font-mono text-fg [overflow-wrap:anywhere]">
          {req.summary}
        </pre>
      )}
      <div className="mt-2 flex items-center gap-2">
        <button
          onClick={() => decide("approve")}
          disabled={busy !== null || !requestId}
          className="rounded-md bg-pegasus-600 px-3 py-1 font-medium text-white hover:bg-pegasus-700 disabled:opacity-50"
        >
          {busy === "approve" ? "Approving…" : "Approve"}
        </button>
        <button
          onClick={() => decide("deny")}
          disabled={busy !== null || !requestId}
          className="rounded-md border border-line px-3 py-1 font-medium text-fgmuted hover:bg-muted disabled:opacity-50"
        >
          {busy === "deny" ? "Denying…" : "Deny"}
        </button>
        {error && (
          <span className="text-rose-600 dark:text-rose-400">{error}</span>
        )}
      </div>
    </div>
  );
}

function FeedbackButtons({ msg }: { msg: ChatMsg }) {
  const setMessageFeedback = useChatStore((s) => s.setMessageFeedback);
  // Compare before the guard: the early return below narrows msg.feedback to
  // 0 | undefined, which would make === 1 / === -1 a type error.
  const isUseful = msg.feedback === 1;
  const isNotUseful = msg.feedback === -1;
  // Feedback needs the persisted DB id; a still-streaming reply doesn't have
  // one yet, so hold the controls until it arrives. Once rated, hide forever
  // (one-shot votes, incl. hydrated) — no undo, no re-vote.
  if (msg.serverId == null || msg.feedback) return null;

  const rate = (value: 1 | -1) => {
    if (msg.feedback) return; // one-shot: no undo, no re-vote
    setMessageFeedback(msg.id, value); // optimistic
    api.setChatFeedback(msg.serverId as number, value).catch(() => {
      setMessageFeedback(msg.id, 0); // revert + buttons reappear on failure
    });
  };

  const btn = (active: boolean) =>
    cn(
      "rounded p-1 text-fgsubtle transition-colors hover:bg-muted hover:text-fg",
      active && "bg-muted text-pegasus-600 dark:text-pegasus-300"
    );

  return (
    <div className="mt-1 flex items-center gap-1 px-1">
      <button
        onClick={() => rate(1)}
        title="Useful"
        aria-label="Mark reply useful"
        aria-pressed={isUseful}
        className={btn(isUseful)}
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3zM7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3" />
        </svg>
      </button>
      <button
        onClick={() => rate(-1)}
        title="Not useful"
        aria-label="Mark reply not useful"
        aria-pressed={isNotUseful}
        className={btn(isNotUseful)}
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M10 15v4a3 3 0 0 0 3 3l4-9V2H5.72a2 2 0 0 0-2 1.7l-1.38 9a2 2 0 0 0 2 2.3zm7-13h2.67A2.31 2.31 0 0 1 22 4v7a2.31 2.31 0 0 1-2.33 2H17" />
        </svg>
      </button>
    </div>
  );
}

function formatMeta(meta: NonNullable<ChatMsg["meta"]>): string {
  const parts: string[] = [];
  if (meta.tokens) parts.push(`~${meta.tokens.toLocaleString()} tokens`);
  if (meta.toolCalls)
    parts.push(`${meta.toolCalls} tool call${meta.toolCalls === 1 ? "" : "s"}`);
  if (meta.durationS != null) parts.push(`${meta.durationS}s`);
  return parts.join(", ");
}
