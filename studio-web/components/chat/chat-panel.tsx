"use client";

import { useChatStore, type ChatMsg } from "@/lib/stores/chat-store";
import { useEffect, useRef } from "react";
import { cn } from "@/lib/utils";

export function ChatPanel() {
  const messages = useChatStore((s) => s.messages);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  return (
    <div className="h-full overflow-auto p-4">
      {messages.length === 0 ? (
        <div className="flex h-full items-center justify-center">
          <div className="text-center text-gray-400">
            <p className="text-lg">Welcome to PegasusAI Chat</p>
            <p className="mt-1 text-sm">
              Ask me to create, debug, or review Pegasus workflows.
            </p>
            <p className="mt-1 text-sm">
              Try: <code className="rounded bg-gray-100 px-1">/scaffold</code>,{" "}
              <code className="rounded bg-gray-100 px-1">/debug</code>,{" "}
              <code className="rounded bg-gray-100 px-1">/review</code>
            </p>
          </div>
        </div>
      ) : (
        <div className="mx-auto max-w-3xl space-y-4">
          {messages.map((msg) => (
            <MessageBubble key={msg.id} msg={msg} />
          ))}
          <div ref={bottomRef} />
        </div>
      )}
    </div>
  );
}

function MessageBubble({ msg }: { msg: ChatMsg }) {
  const isUser = msg.role === "user";

  return (
    <div className={cn("flex", isUser ? "justify-end" : "justify-start")}>
      <div
        className={cn(
          "max-w-[80%] rounded-lg px-4 py-3 text-sm",
          isUser
            ? "bg-pegasus-600 text-white"
            : "bg-white border border-gray-200 text-gray-800"
        )}
      >
        {/* Main content */}
        <div className="prose prose-sm max-w-none whitespace-pre-wrap">
          {msg.content}
        </div>

        {/* Tool calls */}
        {msg.toolCalls && msg.toolCalls.length > 0 && (
          <div className="mt-2 space-y-1 border-t border-gray-100 pt-2">
            {msg.toolCalls.map((tc) => (
              <div
                key={tc.id}
                className="rounded bg-gray-50 px-2 py-1 text-xs text-gray-600"
              >
                <span className="font-mono font-medium">{tc.name}</span>
                <span className="ml-1 text-gray-400">called</span>
              </div>
            ))}
          </div>
        )}

        {/* Tool results */}
        {msg.toolResults && msg.toolResults.length > 0 && (
          <div className="mt-2 space-y-1 border-t border-gray-100 pt-2">
            {msg.toolResults.map((tr) => (
              <details
                key={tr.id}
                className="rounded bg-gray-50 text-xs"
              >
                <summary className="cursor-pointer px-2 py-1 text-gray-600">
                  <span className="font-mono font-medium">{tr.name}</span>{" "}
                  result
                </summary>
                <pre className="max-h-40 overflow-auto px-2 pb-1 text-gray-500">
                  {tr.result}
                </pre>
              </details>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
