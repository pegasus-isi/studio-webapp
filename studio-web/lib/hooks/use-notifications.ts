"use client";

import { useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import * as api from "@/lib/api/client";
import { apiUrl } from "@/lib/urls";
import { useNotificationStore } from "@/lib/stores/notification-store";

/**
 * Reads notification state from the store and exposes mutating actions that
 * optimistically update the store, then persist to the API. Components render
 * from the store (kept live by `useNotificationStream`), not from react-query.
 */
export function useNotifications() {
  const notifications = useNotificationStore((s) => s.notifications);
  const unread = useNotificationStore((s) => s.unread);
  const markReadLocal = useNotificationStore((s) => s.markRead);
  const markAllReadLocal = useNotificationStore((s) => s.markAllRead);
  const removeLocal = useNotificationStore((s) => s.remove);
  const clearLocal = useNotificationStore((s) => s.clear);

  const markRead = (id: string) => {
    markReadLocal(id);
    api.markNotificationRead(id).catch(() => {});
  };
  const markAllRead = () => {
    markAllReadLocal();
    api.markAllNotificationsRead().catch(() => {});
  };
  const remove = (id: string) => {
    removeLocal(id);
    api.deleteNotification(id).catch(() => {});
  };
  const clear = () => {
    clearLocal();
    api.clearNotifications().catch(() => {});
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

  useEffect(() => {
    if (typeof window === "undefined" || typeof EventSource === "undefined") {
      return;
    }
    const es = new EventSource(apiUrl("notifications/stream"));
    es.onmessage = (e) => {
      if (!e.data || e.data.startsWith(":")) return;
      try {
        const n = JSON.parse(e.data) as api.Notification;
        receive(n);
      } catch {
        /* ignore malformed frames */
      }
    };
    // EventSource auto-reconnects on error; nothing to do but let it.
    return () => es.close();
  }, [receive]);
}
