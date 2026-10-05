"use client";

import { useEffect } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import * as api from "@/lib/api/client";
import { apiUrl } from "@/lib/urls";
import { useNotificationStore } from "@/lib/stores/notification-store";
import { FEEDBACK_PENDING_KEY } from "@/lib/feedback/feedback";

/**
 * Reads notification state from the store and exposes mutating actions that
 * optimistically update the store, then persist to the API. Components render
 * from the store (kept live by `useNotificationStream`), not from react-query.
 */
export function useNotifications() {
  const qc = useQueryClient();
  const notifications = useNotificationStore((s) => s.notifications);
  const unread = useNotificationStore((s) => s.unread);
  const markReadLocal = useNotificationStore((s) => s.markRead);
  const markAllReadLocal = useNotificationStore((s) => s.markAllRead);
  const removeLocal = useNotificationStore((s) => s.remove);
  const clearLocal = useNotificationStore((s) => s.clear);

  // On a failed write, resync from the server instead of eating the error and
  // leaving the store diverged (FE-9): invalidating the query refetches
  // history, and the effect below re-runs setAll with the server's truth.
  const resync = () => qc.invalidateQueries({ queryKey: ["notifications"] });

  const markRead = (id: string) => {
    markReadLocal(id);
    api.markNotificationRead(id).catch(resync);
  };
  const markAllRead = () => {
    markAllReadLocal();
    api.markAllNotificationsRead().catch(resync);
  };
  const remove = (id: string) => {
    removeLocal(id);
    api.deleteNotification(id).catch(resync);
  };
  const clear = () => {
    clearLocal();
    api.clearNotifications().catch(resync);
  };

  return { notifications, unread, markRead, markAllRead, remove, clear };
}

/**
 * Mounts once in the studio shell: loads history, then keeps the store live via
 * an SSE connection. Reconnects automatically (EventSource does this), and
 * refetches history on reconnect to catch anything missed while disconnected.
 */
export function useNotificationStream() {
  const setAll = useNotificationStore((s) => s.setAll);
  const receive = useNotificationStore((s) => s.receive);

  // Initial history (and a slow poll as a fallback when SSE is unavailable).
  const { data } = useQuery({
    queryKey: ["notifications"],
    queryFn: () => api.getNotifications(),
    refetchInterval: 60_000,
  });

  useEffect(() => {
    if (data) setAll(data.notifications, data.unread);
  }, [data, setAll]);

  const qc = useQueryClient();

  useEffect(() => {
    if (typeof window === "undefined" || typeof EventSource === "undefined") {
      return;
    }

    let cancelled = false;
    let es: EventSource | null = null;
    let attempts = 0;
    let reconnectTimer: ReturnType<typeof setTimeout> | undefined;

    const connect = () => {
      if (cancelled) return;
      es = new EventSource(apiUrl("notifications/stream"));

      es.onopen = () => {
        attempts = 0;
        // Refetch history on (re)connect to catch anything missed while down.
        qc.invalidateQueries({ queryKey: ["notifications"] });
      };

      es.onmessage = (e) => {
        if (!e.data || e.data.startsWith(":")) return;
        let n: api.Notification;
        try {
          n = JSON.parse(e.data) as api.Notification;
        } catch {
          return; /* ignore malformed frames */
        }
        // A feedback prompt became due. It is a prompt, not a notification:
        // it never enters the bell, it only tells this tab to ask the server
        // whether it may show one (the per-session rule lives there).
        if (n.kind === "feedback_prompt") {
          qc.invalidateQueries({ queryKey: FEEDBACK_PENDING_KEY });
          return;
        }
        receive(n);
      };

      es.onerror = () => {
        // EventSource only auto-retries when the connection drops mid-stream —
        // a non-2xx (e.g. a gateway 502) puts it in CLOSED and it never
        // reconnects, killing live notifications until reload (FE-9). Drive the
        // reconnect ourselves with capped backoff.
        es?.close();
        if (cancelled) return;
        const delay = Math.min(1000 * 2 ** attempts, 30000);
        attempts += 1;
        reconnectTimer = setTimeout(connect, delay);
      };
    };

    connect();

    return () => {
      cancelled = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      es?.close();
    };
  }, [receive, qc]);
}
