"use client";

import { ChatPanel } from "@/components/chat/chat-panel";
import { ChatInput } from "@/components/chat/chat-input";
import { useChatStore } from "@/lib/stores/chat-store";
import { useEffect } from "react";
import * as api from "@/lib/api/client";

interface ChatSidebarProps {
  onClose: () => void;
}

export function ChatSidebar({ onClose }: ChatSidebarProps) {
  const loadHistory = useChatStore((s) => s.loadHistory);

  useEffect(() => {
    api
      .getChatHistory()
      .then((data) => {
        const msgs = data.messages.map((m, i) => ({
          id: `hist-${i}`,
          role: m.role as "user" | "assistant",
          content: m.content,
          agentId: m.agent_id ?? undefined,
          createdAt: m.created_at ?? undefined,
        }));
        if (msgs.length > 0) loadHistory(msgs);
      })
      .catch(() => {});
  }, [loadHistory]);

  return (
    <div className="flex w-[400px] flex-col border-l border-line bg-surface">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-line px-4 py-3">
        <h2 className="text-sm font-semibold text-fg">Chat</h2>
        <button
          onClick={onClose}
          className="rounded p-1 text-fgsubtle hover:bg-muted hover:text-fgmuted"
          aria-label="Close chat"
        >
          <svg
            xmlns="http://www.w3.org/2000/svg"
            width="16"
            height="16"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <line x1="18" y1="6" x2="6" y2="18" />
            <line x1="6" y1="6" x2="18" y2="18" />
          </svg>
        </button>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-hidden">
        <ChatPanel />
      </div>

      {/* Input */}
      <ChatInput />
    </div>
  );
}
