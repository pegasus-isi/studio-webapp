"use client";

import type { ReactNode } from "react";
import { usePathname } from "next/navigation";
import { stripAppBasePath } from "@/lib/urls";
import { AppLink } from "@/components/layout/app-link";
import { UserMenu } from "@/components/layout/user-menu";
import { cn } from "@/lib/utils";
import { usePanelStore } from "@/lib/stores/panel-store";
import {
  useUIPreferences,
  useUpdateUIPreferences,
} from "@/lib/hooks/use-ui-prefs";

/**
 * The left rail (#314).
 *
 * Icons with a short label underneath. An unlabelled rail read as an
 * instrument panel but made a first-time user hover seven targets to find out
 * what they were, so the label earns its 12px back. Emoji are gone: they
 * render as a different glyph on every platform and cannot take a colour from
 * state.
 *
 * Settings sits here rather than only in the account menu — it is a place you
 * go, not a property of your account — and identity stays pinned at the
 * bottom, where it is always visible and costs the content area nothing.
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
  settings: (
    <>
      <circle cx="12" cy="12" r="3" />
      <path d="M12 2.5v2.2M12 19.3v2.2M4.6 4.6l1.6 1.6M17.8 17.8l1.6 1.6M2.5 12h2.2M19.3 12h2.2M4.6 19.4l1.6-1.6M17.8 6.2l1.6-1.6" />
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
  /** Anchor for the guided tour (#317). */
  tour?: string;
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
  { href: "/home", label: "Home", icon: "home", tour: "nav-home" },
  { href: "/workflows", label: "Analyses", icon: "analyses", tour: "nav-analyses" },
  // Next to analyses because "add my data" and "run my analysis" are two
  // halves of one task (#241, Phase 2).
  { href: "/data", label: "Data", icon: "data", tour: "nav-data" },
  { href: "/marketplace", label: "Examples", icon: "examples", tour: "nav-examples" },
  // Where your work runs and whether there is room: since #468 it also holds
  // the cluster catalog and allocation, which a user has to be able to find,
  // so it is no longer behind "More".
  { href: "/provisioning", label: "Resources", icon: "compute", tour: "nav-resources" },
  // Notebooks is a Studio page that frames JupyterLab, not a link out of the
  // app: leaving Studio to open a notebook cost you the rail, the assistant
  // and your analyses. The page keeps an "Open in new tab" button for when
  // that is what you actually want.
  {
    href: "/notebooks",
    label: "Notebooks",
    icon: "notebooks",
    tour: "nav-notebooks",
  },
];

export function Sidebar() {
  const pathname = usePathname();
  const activePathname = stripAppBasePath(pathname);
  const chatOpen = usePanelStore((s) => s.chatOpen);
  const terminalOpen = usePanelStore((s) => s.terminalOpen);
  const toggleChat = usePanelStore((s) => s.toggleChat);
  const toggleTerminal = usePanelStore((s) => s.toggleTerminal);
  const { prefs } = useUIPreferences();
  const updatePrefs = useUpdateUIPreferences();

  // With the simplified interface off, the rail stays flat: someone who turned
  // it off is telling us they want the machinery in reach, and hiding half of
  // it behind a disclosure would be answering a different question.
  const simplified = prefs.simple_run;
  // Resources used to sit behind "More" with the terminal; it moved into the
  // main list in #468, so only the terminal panel is still tucked away.
  const primary = PRIMARY_NAV;
  // In a rail there is nowhere to put a <details> summary, so the disclosure
  // Phase 7 introduced becomes a "More" cell that expands the group in place.
  const advancedOpen = prefs.nav_advanced_open;

  const cellClass = (active: boolean) =>
    cn(
      "flex w-full flex-col items-center gap-1 rounded-xl px-1 py-2 transition-colors",
      active
        ? "bg-pegasus-50 text-pegasus-700 dark:bg-pegasus-400/10 dark:text-pegasus-300"
        : "text-fgsubtle hover:bg-muted hover:text-fg"
    );

  // Short enough to sit under a 18px glyph in a 72px rail without wrapping.
  const cellLabel = (label: string) => (
    <span className="text-[9.5px] font-semibold leading-none">{label}</span>
  );

  const renderItem = (item: NavItem) => {
    if (item.external) {
      return (
        <a
          key={item.href}
          href={item.href}
          target="_blank"
          rel="noopener noreferrer"
          title={`${item.label} — opens in a new tab`}
          data-tour={item.tour}
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
        data-tour={item.tour}
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
    onClick: () => void,
    tour?: string
  ): ReactNode => (
    <button
      onClick={onClick}
      title={label}
      data-tour={tour}
      aria-pressed={open}
      className={cellClass(open)}
    >
      <Glyph name={icon} />
      {cellLabel(label)}
    </button>
  );

  return (
    <aside
      data-tour="nav"
      className="flex w-[72px] shrink-0 flex-col border-r border-line bg-surface"
    >
      <nav className="flex flex-1 flex-col gap-0.5 overflow-y-auto p-1.5">
        {primary.map(renderItem)}

        {/* Advanced — one click away rather than absent. Only split out in the
            simplified interface; with it off the rail is flat, which is what a
            Pegasus-literate user asked for by turning it off. */}
        {simplified && !advancedOpen && (
          <>
            <div className="mx-auto my-1 h-px w-7 bg-line" />
            <button
              onClick={() => updatePrefs.mutate({ nav_advanced_open: true })}
              title="More — terminal"
              aria-label="Show more destinations"
              className={cellClass(false)}
            >
              <Glyph name="more" />
              {cellLabel("More")}
            </button>
          </>
        )}

        <div className="mx-auto my-1 h-px w-7 bg-line" />

        {panelButton("chat", "Chat", chatOpen, toggleChat, "nav-chat")}
        {/* Terminal is an escape hatch, not a primary tool. In the simplified
            interface it sits behind the same disclosure as Resources; with the
            interface off it stays where a power user expects it. */}
        {(!simplified || advancedOpen) &&
          panelButton("terminal", "Terminal", terminalOpen, toggleTerminal)}

        {/* Settings is a place you go, not a property of your account, so it
            gets a rail cell as well as its entry in the account menu. */}
        <div className="mx-auto my-1 h-px w-7 bg-line" />
        {renderItem({
          href: "/settings",
          label: "Settings",
          icon: "settings",
          tour: "nav-settings",
        })}
      </nav>

      {/* Identity, pinned. Always visible and it costs the content area
          nothing, which is why it moved out of the top bar (#276). */}
      <div className="border-t border-line p-1.5" data-tour="nav-account">
        <UserMenu collapsed />
      </div>
    </aside>
  );
}
