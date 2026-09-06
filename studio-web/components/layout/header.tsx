"use client";

import { useEffect, useRef, useState } from "react";
import { usePathname } from "next/navigation";
import { Sun, Moon, Monitor, ChevronDown, Settings, HelpCircle, Server, LogOut, Search, Compass } from "lucide-react";
import { apiUrl, appRelativePath } from "@/lib/urls";
import { cn } from "@/lib/utils";
import { useThemeStore, applyTheme, type Theme } from "@/lib/stores/theme-store";
import { NotificationCenter } from "@/components/notifications/notification-center";
import { PegasusLogo } from "@/components/ui/pegasus-logo";

const STEPS = [
  { label: "Ask AI",    href: "/home"        },
  { label: "Run Jobs",  href: "/workflows"   },
  { label: "Notebooks", href: "/notebooks"   },
  { label: "Resources", href: "/provisioning"},
] as const;

function StepNav() {
  const pathname = usePathname();
  const clean = pathname.replace(/^\/user\/[^/]+/, "") || "/";
  const activeIdx = STEPS.findIndex(
    (s) => clean === s.href || clean.startsWith(s.href + "/")
  );

  return (
    <nav className="hidden md:flex items-center gap-0">
      {STEPS.map((step, i) => {
        const active = i === activeIdx;
        const done   = i < activeIdx;
        return (
          <span key={step.href} className="flex items-center">
            {/* Animated progress track */}
            {i > 0 && (
              <span className="relative mx-1 h-[2px] w-8 overflow-hidden rounded-full bg-line">
                <span
                  className="absolute inset-y-0 left-0 rounded-full bg-accent transition-all duration-500"
                  style={{ width: done ? "100%" : active ? "50%" : "0%" }}
                />
              </span>
            )}

            {/* Step node */}
            <a href={appRelativePath(step.href)} className="group flex flex-col items-center gap-1">
              {active ? (
                /* Active — pill with number + label */
                <span className="flex items-center gap-1.5 rounded-full bg-accent px-3 py-1 shadow-md shadow-accent/30 ring-4 ring-accent/10">
                  <span className="flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-white/25 text-[9px] font-black text-white">
                    {i + 1}
                  </span>
                  <span className="text-[11px] font-semibold text-white">{step.label}</span>
                </span>
              ) : (
                /* Inactive — circle + label below */
                <span className="flex flex-col items-center gap-1">
                  <span className={cn(
                    "flex h-6 w-6 items-center justify-center rounded-full border-2 text-[10px] font-bold transition-all",
                    done
                      ? "border-accent bg-accent/10 text-accent"
                      : "border-line bg-surface text-fgsubtle group-hover:border-accent/50 group-hover:text-accent/70"
                  )}>
                    {i + 1}
                  </span>
                  <span className={cn(
                    "text-[9px] font-medium transition-colors whitespace-nowrap",
                    done ? "text-fgmuted" : "text-fgsubtle group-hover:text-fgmuted"
                  )}>
                    {step.label}
                  </span>
                </span>
              )}
            </a>
          </span>
        );
      })}
    </nav>
  );
}

const THEME_ICONS: Record<Theme, React.ReactNode> = {
  light: <Sun size={14} />,
  dark: <Moon size={14} />,
  system: <Monitor size={14} />,
};

const THEMES: { value: Theme; label: string }[] = [
  { value: "light", label: "Light" },
  { value: "dark", label: "Dark" },
  { value: "system", label: "System" },
];

/**
 * Fixed top header — logo, notifications, theme toggle, user menu.
 * Replaces the old floating UserMenu overlay.
 */
