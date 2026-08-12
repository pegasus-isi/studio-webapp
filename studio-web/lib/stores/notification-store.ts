/**
 * Zustand store for notifications. Holds the merged feed (history loaded from
 * the API + live events pushed over SSE), the unread count, and the queue of
 * transient toasts. The bell dropdown reads `notifications`/`unread`; the toast
 * container reads `toasts`.
 */

import { create } from "zustand";
import type { Notification } from "@/lib/api/client";

interface NotificationStore {
  notifications: Notification[];
  unread: number;
  toasts: Notification[]; // currently-visible transient pop-ups

  /** Replace the whole feed (initial load / refetch). */
  setAll: (notifications: Notification[], unread: number) => void;
  /** Add (or refresh) one notification arriving live; also shows a toast. */
  receive: (n: Notification) => void;
  /** Show a transient toast for a client-side action (FE-6). Ephemeral: it
   *  does NOT enter the persisted feed or the unread count. */
  pushToast: (t: {
    title: string;
    body?: string;
    severity?: Notification["severity"];
  }) => void;
  markRead: (id: string) => void;
  markAllRead: () => void;
  remove: (id: string) => void;
  clear: () => void;
  dismissToast: (id: string) => void;
}

export const useNotificationStore = create<NotificationStore>((set) => ({
  notifications: [],
  unread: 0,
  toasts: [],

  // unread is the server's true count and is adjusted by deltas below, never
  // recomputed from `notifications` — that list is capped (~100 server-side),
  // so recomputing collapsed a real count like 137 to ~99 (FE-9).
  setAll: (notifications, unread) => set({ notifications, unread }),

  receive: (n) =>
    set((s) => {
      // Dedup by id — the watch loop refreshes existing rows in place.
      const existing = s.notifications.findIndex((x) => x.id === n.id);
      let notifications: Notification[];
      let unreadDelta = 0;
      if (existing >= 0) {
        const prev = s.notifications[existing];
        notifications = [...s.notifications];
        notifications[existing] = n;
        if (prev.read && !n.read) unreadDelta = 1;
        else if (!prev.read && n.read) unreadDelta = -1;
      } else {
        notifications = [n, ...s.notifications];
        unreadDelta = n.read ? 0 : 1;
      }
      return {
        notifications,
        unread: Math.max(0, s.unread + unreadDelta),
        // Surface a fresh toast — but only for unread notifications. Muted
        // categories arrive already-read: recorded in history, no pop-up.
        toasts: n.read
          ? s.toasts.filter((t) => t.id !== n.id)
          : [n, ...s.toasts.filter((t) => t.id !== n.id)].slice(0, 4),
      };
    }),

  pushToast: ({ title, body, severity = "error" }) =>
    set((s) => {
      const id = `toast-${
        typeof crypto !== "undefined" && crypto.randomUUID
          ? crypto.randomUUID()
          : `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
      }`;
      const toast: Notification = {
        id,
        kind: "manual",
        severity,
        title,
        body: body ?? null,
        run_id: null,
        link: null,
        meta: {},
        read: false,
        created_at: new Date().toISOString(),
      };
      // Prepend to toasts only — not `notifications`/`unread` (ephemeral).
      return { toasts: [toast, ...s.toasts].slice(0, 4) };
    }),

  markRead: (id) =>
    set((s) => {
      const target = s.notifications.find((n) => n.id === id);
      const wasUnread = target ? !target.read : false;
      return {
        notifications: s.notifications.map((n) =>
          n.id === id ? { ...n, read: true } : n
        ),
        unread: wasUnread ? Math.max(0, s.unread - 1) : s.unread,
      };
    }),

  markAllRead: () =>
    set((s) => ({
      notifications: s.notifications.map((n) => ({ ...n, read: true })),
      unread: 0,
    })),

  remove: (id) =>
    set((s) => {
      const target = s.notifications.find((n) => n.id === id);
      const wasUnread = target ? !target.read : false;
      return {
        notifications: s.notifications.filter((n) => n.id !== id),
        unread: wasUnread ? Math.max(0, s.unread - 1) : s.unread,
        toasts: s.toasts.filter((t) => t.id !== id),
      };
    }),

  clear: () => set({ notifications: [], unread: 0, toasts: [] }),

  dismissToast: (id) =>
    set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
}));
