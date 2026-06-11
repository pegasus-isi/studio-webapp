"use client";

import { usePanelStore } from "@/lib/stores/panel-store";
import { ChatSidebar } from "@/components/chat/chat-sidebar";

export function GlobalChatPanel() {
  const chatOpen = usePanelStore((s) => s.chatOpen);
  const setChatOpen = usePanelStore((s) => s.setChatOpen);

  if (!chatOpen) return null;

  return <ChatSidebar onClose={() => setChatOpen(false)} />;
}
