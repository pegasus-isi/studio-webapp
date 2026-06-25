"use client";

import { useEffect, useRef, useState } from "react";
import { apiUrl, appRelativePath } from "@/lib/urls";
import { useThemeStore, applyTheme, type Theme } from "@/lib/stores/theme-store";
import { NotificationCenter } from "@/components/notifications/notification-center";

/**
 * Top-right user menu: username + dropdown (theme, Settings, JupyterHub server
 * control / Stop server, Sign out). Identity and the hub URLs come from
 * /api/whoami (JUPYTERHUB_USER + JUPYTERHUB_BASE_URL); hidden if no identity.
 */
const THEMES: { value: Theme; label: string; icon: string }[] = [
  { value: "light", label: "Light", icon: "☀️" },
  { value: "dark", label: "Dark", icon: "🌙" },
  { value: "system", label: "System", icon: "🖥️" },
];

export function UserMenu() {
  const [user, setUser] = useState<string | null>(null);
  const [logoutUrl, setLogoutUrl] = useState<string | null>(null);
  const [controlUrl, setControlUrl] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  const theme = useThemeStore((s) => s.theme);
  const setTheme = useThemeStore((s) => s.setTheme);

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

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
    function handleClick(e: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    if (open) {
      document.addEventListener("mousedown", handleClick);
      return () => document.removeEventListener("mousedown", handleClick);
    }
  }, [open]);

  const initial = user ? user[0]?.toUpperCase() ?? "?" : "⚙";

  return (
    <div className="fixed right-4 top-2.5 z-50 flex items-center gap-2">
      <NotificationCenter />
      <div ref={menuRef} className="relative">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-2 rounded-full border border-line bg-surface py-1 pl-1 pr-3 text-sm shadow-sm transition-colors hover:bg-muted"
      >
        <span className="flex h-7 w-7 items-center justify-center rounded-full bg-accent text-xs font-semibold text-accent-fg">
          {initial}
        </span>
        {user && (
          <span className="max-w-[180px] truncate text-fgmuted">{user}</span>
        )}
        <svg
          width="12"
          height="12"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          className="text-fgsubtle"
        >
          <polyline points="6 9 12 15 18 9" />
        </svg>
      </button>

      {open && (
        <div className="absolute right-0 mt-1.5 w-60 overflow-hidden rounded-lg border border-line bg-surface shadow-lg">
          {user && (
            <div className="border-b border-line px-4 py-3">
              <p className="text-xs text-fgsubtle">Signed in as</p>
              <p className="truncate text-sm font-medium text-fg">{user}</p>
            </div>
          )}

          {/* Theme switch */}
          <div className="border-b border-line px-4 py-3">
            <p className="mb-2 text-xs text-fgsubtle">Theme</p>
            <div className="flex gap-1 rounded-md bg-muted p-1">
              {THEMES.map((t) => (
                <button
                  key={t.value}
                  onClick={() => setTheme(t.value)}
                  className={
                    "flex flex-1 items-center justify-center gap-1 rounded px-2 py-1 text-xs transition-colors " +
                    (theme === t.value
                      ? "bg-surface text-fg shadow-sm"
                      : "text-fgmuted hover:text-fg")
                  }
                  title={t.label}
                >
                  <span>{t.icon}</span>
                  <span className="hidden sm:inline">{t.label}</span>
                </button>
              ))}
            </div>
          </div>

          <nav className="py-1 text-sm text-fg">
            <a
              href={appRelativePath("/settings")}
              onClick={() => setOpen(false)}
              className="block px-4 py-2 hover:bg-muted"
            >
              ⚙️ Settings
            </a>
            <a
              href="https://github.com/pegasus-isi/studio-webapp#readme"
              target="_blank"
              rel="noopener noreferrer"
              onClick={() => setOpen(false)}
              className="block px-4 py-2 hover:bg-muted"
            >
              ❓ Help
            </a>
            {controlUrl && (
              <a
                href={controlUrl}
                className="block border-t border-line px-4 py-2 hover:bg-muted"
              >
                🖥️ Server control panel
              </a>
            )}
            {logoutUrl && (
              <a
                href={logoutUrl}
                className="block border-t border-line px-4 py-2 text-rose-500 hover:bg-muted"
              >
                ↩ Sign out
              </a>
            )}
          </nav>
        </div>
      )}
      </div>
    </div>
  );
}
