"use client";

import { OpenCodeChat } from "@/components/chat/opencode-chat";
import { ConversationBar } from "@/components/chat/conversation-bar";
import { PegasusLogo } from "@/components/ui/pegasus-logo";
import { ResizablePane } from "@/components/layout/resizable-pane";

interface ChatSidebarProps {
  onClose: () => void;
}

export function ChatSidebar({ onClose }: ChatSidebarProps) {
  return (
    // Drag the left edge to resize. A fixed 400px was too narrow to read a
    // tool result in and too wide when you only wanted the transcript beside
    // a graph. `collapsible={false}` because the header already has a close
    // button, and `scroll={false}` because the transcript scrolls itself
    // under a pinned composer.
    <ResizablePane
      storageKey="chat"
      side="right"
      collapsible={false}
      scroll={false}
      label="the chat"
      defaultWidth={400}
      minWidth={320}
      maxWidth={860}
    >
    <div className="flex h-full flex-col bg-surface">
      {/* The header used to carry pt-14 to clear a user-menu pill that floated
          over the top-right of the page. That menu moved into the nav in #276
          and onto the rail in #314, so the padding was holding open 56px of
          nothing. */}
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

      {/* Transcript and composer, from whichever engine is selected (#367) */}
      <OpenCodeChat />
    </div>
    </ResizablePane>
  );
}
