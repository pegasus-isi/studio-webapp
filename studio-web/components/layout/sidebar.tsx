"use client";

import { usePathname } from "next/navigation";
import { appRelativePath, jupyterUrl, stripAppBasePath } from "@/lib/urls";
import { cn } from "@/lib/utils";
import { usePanelStore } from "@/lib/stores/panel-store";
import { useStudioVersion } from "@/lib/hooks/use-version";
import { UserMenu } from "@/components/layout/user-menu";
import {
  useUIPreferences,
  useUpdateUIPreferences,
} from "@/lib/hooks/use-ui-prefs";

// Settings is intentionally not here — it lives in the top-right user menu,
// which is already the "tucked away" position Phase 7 wants for it.
interface NavItem {
  href: string;
  label: string;
  icon: string;
  external?: boolean;
}

/**
 * What a scientist does, in the order they do it (#241, Phase 7).
 *
 * The old nav listed what the *infrastructure* offers — Workflows,
 * Marketplace, Resources — with a pool inventory sitting at the same level as
 * the thing you came to run. These are named for the task instead: an analysis
 * to run, data to run it on, examples to start from, notebooks to look at
 * results in.
 */
const PRIMARY_NAV: NavItem[] = [
  { href: "/", label: "Home", icon: "🏠" },
  { href: "/workflows", label: "My analyses", icon: "📊" },
  // Next to analyses because "add my data" and "run my analysis" are two
  // halves of one task (#241, Phase 2).
  { href: "/data", label: "My data", icon: "📁" },
  { href: "/marketplace", label: "Examples", icon: "🛒" },
  // Notebooks opens the prefix-aware JupyterLab in a new tab.
  { href: "/jupyter/lab", label: "Notebooks", icon: "📓", external: true },
];

/**
 * The machinery. Real, useful, and not what a first-time user should have to
 * step over to reach their analysis — so it is one click away rather than
 * absent. Collapsed by default; the state is per-user, because a workflow
 * engineer should not reopen it every visit.
 */
const ADVANCED_NAV: NavItem[] = [
  { href: "/provisioning", label: "Resources", icon: "🖥️" },
];

