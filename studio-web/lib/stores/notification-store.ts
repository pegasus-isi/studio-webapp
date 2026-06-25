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
  markRead: (id: string) => void;
  markAllRead: () => void;
  remove: (id: string) => void;
  clear: () => void;
  dismissToast: (id: string) => void;
}

function countUnread(list: Notification[]): number {
  return list.reduce((n, x) => (x.read ? n : n + 1), 0);
}

export const useNotificationStore = create<NotificationStore>((set) => ({
  notifications: [],
  unread: 0,
  toasts: [],

  setAll: (notifications, unread) => set({ notifications, unread }),

  receive: (n) =>
    set((s) => {
      // Dedup by id — the watch loop refreshes existing rows in place.
      const existing = s.notifications.findIndex((x) => x.id === n.id);
      let notifications: Notification[];
      if (existing >= 0) {
        notifications = [...s.notifications];
        notifications[existing] = n;
      } else {
        notifications = [n, ...s.notifications];
      }
      return {
        notifications,
        unread: countUnread(notifications),
        // Surface a fresh toast — but only for unread notifications. Muted
        // categories arrive already-read: recorded in history, no pop-up.
        toasts: n.read
          ? s.toasts.filter((t) => t.id !== n.id)
          : [n, ...s.toasts.filter((t) => t.id !== n.id)].slice(0, 4),
      };
    }),

  markRead: (id) =>
    set((s) => {
      const notifications = s.notifications.map((n) =>
        n.id === id ? { ...n, read: true } : n
      );
      return { notifications, unread: countUnread(notifications) };
    }),

  markAllRead: () =>
    set((s) => ({
      notifications: s.notifications.map((n) => ({ ...n, read: true })),
      unread: 0,
    })),

  remove: (id) =>
    set((s) => {
      const notifications = s.notifications.filter((n) => n.id !== id);
      return {
        notifications,
        unread: countUnread(notifications),
        toasts: s.toasts.filter((t) => t.id !== id),
      };
    }),

  clear: () => set({ notifications: [], unread: 0, toasts: [] }),

  dismissToast: (id) =>
    set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
}));
