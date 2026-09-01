"use client";

import type { ReactNode } from "react";
import { usePathname } from "next/navigation";
import { jupyterUrl, stripAppBasePath } from "@/lib/urls";
import { AppLink } from "@/components/layout/app-link";
import { cn } from "@/lib/utils";
import { usePanelStore } from "@/lib/stores/panel-store";
import { useStudioVersion } from "@/lib/hooks/use-version";
import { UserMenu } from "@/components/layout/user-menu";
import {
  useUIPreferences,
  useUpdateUIPreferences,
} from "@/lib/hooks/use-ui-prefs";

/**
 * The left rail (#314).
 *
 * Previously a 224px sidebar of emoji and text. Emoji were the wrong material
 * for this: they render as a different glyph on every platform, they cannot
 * take a colour from state, and at nav size they read as a documentation site
 * rather than an instrument panel. These are stroke icons with a micro-label
 * underneath — the label costs 22px of width against an icons-only rail and
 * removes the guess-and-hover a bare icon rail forces on a first-time user.
 *
 * Collapsed drops the labels rather than the rail, so the toggle still buys
 * the canvas back on the graph screen.
 *
 * Settings is intentionally absent — it lives in the user menu at the bottom,
 * which is the tucked-away position Phase 7 wanted for it.
 */

const ICON = {
  home: (
    <>
      <path d="M3 10.5 12 3l9 7.5" />
      <path d="M5.5 9.5V20h13V9.5" />
    </>
  ),
  analyses: (
    <>
      <path d="M4 19V5M4 19h16" />
      <path d="M8 16v-5M12 16V7M16 16v-8" />
    </>
  ),
  data: (
    <>
      <path d="M3 7l9-4 9 4-9 4-9-4z" />
      <path d="M3 12l9 4 9-4M3 17l9 4 9-4" />
    </>
  ),
  examples: (
    <>
      <path d="M4 6h16v13a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1z" />
      <path d="M8 6V4h8v2" />
    </>
  ),
  notebooks: (
    <>
      <path d="M4 4h7v16H4zM13 4h7v16h-7z" />
    </>
  ),
  compute: (
    <>
      <rect x="3" y="4" width="18" height="7" rx="1.6" />
      <rect x="3" y="13" width="18" height="7" rx="1.6" />
      <path d="M7 7.5h.01M7 16.5h.01" />
    </>
  ),
  chat: (
    <>
      <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
    </>
  ),
  terminal: (
    <>
      <path d="M4 17l6-5-6-5M12 19h8" />
    </>
  ),
  more: (
    <>
      <path d="M5 12h.01M12 12h.01M19 12h.01" />
    </>
  ),
} as const;

function Glyph({ name }: { name: keyof typeof ICON }) {
  return (
    <svg
      width="18"
      height="18"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.9"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {ICON[name]}
    </svg>
  );
}

interface NavItem {
  href: string;
  label: string;
  icon: keyof typeof ICON;
  external?: boolean;
}

/**
 * What a scientist does, in the order they do it (#241, Phase 7).
 *
 * Named for the task, not for what the infrastructure offers: an analysis to
 * run, data to run it on, examples to start from, notebooks to look at results
 * in.
 */
const PRIMARY_NAV: NavItem[] = [
  // "/home", not "/" — see app/(studio)/home/page.tsx (#292).
  { href: "/home", label: "Home", icon: "home" },
  { href: "/workflows", label: "Analyses", icon: "analyses" },
  // Next to analyses because "add my data" and "run my analysis" are two
  // halves of one task (#241, Phase 2).
  { href: "/data", label: "Data", icon: "data" },
  { href: "/marketplace", label: "Examples", icon: "examples" },
  // Notebooks opens the prefix-aware JupyterLab in a new tab.
  { href: "/jupyter/lab", label: "Notebooks", icon: "notebooks", external: true },
];

/**
 * The machinery. Real, useful, and not what a first-time user should have to
 * step over to reach their analysis — so it is one click away rather than
 * absent.
 */
