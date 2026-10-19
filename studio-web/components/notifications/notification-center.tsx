"use client";

import { useEffect, useRef, useState } from "react";
import { appRelativePath } from "@/lib/urls";
import { useNotifications } from "@/lib/hooks/use-notifications";
import type { Notification, NotificationSeverity } from "@/lib/api/client";
import {
  severityAccent,
  SeverityIcon,
  relativeTime,
} from "@/components/notifications/shared";

/**
 * Bell button with unread badge + dropdown panel listing recent notifications.
 * Lives in the top-right shell next to the user menu.
 */
export function NotificationCenter() {
  const { notifications, unread, markRead, markAllRead, remove, clear } =
    useNotifications();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    if (open) {
      document.addEventListener("mousedown", handleClick);
      return () => document.removeEventListener("mousedown", handleClick);
    }
  }, [open]);

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen((v) => !v)}
        className="relative flex h-9 w-9 items-center justify-center rounded-full border border-line bg-surface text-fgmuted shadow-sm transition-colors hover:bg-muted hover:text-fg"
        title="Notifications"
        aria-label={`Notifications${unread ? ` (${unread} unread)` : ""}`}
      >
        <svg
          width="16"
          height="16"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9" />
          <path d="M13.73 21a2 2 0 0 1-3.46 0" />
        </svg>
        {unread > 0 && (
          <span className="absolute -right-0.5 -top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-rose-500 px-1 text-2xs font-semibold text-white">
            {unread > 99 ? "99+" : unread}
          </span>
        )}
      </button>

      {/* Opens upward and rightward: the trigger now sits in the sidebar
          footer (#276), so there is nothing below or to the left of it. */}
      {open && (
        <div className="absolute bottom-full left-0 z-50 mb-1.5 w-96 overflow-hidden rounded-lg border border-line bg-surface shadow-lg">
          <div className="flex items-center justify-between border-b border-line px-4 py-2.5">
            <p className="text-sm font-medium text-fg">Notifications</p>
            <div className="flex items-center gap-2 text-xs">
              {unread > 0 && (
                <button
                  onClick={markAllRead}
                  className="text-fgmuted hover:text-fg"
                >
                  Mark all read
                </button>
              )}
              {notifications.length > 0 && (
                <button onClick={clear} className="text-fgmuted hover:text-fg">
                  Clear
                </button>
              )}
            </div>
          </div>

          <div className="max-h-[28rem] overflow-y-auto">
            {notifications.length === 0 ? (
              <p className="px-4 py-8 text-center text-sm text-fgsubtle">
                You&rsquo;re all caught up.
              </p>
            ) : (
              notifications.map((n) => (
                <NotificationRow
                  key={n.id}
                  n={n}
                  onRead={markRead}
                  onRemove={remove}
                  onNavigate={() => setOpen(false)}
                />
              ))
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function NotificationRow({
  n,
  onRead,
  onRemove,
  onNavigate,
}: {
  n: Notification;
  onRead: (id: string) => void;
  onRemove: (id: string) => void;
  onNavigate: () => void;
}) {
  const severity = n.severity as NotificationSeverity;
  const Wrapper = n.link ? "a" : "div";
  const wrapperProps = n.link
    ? { href: appRelativePath(n.link) }
    : {};

  return (
    <Wrapper
      {...wrapperProps}
      onClick={() => {
        if (!n.read) onRead(n.id);
        if (n.link) onNavigate();
      }}
      className={`group flex gap-3 border-b border-line px-4 py-3 transition-colors last:border-b-0 hover:bg-muted ${
        n.link ? "cursor-pointer" : ""
      } ${n.read ? "" : "bg-accent/5"}`}
    >
      <span className={`mt-0.5 shrink-0 ${severityAccent(severity)}`}>
        <SeverityIcon severity={severity} />
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex items-start justify-between gap-2">
          <p className="text-sm font-medium text-fg">{n.title}</p>
          {!n.read && (
            <span className="mt-1 h-1.5 w-1.5 shrink-0 rounded-full bg-rose-500" />
          )}
        </div>
        {n.body && (
          <p className="mt-0.5 text-xs text-fgmuted">{n.body}</p>
        )}
        <p className="mt-1 text-xs text-fgsubtle">
          {relativeTime(n.created_at)}
        </p>
      </div>
      <button
        onClick={(e) => {
          e.preventDefault();
          e.stopPropagation();
          onRemove(n.id);
        }}
        className="shrink-0 self-start text-fgsubtle opacity-0 transition-opacity hover:text-fg group-hover:opacity-100"
        title="Dismiss"
        aria-label="Dismiss notification"
      >
        <svg
          width="14"
          height="14"
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
    </Wrapper>
  );
}