export function Header() {
  const [user, setUser] = useState<string | null>(null);
  const [logoutUrl, setLogoutUrl] = useState<string | null>(null);
  const [controlUrl, setControlUrl] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  const theme = useThemeStore((s) => s.theme);
  const setTheme = useThemeStore((s) => s.setTheme);

  useEffect(() => { applyTheme(theme); }, [theme]);

  useEffect(() => {
    fetch(apiUrl("whoami"))
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        setUser(d?.user ?? null);
        setLogoutUrl(d?.logout_url ?? null);
        setControlUrl(d?.control_url ?? null);
      })
      .catch(() => setUser(null));
  }, []);

  useEffect(() => {
    if (!open) return;
    function handleClick(e: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [open]);

  const initial = user ? user[0]?.toUpperCase() ?? "?" : "?";

  return (
    <header className="fixed top-0 left-0 right-0 z-50 flex h-14 items-center justify-between border-b border-line bg-surface/80 px-4 backdrop-blur-md">
      {/* Logo — click to go home */}
      <a href={appRelativePath("/home")} className="flex items-center gap-2.5 select-none min-w-0 rounded-lg p-1 transition-opacity hover:opacity-80">
        <PegasusLogo size={26} />
        <div className="flex flex-col leading-none">
          <span className="text-[11px] font-bold uppercase tracking-[0.18em] text-accent">
            PegasusAI
          </span>
          <span className="text-[10px] font-medium text-fgsubtle tracking-wide">Studio</span>
        </div>
      </a>

      {/* Center — step nav */}
      <StepNav />

      {/* Right controls */}
      <div className="flex items-center gap-1.5">
        {/* Search / command palette trigger */}
        <button
          onClick={() => window.dispatchEvent(new Event("open-command-palette"))}
          className="flex items-center gap-2 rounded-lg border border-line bg-muted/60 px-3 py-1.5 text-xs text-fgsubtle transition-colors hover:border-accent/40 hover:text-fgmuted"
          title="Search (⌘K)"
        >
          <Search size={12} />
          <span className="hidden sm:inline">Search…</span>
          <kbd className="hidden rounded border border-line px-1 font-mono text-[10px] sm:inline">⌘K</kbd>
        </button>

        {/* Notifications */}
        <NotificationCenter />

        {/* Theme segmented control */}
        <div className="flex items-center rounded-lg border border-line bg-muted p-0.5">
          {THEMES.map((t) => (
            <button
              key={t.value}
              onClick={() => setTheme(t.value)}
              title={t.label}
              className={
                "flex items-center justify-center rounded-md p-1.5 transition-all " +
                (theme === t.value
                  ? "bg-surface text-accent shadow-sm glow-cyan-sm"
                  : "text-fgsubtle hover:text-fgmuted")
              }
            >
              {THEME_ICONS[t.value]}
            </button>
          ))}
        </div>

        {/* User menu */}
        <div ref={menuRef} className="relative">
          <button
            onClick={() => setOpen((v) => !v)}
            className="flex items-center gap-2 rounded-xl border border-line bg-surface px-2.5 py-1.5 text-sm transition-all hover:border-accent/40 hover:bg-muted"
          >
            <span className="flex h-6 w-6 items-center justify-center rounded-full bg-accent text-[11px] font-bold text-white">
              {initial}
            </span>
            {user && (
              <span className="max-w-[120px] truncate text-xs font-medium text-fgmuted">
                {user}
              </span>
            )}
            <ChevronDown size={13} className="text-fgsubtle" />
          </button>

          {open && (
            <div className="glass-card absolute right-0 mt-2 w-56 overflow-hidden shadow-xl">
              {user && (
                <div className="border-b border-line px-4 py-3">
                  <p className="text-[11px] text-fgsubtle">Signed in as</p>
                  <p className="mt-0.5 truncate text-sm font-semibold text-fg">{user}</p>
                </div>
              )}

              <nav className="py-1 text-sm">
                <a
                  href={appRelativePath("/settings")}
                  onClick={() => setOpen(false)}
                  className="flex items-center gap-2.5 px-4 py-2 text-fgmuted transition-colors hover:bg-muted hover:text-fg"
                >
                  <Settings size={14} /> Settings
                </a>
                <button
                  onClick={() => {
                    setOpen(false);
                    window.dispatchEvent(new Event("open-onboarding-tour"));
                  }}
                  className="flex w-full items-center gap-2.5 px-4 py-2 text-fgmuted transition-colors hover:bg-muted hover:text-fg"
                >
                  <Compass size={14} /> Take a tour
                </button>
                <a
                  href="https://github.com/pegasus-isi/studio-webapp#readme"
                  target="_blank"
                  rel="noopener noreferrer"
                  onClick={() => setOpen(false)}
                  className="flex items-center gap-2.5 px-4 py-2 text-fgmuted transition-colors hover:bg-muted hover:text-fg"
                >
                  <HelpCircle size={14} /> Help & docs
                </a>
                {controlUrl && (
                  <a
                    href={controlUrl}
                    className="flex items-center gap-2.5 border-t border-line px-4 py-2 text-fgmuted transition-colors hover:bg-muted hover:text-fg"
                  >
                    <Server size={14} /> Server control
                  </a>
                )}
                {logoutUrl && (
                  <a
                    href={logoutUrl}
                    className="flex items-center gap-2.5 border-t border-line px-4 py-2 text-rose-500 transition-colors hover:bg-muted"
                  >
                    <LogOut size={14} /> Sign out
                  </a>
                )}
              </nav>
            </div>
          )}
        </div>
      </div>
    </header>
  );
}