const ADVANCED_NAV: NavItem[] = [
  { href: "/provisioning", label: "Resources", icon: "compute" },
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

  // With the simplified interface off, the rail stays flat: someone who turned
  // it off is telling us they want the machinery in reach, and hiding half of
  // it behind a disclosure would be answering a different question.
  const simplified = prefs.simple_run;
  const primary = simplified ? PRIMARY_NAV : [...PRIMARY_NAV, ...ADVANCED_NAV];
  const advanced = simplified ? ADVANCED_NAV : [];
  // In a rail there is nowhere to put a <details> summary, so the disclosure
  // Phase 7 introduced becomes a "More" cell that expands the group in place.
  const advancedOpen = prefs.nav_advanced_open;

  const cellClass = (active: boolean) =>
    cn(
      "flex w-full flex-col items-center gap-1 rounded-lg px-1 py-2 transition-colors",
      active
        ? "bg-pegasus-50 text-pegasus-700 dark:bg-pegasus-400/10 dark:text-pegasus-300"
        : "text-fgsubtle hover:bg-muted hover:text-fg"
    );

  const cellLabel = (label: string) =>
    collapsed ? null : (
      <span className="text-[9px] font-semibold leading-none">{label}</span>
    );

  const renderItem = (item: NavItem) => {
    if (item.external) {
      return (
        <a
          key={item.href}
          href={jupyterUrl("/lab")}
          target="_blank"
          rel="noopener noreferrer"
          title={`${item.label} — opens in a new tab`}
          className={cellClass(false)}
        >
          <Glyph name={item.icon} />
          {cellLabel(item.label)}
        </a>
      );
    }
    // The dashboard answers to both "/" (where a user lands) and "/home"
    // (where the rail links), so either should light up Home.
    const active =
      item.href === "/home"
        ? activePathname === "/" || activePathname.startsWith("/home")
        : activePathname === item.href ||
          activePathname.startsWith(item.href + "/");
    return (
      <AppLink
        key={item.href}
        href={item.href}
        title={item.label}
        aria-current={active ? "page" : undefined}
        className={cellClass(active)}
      >
        <Glyph name={item.icon} />
        {cellLabel(item.label)}
      </AppLink>
    );
  };

  const panelButton = (
    icon: keyof typeof ICON,
    label: string,
    open: boolean,
    onClick: () => void
  ): ReactNode => (
    <button
      onClick={onClick}
      title={label}
      aria-pressed={open}
      className={cellClass(open)}
    >
      <Glyph name={icon} />
      {cellLabel(label)}
    </button>
  );

  return (
    <aside
      className={cn(
        "flex flex-col border-r border-line bg-surface transition-[width] duration-150",
        collapsed ? "w-[46px]" : "w-[68px]"
      )}
    >
      {/* Mark + collapse toggle. The wordmark does not fit a rail and was
          costing more width than it earned; the release string moves to the
          toggle's tooltip so it is still reachable without a header. */}
      <div className="flex flex-col items-center gap-1 border-b border-line px-1 py-2">
        <img
          src="pegasus-ai-logo-light.png"
          alt="PegasusAI Studio"
          className="block h-7 w-auto dark:hidden"
        />
        <img
          src="pegasus-ai-logo-dark.png"
          alt="PegasusAI Studio"
          className="hidden h-7 w-auto dark:block"
        />
        <button
          onClick={toggleSidebar}
          title={
            collapsed
              ? `Show labels${studioVersion ? ` — Studio ${studioVersion}` : ""}`
              : `Hide labels${studioVersion ? ` — Studio ${studioVersion}` : ""}`
          }
          aria-label={collapsed ? "Show rail labels" : "Hide rail labels"}
          className="rounded p-0.5 text-fgsubtle transition-colors hover:bg-muted hover:text-fg"
        >
          <svg
            width="14"
            height="14"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            className={cn("transition-transform", collapsed && "rotate-180")}
            aria-hidden="true"
          >
            <polyline points="15 18 9 12 15 6" />
          </svg>
        </button>
      </div>

      <nav className="flex-1 space-y-0.5 overflow-y-auto p-1">
        {primary.map(renderItem)}

        {/* Advanced — one click away rather than absent. Only split out in the
            simplified interface; with it off the rail is flat, which is what a
            Pegasus-literate user asked for by turning it off. */}
        {advanced.length > 0 && (
          <>
            <div className="mx-2 my-1.5 border-t border-line" />
            {advancedOpen ? (
              advanced.map(renderItem)
            ) : (
              <button
                onClick={() =>
                  updatePrefs.mutate({ ...prefs, nav_advanced_open: true })
                }
                title="More — resources and terminal"
                className={cellClass(false)}
              >
                <Glyph name="more" />
                {cellLabel("More")}
              </button>
            )}
          </>
        )}

        <div className="mx-2 my-1.5 border-t border-line" />

        {panelButton("chat", "Chat", chatOpen, toggleChat)}
        {/* Terminal is an escape hatch, not a primary tool. In the simplified
            interface it sits behind the same disclosure as Resources; with the
            interface off it stays where a power user expects it. */}
        {(!simplified || advancedOpen) &&
          panelButton("terminal", "Terminal", terminalOpen, toggleTerminal)}
      </nav>

      {/* Identity lives here rather than floating over the page (#276).
          Always in its compact form — the username and chevron do not fit a
          rail at either width, and the menu itself still carries the name. */}
      <div className="border-t border-line p-1">
        <UserMenu collapsed />
      </div>
    </aside>
  );
}
