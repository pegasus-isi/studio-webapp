"use client";

import { ChatPanel } from "@/components/chat/chat-panel";
import { ChatInput } from "@/components/chat/chat-input";
import { ConversationBar } from "@/components/chat/conversation-bar";
import { PegasusLogo } from "@/components/ui/pegasus-logo";

interface ChatSidebarProps {
  onClose: () => void;
}

export function ChatSidebar({ onClose }: ChatSidebarProps) {
  return (
    <div className="flex w-[400px] flex-col border-l border-line bg-surface">
      {/* Header */}
      <div className="flex items-center justify-between gap-2 border-b border-line px-3 py-2.5">
        <h2 className="flex shrink-0 items-center gap-1.5 text-sm font-semibold text-fg">
          <PegasusLogo size={20} />
          <span className="hidden sm:inline">PegasusAI</span>
        </h2>
        <ConversationBar />
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
