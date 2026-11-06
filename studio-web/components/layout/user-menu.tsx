"use client";

import { useEffect, useRef, useState } from "react";
import { apiUrl, appRelativePath } from "@/lib/urls";
import { useThemeStore, applyTheme, type Theme } from "@/lib/stores/theme-store";
import { NotificationCenter } from "@/components/notifications/notification-center";
import { cn } from "@/lib/utils";
import { AppLink } from "@/components/layout/app-link";

/** The PegasusAI project site, linked from the account menu and the ⌘K palette. */
export const PROJECT_SITE = "https://pegasus-ai.org/";

/** Where users reach the Pegasus team; shown in the account menu and ⌘K. */
export const SUPPORT_EMAIL = "pegasus-support@isi.edu";
export const CONTACT_PAGE = "https://pegasus.isi.edu/contact/";

/**
 * Help goes to the Pegasus team's contact page (mailing lists, Slack, support
 * address). It used to open the studio-webapp README, which is written for
 * people building the image, not for people using it.
 */
export const HELP_URL = CONTACT_PAGE;

/**
 * User menu: username + dropdown (theme, Settings, JupyterHub server control /
 * Stop server, Sign out). Identity and the hub URLs come from /api/whoami
 * (JUPYTERHUB_USER + JUPYTERHUB_BASE_URL); hidden if no identity.
 *
 * ## Lives in the sidebar, not floating over the page (#276)
 *
 * This used to be `fixed right-4 top-2.5`, overlaying the page rather than
 * occupying layout space — so every page header with right-aligned content had
 * to reserve the corner by hand (`pr-[19rem]`), a constraint the next header
 * to be written would silently violate.
 *
 * The obvious alternative, a global top bar, would stack a second bar above
 * every page's own header and cost vertical space on all of them. The sidebar
 * footer is already always-present and always-visible, costs the content area
 * nothing, and collapses with the rest of the nav — so identity moved there
 * instead. Both dropdowns consequently open *upward and rightward*, over the
 * main content, since there is nothing below or to the left of them.
 */
const THEMES: { value: Theme; label: string; icon: string }[] = [
  { value: "light", label: "Light", icon: "☀️" },
  { value: "dark", label: "Dark", icon: "🌙" },
  { value: "system", label: "System", icon: "🖥️" },
];

export function UserMenu({ collapsed = false }: { collapsed?: boolean }) {
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
    <div
      className={cn(
        "flex items-center gap-2",
        collapsed && "flex-col gap-1"
      )}
    >
      <NotificationCenter />
      <div ref={menuRef} className="relative min-w-0 flex-1">
      <button
        onClick={() => setOpen((v) => !v)}
        title={user ?? "Account"}
        aria-label={user ? `Account menu for ${user}` : "Account menu"}
        className={cn(
          "flex w-full items-center gap-2 rounded-full border border-line bg-surface py-1 text-sm transition-colors hover:bg-muted",
          collapsed ? "justify-center px-1" : "pl-1 pr-3"
        )}
      >
        <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-accent text-xs font-semibold text-accent-fg">
          {initial}
        </span>
        {!collapsed && user && (
          <span className="min-w-0 flex-1 truncate text-left text-fgmuted">
            {user}
          </span>
        )}
        {!collapsed && (
          <svg
            width="12"
            height="12"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            className="shrink-0 text-fgsubtle"
          >
            <polyline points="18 15 12 9 6 15" />
          </svg>
        )}
      </button>

      {open && (
        <div className="absolute bottom-full left-0 z-50 mb-1.5 w-60 overflow-hidden rounded-lg border border-line bg-surface shadow-lg">
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
            <AppLink
              href="/settings"
              onClick={() => setOpen(false)}
              className="block px-4 py-2 hover:bg-muted"
            >
              ⚙️ Application Settings
            </AppLink>
            {/* The tour's only launcher used to be a link at the bottom of the
                landing page, below the openers — findable if you already knew
                it was there. The account menu is on every page, which is where
                "show me that again" belongs. It goes to the landing page
                because that is where the tour's steps are: the whole rail is
                visible from there, so a nav tour needs no navigation. */}
            <AppLink
              href="/home?tour=1"
              onClick={() => setOpen(false)}
              className="block px-4 py-2 hover:bg-muted"
            >
              🧭 Take a tour
            </AppLink>
            <a
              href={HELP_URL}
              target="_blank"
              rel="noopener noreferrer"
              onClick={() => setOpen(false)}
              className="block px-4 py-2 hover:bg-muted"
            >
              ❓ Help
            </a>
            {/* Back to the project's own site (launch feedback, 2026-11). A
                new tab, like Help: leaving Studio in this one would drop the
                user's place in the app. */}
            <a
              href={PROJECT_SITE}
              target="_blank"
              rel="noopener noreferrer"
              onClick={() => setOpen(false)}
              className="block px-4 py-2 hover:bg-muted"
            >
              🌐 PegasusAI website
            </a>
            {/* The address is printed, not only behind a mailto: link, because
                hosted users often have no mail client registered with the
                browser, and a mailto: that does nothing leaves them with no
                way to find out where to write. */}
            <div className="px-4 py-2">
              <a
                href={`mailto:${SUPPORT_EMAIL}`}
                onClick={() => setOpen(false)}
                className="block hover:underline"
              >
                ✉️ Contact us
              </a>
              <p className="mt-0.5 select-all pl-6 text-xs text-fgmuted">
                {SUPPORT_EMAIL}
              </p>
            </div>
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
