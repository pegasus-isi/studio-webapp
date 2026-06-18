/**
 * Theme store: light / dark / system, persisted to localStorage and applied as
 * the `.dark` class on <html>. A no-flash inline script in layout.tsx sets the
 * class before first paint; this store keeps it in sync after hydration and on
 * user toggle.
 */

import { create } from "zustand";
import { persist } from "zustand/middleware";

export type Theme = "light" | "dark" | "system";

function systemPrefersDark(): boolean {
  return (
    typeof window !== "undefined" &&
    window.matchMedia("(prefers-color-scheme: dark)").matches
  );
}

export function applyTheme(theme: Theme): void {
  if (typeof document === "undefined") return;
  const dark = theme === "dark" || (theme === "system" && systemPrefersDark());
  document.documentElement.classList.toggle("dark", dark);
}

interface ThemeStore {
  theme: Theme;
  setTheme: (t: Theme) => void;
  cycle: () => void; // light -> dark -> system -> light
}

export const useThemeStore = create<ThemeStore>()(
  persist(
    (set, get) => ({
      theme: "system",
      setTheme: (t) => {
        applyTheme(t);
        set({ theme: t });
      },
      cycle: () => {
        const order: Theme[] = ["light", "dark", "system"];
        const next = order[(order.indexOf(get().theme) + 1) % order.length];
        applyTheme(next);
        set({ theme: next });
      },
    }),
    { name: "studio-theme" }
  )
);

// Cross-document sync: the embedded chat (JupyterLab iframe) is a separate
// document with its own store, so a toggle in the studio window must reach it.
// localStorage "storage" events fire in OTHER same-origin documents; re-apply
// the theme there. The equality guard prevents a persist/storage ping-pong.
if (typeof window !== "undefined") {
  window.addEventListener("storage", (e) => {
    if (e.key && e.key !== "studio-theme") return;
    try {
      const t = JSON.parse(localStorage.getItem("studio-theme") || "{}").state
        ?.theme as Theme | undefined;
      if (t && t !== useThemeStore.getState().theme) {
        applyTheme(t);
        useThemeStore.setState({ theme: t });
      }
    } catch {
      /* ignore */
    }
  });
}
