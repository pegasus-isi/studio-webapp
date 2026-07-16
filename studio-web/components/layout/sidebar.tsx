"use client";

import { usePathname } from "next/navigation";
import { appRelativePath, jupyterUrl, stripAppBasePath } from "@/lib/urls";
import { cn } from "@/lib/utils";
import { usePanelStore } from "@/lib/stores/panel-store";

// Settings is intentionally not here — it lives in the top-right user menu.
const NAV_ITEMS: {
  href: string;
  label: string;
  icon: string;
  external?: boolean;
}[] = [
  { href: "/", label: "Dashboard", icon: "🏠" },
  { href: "/workflows", label: "Workflows", icon: "📊" },
  { href: "/marketplace", label: "Marketplace", icon: "🛒" },
  { href: "/provisioning", label: "Resources", icon: "🖥️" },
  // Notebooks opens the prefix-aware JupyterLab in a new tab.
  { href: "/jupyter/lab", label: "Notebooks", icon: "📓", external: true },
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

  return (
    <aside
      className={cn(
        "flex flex-col border-r border-line bg-surface transition-[width] duration-150",
        collapsed ? "w-14" : "w-56"
      )}
    >
      {/* Logo + collapse toggle */}
      <div
        className={cn(
          "flex h-14 items-center border-b border-line",
          collapsed ? "justify-center px-0" : "justify-between px-3"
        )}
      >
        {!collapsed && (
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
        )}
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
      </div>

      {/* Navigation */}
      <nav className="flex-1 space-y-1 p-2">
        {NAV_ITEMS.map((item) => {
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
                className={cn(
                  base,
                  "text-fgmuted hover:bg-muted hover:text-fg"
                )}
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
        })}

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
        </div>
      </nav>

      {/* Footer */}
      {!collapsed && (
        <div className="border-t border-line p-3 text-xs text-fgsubtle">
          v0.1.0
        </div>
      )}
    </aside>
  );
}
