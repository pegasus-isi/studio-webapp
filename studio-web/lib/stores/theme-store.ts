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
