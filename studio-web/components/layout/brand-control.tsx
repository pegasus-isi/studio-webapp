"use client";

import { useEffect } from "react";
import { appRelativePath } from "@/lib/urls";
import { useThemeStore, applyTheme, type Theme } from "@/lib/stores/theme-store";
import { PegasusLogo } from "@/components/ui/pegasus-logo";

/**
 * Top-right cluster: the PegasusAI brand mark (links home) and a theme toggle
 * cycling light → dark → system. Identity/logout are handled by JupyterHub
 * upstream, so this is intentionally not a full user menu.
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
    <div className="fixed right-4 top-2.5 z-50 flex items-center gap-2">
      <a
        href={appRelativePath("/")}
        title="PegasusAI Studio"
        className="flex items-center"
      >
        <PegasusLogo size={26} />
      </a>
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
