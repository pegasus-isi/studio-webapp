"use client";

/**
 * The application bar (#314).
 *
 * Full width above the rail, carrying what belongs to the whole app rather
 * than to a page. Identity stays on the rail — always visible, and it costs
 * the content area nothing — so this is the wordmark, the step navigator
 * (where you are on the Home → Run Workflows → Developer Space → Resources path)
 * and search.
 */

import { useEffect, useState } from "react";
import { AppLink } from "@/components/layout/app-link";
import { CommandPalette } from "@/components/layout/command-palette";
import { StepNav } from "@/components/layout/step-nav";
import { useStudioVersion } from "@/lib/hooks/use-version";

export function TopBar() {
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [mac, setMac] = useState(false);
  const studioVersion = useStudioVersion();

  useEffect(() => {
    setMac(/Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent));
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPaletteOpen((v) => !v);
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  return (
    <>
      <header className="flex h-12 shrink-0 items-center gap-3 border-b border-line bg-surface px-3">
        <AppLink
          href="/home"
          className="flex shrink-0 items-baseline gap-1.5"
          title={studioVersion ? `PegasusAI Studio ${studioVersion}` : "PegasusAI Studio"}
        >
          <img
            src="pegasus-ai-logo-light.png"
            alt=""
            className="block h-7 w-auto self-center dark:hidden"
          />
          <img
            src="pegasus-ai-logo-dark.png"
            alt=""
            className="hidden h-7 w-auto self-center dark:block"
          />
          <span className="text-[13px] font-bold tracking-[0.07em] text-fg">
            PEGASUSAI
          </span>
          <span className="text-[11px] text-fgsubtle">Studio</span>
        </AppLink>

        {/* Centred between wordmark and search; hidden below md, where the
            rail's labels already say where you are. */}
        <div className="flex min-w-0 flex-1 justify-center">
          <StepNav />
        </div>

        <button
          data-tour="search"
          onClick={() => setPaletteOpen(true)}
          className="flex h-8 w-64 shrink-0 items-center gap-2 rounded-lg border border-line bg-base px-2.5 text-xs text-fgsubtle transition-colors hover:border-pegasus-300 hover:text-fgmuted"
        >
          <svg
            width="13"
            height="13"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.2"
            className="shrink-0"
            aria-hidden="true"
          >
            <circle cx="11" cy="11" r="7" />
            <path d="M20 20l-3.5-3.5" />
          </svg>
          <span className="truncate">Search workflows, examples…</span>
          <kbd className="ml-auto shrink-0 rounded border border-line px-1 font-mono text-[10px]">
            {mac ? "⌘K" : "Ctrl K"}
          </kbd>
        </button>

      </header>

      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} />
    </>
  );
}