export function Sidebar() {
  const pathname = usePathname();
  const activePathname = stripAppBasePath(pathname);
  const chatOpen = usePanelStore((s) => s.chatOpen);
  const terminalOpen = usePanelStore((s) => s.terminalOpen);
  const collapsed = usePanelStore((s) => s.sidebarCollapsed);
  const toggleChat = usePanelStore((s) => s.toggleChat);
  const toggleTerminal = usePanelStore((s) => s.toggleTerminal);
  const toggleSidebar = usePanelStore((s) => s.toggleSidebar);
  const studioVersion = useStudioVersion();
  const { prefs } = useUIPreferences();
  const updatePrefs = useUpdateUIPreferences();

  // With the simplified interface off, the nav stays flat: someone who turned
  // it off is telling us they want the machinery in reach, and hiding half of
  // it behind a disclosure would be answering a different question.
  const simplified = prefs.simple_run;
  const primary = simplified ? PRIMARY_NAV : [...PRIMARY_NAV, ...ADVANCED_NAV];
  const advanced = simplified ? ADVANCED_NAV : [];

  const renderItem = (item: NavItem) => {
    const base = cn(
      "flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors",
      collapsed && "justify-center px-0"
    );
    if (item.external) {
      return (
        <a
          key={item.href}
          href={jupyterUrl("/lab")}
          target="_blank"
          rel="noopener noreferrer"
          title={item.label}
          className={cn(base, "text-fgmuted hover:bg-muted hover:text-fg")}
        >
          <span>{item.icon}</span>
          {!collapsed && (
            <>
              {item.label}
              <span className="ml-auto text-xs text-fgsubtle">↗</span>
            </>
          )}
        </a>
      );
    }
    const active =
      item.href === "/"
        ? activePathname === "/"
        : activePathname === item.href ||
          activePathname.startsWith(item.href + "/");
    return (
      <a
        key={item.href}
        href={appRelativePath(item.href)}
        title={item.label}
        className={cn(
          base,
          active
            ? "bg-pegasus-50 dark:bg-pegasus-400/10 text-pegasus-700 dark:text-pegasus-300"
            : "text-fgmuted hover:bg-muted hover:text-fg"
        )}
      >
        <span>{item.icon}</span>
        {!collapsed && item.label}
      </a>
    );
  };

  const collapseToggle = (
    <button
      onClick={toggleSidebar}
      title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
      className="rounded p-1.5 text-fgsubtle transition-colors hover:bg-muted hover:text-fg"
    >
      <svg
        width="16"
        height="16"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
        className={cn("transition-transform", collapsed && "rotate-180")}
      >
        <polyline points="15 18 9 12 15 6" />
      </svg>
    </button>
  );

  return (
    <aside
      className={cn(
        "flex flex-col border-r border-line bg-surface transition-[width] duration-150",
        collapsed ? "w-14" : "w-56"
      )}
    >
      {/* Logo + release version + collapse toggle */}
      {collapsed ? (
        <div className="flex h-14 items-center justify-center border-b border-line">
          {collapseToggle}
        </div>
      ) : (
        <div className="border-b border-line px-3 py-2">
          <div className="flex items-center justify-between">
            <span className="flex items-center gap-1.5">
              <img
                src="pegasus-ai-logo-light.png"
                alt="PegasusAI"
                className="block h-9 w-auto dark:hidden"
              />
              <img
                src="pegasus-ai-logo-dark.png"
                alt="PegasusAI"
                className="hidden h-9 w-auto dark:block"
              />
              <span className="text-sm font-medium text-fgsubtle">Studio</span>
            </span>
            {collapseToggle}
          </div>
          {/* Non-breaking space while the version loads keeps the header from
              shifting the nav down on first paint. */}
          <span
            title="PegasusAI Studio release"
            className="mt-0.5 block font-mono text-[10px] leading-none text-fgsubtle"
          >
            {studioVersion ?? "\u00a0"}
          </span>
        </div>
      )}

      {/* Navigation */}
      <nav className="flex-1 space-y-1 p-2">
        {primary.map(renderItem)}

        {/* Advanced — the machinery, one click away rather than absent.
            Only split out in the simplified interface; with it off, the full
            nav is flat, which is what a Pegasus-literate user asked for by
            turning it off. */}
        {advanced.length > 0 && (
          <div className="mt-4 border-t border-line pt-3">
            {collapsed ? (
              advanced.map(renderItem)
            ) : (
              <details
                open={prefs.nav_advanced_open}
                onToggle={(e) => {
                  const open = (e.currentTarget as HTMLDetailsElement).open;
                  if (open !== prefs.nav_advanced_open) {
                    updatePrefs.mutate({ ...prefs, nav_advanced_open: open });
                  }
                }}
              >
                <summary className="mb-1 cursor-pointer px-3 text-xs font-medium uppercase tracking-wider text-fgsubtle hover:text-fg">
                  Advanced
                </summary>
                {advanced.map(renderItem)}
              </details>
            )}
          </div>
        )}

        {/* Quick Access */}
        <div className="mt-4 border-t border-line pt-3">
          {!collapsed && (
            <p className="mb-1 px-3 text-xs font-medium uppercase tracking-wider text-fgsubtle">
              Quick Access
            </p>
          )}
          <button
            onClick={toggleChat}
            title="PegasusAI Chat"
            className={cn(
              "flex w-full items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors",
              collapsed && "justify-center px-0",
              chatOpen
                ? "bg-pegasus-50 dark:bg-pegasus-400/10 text-pegasus-700 dark:text-pegasus-300"
                : "text-fgmuted hover:bg-muted hover:text-fg"
            )}
          >
            <svg
              width="16"
              height="16"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
            </svg>
            {!collapsed && "PegasusAI Chat"}
          </button>
          {/* Terminal is an escape hatch, not a primary tool. In the
              simplified interface it moves under Advanced; with the interface
              turned off it stays where a power user expects it. */}
          {(!simplified || prefs.nav_advanced_open) && (
          <button
            onClick={toggleTerminal}
            title="Terminal"
            className={cn(
              "flex w-full items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors",
              collapsed && "justify-center px-0",
              terminalOpen
                ? "bg-gray-800 text-white"
                : "text-fgmuted hover:bg-muted hover:text-fg"
            )}
          >
            <svg
              width="16"
              height="16"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <polyline points="4 17 10 11 4 5" />
              <line x1="12" y1="19" x2="20" y2="19" />
            </svg>
            {!collapsed && "Terminal"}
          </button>
          )}
        </div>
      </nav>

      {/* Identity lives here rather than floating over the page (#276). The
          sidebar is always present and always visible, so the menu costs the
          content area nothing and no page header has to reserve a corner for
          it. */}
      <div className="border-t border-line p-2">
        <UserMenu collapsed={collapsed} />
      </div>
    </aside>
  );
}
