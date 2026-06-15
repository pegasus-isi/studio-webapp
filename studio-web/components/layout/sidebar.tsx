"use client";

import { usePathname } from "next/navigation";
import { appRelativePath, jupyterUrl, stripAppBasePath } from "@/lib/urls";
import { cn } from "@/lib/utils";
import { usePanelStore } from "@/lib/stores/panel-store";
import { PegasusLogo } from "@/components/ui/pegasus-logo";

const NAV_ITEMS: {
  href: string;
  label: string;
  icon: string;
  external?: boolean;
}[] = [
  { href: "/workflows", label: "Workflows", icon: "📊" },
  { href: "/workbench", label: "Workbench", icon: "🔧" },
  // Notebooks is just a link to the always-on JupyterLab (opens in a new tab),
  // not an in-studio pane.
  { href: "/jupyter/lab", label: "Notebooks", icon: "📓", external: true },
  { href: "/settings", label: "Settings", icon: "⚙️" },
];

export function Sidebar() {
  const pathname = usePathname();
  const activePathname = stripAppBasePath(pathname);
  const chatOpen = usePanelStore((s) => s.chatOpen);
  const terminalOpen = usePanelStore((s) => s.terminalOpen);
  const toggleChat = usePanelStore((s) => s.toggleChat);
  const toggleTerminal = usePanelStore((s) => s.toggleTerminal);

  return (
    <aside className="flex w-56 flex-col border-r border-line bg-surface">
      {/* Logo */}
      <div className="flex h-14 items-center gap-2 border-b border-line px-4">
        <PegasusLogo size={22} />
        <span className="text-lg font-bold text-pegasus-700 dark:text-pegasus-300">PegasusAI</span>
        <span className="text-xs text-fgsubtle">Studio</span>
      </div>

      {/* Navigation */}
      <nav className="flex-1 space-y-1 p-3">
        {NAV_ITEMS.map((item) => {
          const classes =
            "flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors text-fgmuted hover:bg-muted hover:text-fg";

          // External links (e.g. JupyterLab) open in a new browser tab.
          if (item.external) {
            return (
              <a
                key={item.href}
                href={jupyterUrl("/lab")}
                target="_blank"
                rel="noopener noreferrer"
                className={classes}
              >
                <span>{item.icon}</span>
                {item.label}
                <span className="ml-auto text-xs text-fgsubtle">↗</span>
              </a>
            );
          }

          const active =
            activePathname === item.href ||
            activePathname.startsWith(item.href + "/") ||
            (activePathname === "/" && item.href === "/workflows");
          return (
            <a
              key={item.href}
              href={appRelativePath(item.href)}
              className={cn(
                "flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors",
                active
                  ? "bg-pegasus-50 dark:bg-pegasus-400/10 text-pegasus-700 dark:text-pegasus-300"
                  : "text-fgmuted hover:bg-muted hover:text-fg"
              )}
            >
              <span>{item.icon}</span>
              {item.label}
            </a>
          );
        })}

        {/* Quick Access */}
        <div className="mt-4 border-t border-line pt-3">
          <p className="mb-1 px-3 text-xs font-medium uppercase tracking-wider text-fgsubtle">
            Quick Access
          </p>
          <button
            onClick={toggleChat}
            className={cn(
              "flex w-full items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors",
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
            PegasusAI Chat
          </button>
          <button
            onClick={toggleTerminal}
            className={cn(
              "flex w-full items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors",
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
            Terminal
          </button>
        </div>
      </nav>

      {/* Footer */}
      <div className="border-t border-line p-3 text-xs text-fgsubtle">
        v0.1.0
      </div>
    </aside>
  );
}
