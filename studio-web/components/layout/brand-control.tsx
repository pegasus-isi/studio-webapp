"use client";

import { useEffect } from "react";
import { useThemeStore, applyTheme, type Theme } from "@/lib/stores/theme-store";

/**
 * Top-right theme toggle (light → dark → system). Branding lives top-left in
 * the sidebar; identity/logout are handled by JupyterHub upstream, so this is
 * intentionally not a full user menu.
 */
const ICONS: Record<Theme, string> = {
  light: "☀️",
  dark: "🌙",
  system: "🖥️",
};

export function BrandControl() {
  const theme = useThemeStore((s) => s.theme);
  const cycle = useThemeStore((s) => s.cycle);

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  return (
    <div className="fixed right-4 top-2.5 z-50">
      <button
        onClick={cycle}
        title={`Theme: ${theme} (click to change)`}
        className="flex h-8 w-8 items-center justify-center rounded-full border border-line bg-surface text-sm shadow-sm transition-colors hover:bg-muted"
      >
        <span>{ICONS[theme]}</span>
      </button>
    </div>
  );
}
