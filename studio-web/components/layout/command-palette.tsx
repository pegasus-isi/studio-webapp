"use client";

/**
 * ⌘K search over the things a user actually looks for: their own analyses, and
 * the examples they could add (#314).
 *
 * Deliberately lazy. The queries are `enabled` only while the palette is open,
 * so the top bar's search box costs nothing on every page — a global search
 * that polls two endpoints in the background to stay warm would be a worse
 * trade than the half-second it takes to load on open.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import * as api from "@/lib/api/client";
import { appPushPath } from "@/lib/urls";
import { cn } from "@/lib/utils";
import { CONTACT_PAGE, PROJECT_SITE, SUPPORT_EMAIL } from "@/components/layout/user-menu";

interface Hit {
  key: string;
  title: string;
  detail: string;
  group: "Analyses" | "Examples" | "Go to";
  href: string;
}

const DESTINATIONS: Hit[] = [
  { key: "go-home", title: "Home", detail: "Ask the assistant", group: "Go to", href: "/home" },
  { key: "go-wf", title: "Analyses", detail: "Your workflows and runs", group: "Go to", href: "/workflows" },
  { key: "go-data", title: "Data", detail: "Files in your workspace", group: "Go to", href: "/data" },
  { key: "go-mkt", title: "Examples", detail: "Ready-made workflows", group: "Go to", href: "/marketplace" },
  { key: "go-res", title: "Resources", detail: "Pool capacity", group: "Go to", href: "/provisioning" },
  { key: "go-set", title: "Application Settings", detail: "Models, tools, preferences", group: "Go to", href: "/settings" },
  { key: "go-site", title: "PegasusAI website", detail: "pegasus-ai.org, in a new tab", group: "Go to", href: PROJECT_SITE },
  { key: "go-contact", title: "Help & contact", detail: `${SUPPORT_EMAIL}, or the contact page in a new tab`, group: "Go to", href: CONTACT_PAGE },
];

export function CommandPalette({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const [q, setQ] = useState("");
  const [active, setActive] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  const projects = useQuery({
    queryKey: ["workflow-projects"],
    queryFn: () => api.getWorkflowProjects(),
    enabled: open,
  });
  const marketplace = useQuery({
    queryKey: ["marketplace"],
    queryFn: () => api.getMarketplace(),
    enabled: open,
  });

  useEffect(() => {
    if (!open) return;
    setQ("");
    setActive(0);
    // A frame, so the input exists before we reach for it.
    const t = requestAnimationFrame(() => inputRef.current?.focus());
    return () => cancelAnimationFrame(t);
  }, [open]);

  const hits = useMemo<Hit[]>(() => {
    const all: Hit[] = [
      ...(projects.data?.projects ?? []).map((p) => ({
        key: `p:${p.project_id}`,
        title: p.name,
        detail:
          p.runs.length > 0
            ? `${p.runs.length} run${p.runs.length !== 1 ? "s" : ""} · ${p.status}`
            : p.status,
        group: "Analyses" as const,
        href: `/workflows?project=${encodeURIComponent(p.project_id)}`,
      })),
      ...(marketplace.data?.workflows ?? []).map((w) => ({
        key: `m:${w.id}`,
        title: w.name,
        detail: w.description || (w.topics ?? []).join(" · ") || w.source,
        group: "Examples" as const,
        href: "/marketplace",
      })),
      ...DESTINATIONS,
    ];
    const needle = q.trim().toLowerCase();
    if (!needle) return all.slice(0, 12);
    return all
      .filter(
        (h) =>
          h.title.toLowerCase().includes(needle) ||
          h.detail.toLowerCase().includes(needle)
      )
      .slice(0, 20);
  }, [q, projects.data, marketplace.data]);

  useEffect(() => setActive(0), [q]);

  if (!open) return null;

  const go = (hit: Hit | undefined) => {
    if (!hit) return;
    onClose();
    // An absolute URL leaves the app: open it beside Studio rather than
    // through appPushPath, which would prefix it as an in-app route.
    if (/^https?:\/\//.test(hit.href)) {
      window.open(hit.href, "_blank", "noopener,noreferrer");
      return;
    }
    window.location.href = appPushPath(hit.href);
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") {
      e.preventDefault();
      onClose();
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((i) => Math.min(i + 1, hits.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((i) => Math.max(i - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      go(hits[active]);
    }
  };

  const loading = projects.isLoading || marketplace.isLoading;
  let lastGroup = "";

  return (
    <div
      className="fixed inset-0 z-[60] flex items-start justify-center bg-black/30 p-4 pt-[12vh]"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Search Studio"
        className="w-full max-w-xl overflow-hidden rounded-xl border border-line bg-surface shadow-2xl"
        onClick={(e) => e.stopPropagation()}
        onKeyDown={onKeyDown}
      >
        <div className="flex items-center gap-2 border-b border-line px-3.5 py-3">
          <svg
            width="15"
            height="15"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.2"
            className="shrink-0 text-fgsubtle"
            aria-hidden="true"
          >
            <circle cx="11" cy="11" r="7" />
            <path d="M20 20l-3.5-3.5" />
          </svg>
          <input
            ref={inputRef}
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search analyses, examples, or jump to a page"
            aria-label="Search analyses, examples, or jump to a page"
            className="w-full border-0 bg-transparent text-sm text-fg outline-none placeholder:text-fgsubtle"
          />
          <kbd className="shrink-0 rounded border border-line px-1.5 py-0.5 font-mono text-2xs text-fgsubtle">
            esc
          </kbd>
        </div>

        <div ref={listRef} className="max-h-80 overflow-auto py-1">
          {loading && hits.length === 0 ? (
            <p className="px-3.5 py-6 text-center text-sm text-fgsubtle">
              Looking…
            </p>
          ) : hits.length === 0 ? (
            <p className="px-3.5 py-6 text-center text-sm text-fgsubtle">
              Nothing matches “{q}”.
            </p>
          ) : (
            hits.map((hit, i) => {
              const header = hit.group !== lastGroup ? hit.group : null;
              lastGroup = hit.group;
              return (
                <div key={hit.key}>
                  {header && (
                    <p className="px-3.5 pb-1 pt-2 text-2xs font-semibold uppercase tracking-wider text-fgsubtle">
                      {header}
                    </p>
                  )}
                  <button
                    onMouseEnter={() => setActive(i)}
                    onClick={() => go(hit)}
                    className={cn(
                      "flex w-full items-baseline gap-2 px-3.5 py-1.5 text-left",
                      i === active ? "bg-pegasus-50 dark:bg-pegasus-400/10" : ""
                    )}
                  >
                    <span className="shrink-0 text-sm font-medium text-fg">
                      {hit.title}
                    </span>
                    <span className="min-w-0 flex-1 truncate text-xs text-fgmuted">
                      {hit.detail}
                    </span>
                  </button>
                </div>
              );
            })
          )}
        </div>
      </div>
    </div>
  );
}
