"use client";

import { useEffect } from "react";
import { appRelativePath } from "@/lib/urls";
import { useNotificationStore } from "@/lib/stores/notification-store";
import { useNotificationStream } from "@/lib/hooks/use-notifications";
import { useFeedbackPromptSync } from "@/lib/feedback/feedback";
import type { Notification, NotificationSeverity } from "@/lib/api/client";
import {
  severityAccent,
  severityBorder,
  SeverityIcon,
} from "@/components/notifications/shared";

const TOAST_TTL_MS = 6000;

/**
 * Renders transient toast pop-ups for notifications pushed into the store by
 * the SSE stream. Each toast auto-dismisses after a few seconds. Mounted once
 * in the studio shell.
 */
export function ToastContainer() {
  // Single mount point for the live feed: loads history + opens the SSE stream
  // that feeds both these toasts and the bell dropdown.
  useNotificationStream();
  // And the feedback prompt it pokes about — mounted here for the same
  // reason: once per studio tab, never in the JupyterLab chat iframe.
  useFeedbackPromptSync();

  const toasts = useNotificationStore((s) => s.toasts);
  const dismissToast = useNotificationStore((s) => s.dismissToast);

  return (
    <div className="pointer-events-none fixed bottom-10 right-4 z-[60] flex w-80 flex-col gap-2">
      {toasts.map((t) => (
        <Toast key={t.id} n={t} onDismiss={() => dismissToast(t.id)} />
      ))}
    </div>
  );
}

function Toast({
  n,
  onDismiss,
}: {
  n: Notification;
  onDismiss: () => void;
}) {
  const severity = n.severity as NotificationSeverity;

  useEffect(() => {
    const id = setTimeout(onDismiss, TOAST_TTL_MS);
    return () => clearTimeout(id);
  }, [onDismiss]);

  const Wrapper = n.link ? "a" : "div";
  const wrapperProps = n.link ? { href: appRelativePath(n.link) } : {};

  return (
    <Wrapper
      {...wrapperProps}
      className={`pointer-events-auto flex gap-2.5 rounded-md border border-line border-l-4 bg-surface p-3 shadow-lg ${severityBorder(
        severity
      )} ${n.link ? "cursor-pointer hover:bg-muted" : ""}`}
      role="status"
    >
      <span className={`mt-0.5 shrink-0 ${severityAccent(severity)}`}>
        <SeverityIcon severity={severity} />
      </span>
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium text-fg">{n.title}</p>
        {n.body && <p className="mt-0.5 text-xs text-fgmuted">{n.body}</p>}
      </div>
      <button
        onClick={(e) => {
          e.preventDefault();
          e.stopPropagation();
          onDismiss();
        }}
        className="shrink-0 self-start text-fgsubtle hover:text-fg"
        aria-label="Dismiss"
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
