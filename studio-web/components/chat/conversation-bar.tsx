"use client";

import { useEffect, useRef, useState } from "react";
import { useChatStore } from "@/lib/stores/chat-store";

/**
 * Conversation controls for the chat header: a "New" button plus a dropdown
 * to switch / rename / delete saved conversations (client-side, persisted).
 */
export function ConversationBar() {
  const conversations = useChatStore((s) => s.conversations);
  const activeId = useChatStore((s) => s.activeId);
  const newConversation = useChatStore((s) => s.newConversation);
  const switchConversation = useChatStore((s) => s.switchConversation);
  const renameConversation = useChatStore((s) => s.renameConversation);
  const deleteConversation = useChatStore((s) => s.deleteConversation);

  const [open, setOpen] = useState(false);
  const [mounted, setMounted] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => setMounted(true), []);
  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    if (open) {
      document.addEventListener("mousedown", onClick);
      return () => document.removeEventListener("mousedown", onClick);
    }
  }, [open]);

  // Avoid SSR/localStorage hydration mismatch.
  const active = mounted
    ? conversations.find((c) => c.id === activeId)
    : undefined;

  return (
    <div ref={ref} className="relative flex items-center gap-1">
      <button
        onClick={() => setOpen((v) => !v)}
        title="Conversations"
        className="flex max-w-[150px] items-center gap-1 rounded px-2 py-1 text-xs text-fgmuted hover:bg-muted hover:text-fg"
      >
        <span className="truncate">{active?.title ?? "Chat"}</span>
        <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <polyline points="6 9 12 15 18 9" />
        </svg>
      </button>
      <button
        onClick={() => newConversation()}
        title="New chat"
        className="rounded px-1.5 py-1 text-fgmuted hover:bg-muted hover:text-fg"
      >
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
          <line x1="12" y1="5" x2="12" y2="19" />
          <line x1="5" y1="12" x2="19" y2="12" />
        </svg>
      </button>

      {open && mounted && (
        <div className="absolute left-0 top-8 z-50 max-h-80 w-64 overflow-auto rounded-lg border border-line bg-surface py-1 shadow-lg">
          <button
            onClick={() => {
              newConversation();
              setOpen(false);
            }}
            className="flex w-full items-center gap-2 border-b border-line px-3 py-2 text-xs font-medium text-pegasus-700 hover:bg-muted dark:text-pegasus-300"
          >
            + New chat
          </button>
          {conversations.map((c) => (
            <div
              key={c.id}
              className={
                "group flex items-center gap-1 px-2 py-1.5 text-xs " +
                (c.id === activeId ? "bg-muted" : "hover:bg-muted")
              }
            >
              <button
                onClick={() => {
                  switchConversation(c.id);
                  setOpen(false);
                }}
                className="flex-1 truncate text-left text-fg"
                title={c.title}
              >
                {c.title || "Untitled"}
              </button>
              <button
                onClick={() => {
                  const t = window.prompt("Rename conversation", c.title);
                  if (t !== null) renameConversation(c.id, t.trim());
                }}
                title="Rename"
                className="rounded p-0.5 text-fgsubtle opacity-0 group-hover:opacity-100 hover:text-fg"
              >
                ✎
              </button>
              <button
                onClick={() => deleteConversation(c.id)}
                title="Delete"
                className="rounded p-0.5 text-fgsubtle opacity-0 group-hover:opacity-100 hover:text-rose-500"
              >
                ✕
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
