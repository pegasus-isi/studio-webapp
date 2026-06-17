"use client";

import { useChatStore, type ChatMsg } from "@/lib/stores/chat-store";
import { PegasusLogo } from "@/components/ui/pegasus-logo";
import { useEffect, useRef } from "react";
import { cn } from "@/lib/utils";

export function ChatPanel() {
  const messages = useChatStore((s) => s.messages);
  const isStreaming = useChatStore((s) => s.isStreaming);
  const bottomRef = useRef<HTMLDivElement>(null);

  // Show a "thinking" indicator while streaming but before the assistant's
  // first token (no assistant message yet — the model is reasoning / a tool
  // is running). Once content arrives, the last message is the assistant's.
  const last = messages[messages.length - 1];
  const showThinking = isStreaming && (!last || last.role === "user");

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, showThinking]);

  return (
    <div className="h-full overflow-auto p-4">
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
        {/* Main content */}
        <div className="prose prose-sm max-w-none whitespace-pre-wrap [overflow-wrap:anywhere] dark:prose-invert">
          {msg.content}
        </div>

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
