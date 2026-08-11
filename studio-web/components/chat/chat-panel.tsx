"use client";

import { useChatStore, genId, type ChatMsg } from "@/lib/stores/chat-store";
import { PegasusLogo } from "@/components/ui/pegasus-logo";
import { ChatMarkdown } from "@/components/chat/markdown";
import * as api from "@/lib/api/client";
import { useEffect, useRef, useState } from "react";
import { cn } from "@/lib/utils";

export function ChatPanel() {
  const messages = useChatStore((s) => s.messages);
  const isStreaming = useChatStore((s) => s.isStreaming);
  const activeId = useChatStore((s) => s.activeId);
  const loadHistory = useChatStore((s) => s.loadHistory);
  const scrollRef = useRef<HTMLDivElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
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

  // Only auto-scroll when the user is already near the bottom, so a token burst
  // doesn't yank them away while they're reading earlier output.
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const nearBottom =
      el.scrollHeight - el.scrollTop - el.clientHeight < 120;
    if (nearBottom) {
      bottomRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }, [messages, showThinking]);

  return (
    <div ref={scrollRef} className="h-full overflow-auto p-4">
      {messages.length === 0 ? (
        <div className="flex h-full items-center justify-center">
          <div className="text-center text-fgsubtle">
            <div className="mb-3 flex justify-center">
              <PegasusLogo size={48} />
            </div>
            <p className="text-lg">Welcome to PegasusAI Chat</p>
            <p className="mt-1 text-sm">
              Ask me to create, debug, or review Pegasus workflows.
            </p>
            <p className="mt-1 text-sm">
              Try: <code className="rounded bg-muted px-1">/scaffold</code>,{" "}
              <code className="rounded bg-muted px-1">/debug</code>,{" "}
              <code className="rounded bg-muted px-1">/review</code>
            </p>
          </div>
        </div>
      ) : (
        <div className="mx-auto max-w-3xl space-y-4">
          {messages.map((msg) => (
            <MessageBubble key={msg.id} msg={msg} />
          ))}
          {showThinking && <ThinkingIndicator />}
          <div ref={bottomRef} />
        </div>
      )}
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
                className="rounded bg-base px-2 py-1 text-xs text-fgmuted"
              >
                <span className="font-mono font-medium">{tc.name}</span>
                <span className="ml-1 text-fgsubtle">called</span>
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
                className="rounded bg-base text-xs"
              >
                <summary className="cursor-pointer px-2 py-1 text-fgmuted">
                  <span className="font-mono font-medium">{tr.name}</span>{" "}
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
          <div className="mt-2 border-t border-line pt-1.5 text-xs italic text-fgsubtle">
            {formatMeta(meta)}
          </div>
        ) : null}
      </div>

      {/* Usefulness feedback — assistant replies with real content only. */}
      {!isUser && msg.content && !msg.error && <FeedbackButtons msg={msg} />}
    </div>
  );
}

function ApprovalCard({
  req,
}: {
  req: { id: string; name: string; summary: string };
}) {
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
        Approve shell command?
      </div>
      {req.summary && (
        <pre className="mt-1.5 max-h-32 overflow-auto whitespace-pre-wrap rounded bg-base px-2 py-1 font-mono text-fgmuted [overflow-wrap:anywhere]">
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
  // Feedback needs the persisted DB id; a still-streaming reply doesn't have
  // one yet, so hold the controls until it arrives.
  if (msg.serverId == null) return null;

  const rate = (value: 1 | -1) => {
    const next = msg.feedback === value ? 0 : value;
    setMessageFeedback(msg.id, next); // optimistic
    api.setChatFeedback(msg.serverId as number, next).catch(() => {
      setMessageFeedback(msg.id, msg.feedback ?? 0); // revert on failure
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
        aria-pressed={msg.feedback === 1}
        className={btn(msg.feedback === 1)}
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3zM7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3" />
        </svg>
      </button>
      <button
        onClick={() => rate(-1)}
        title="Not useful"
        aria-label="Mark reply not useful"
        aria-pressed={msg.feedback === -1}
        className={btn(msg.feedback === -1)}
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
