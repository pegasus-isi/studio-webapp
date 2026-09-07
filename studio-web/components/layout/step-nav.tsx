"use client";

/**
 * The step navigator in the top bar: Ask AI → Run Jobs → Notebooks → Resources.
 *
 * The rail lists every place in Studio; this shows the *order* a first-time
 * user is expected to take through it, and where they are on that path. Steps
 * before the current one read as done, later ones as still to come.
 *
 * Pages that are not on the path (Data, Marketplace, Settings) light nothing
 * up rather than pretending to be a step.
 */

import { usePathname } from "next/navigation";
import { AppLink } from "@/components/layout/app-link";
import { stripAppBasePath } from "@/lib/urls";
import { cn } from "@/lib/utils";

const STEPS = [
  { label: "Ask AI",    href: "/home",         also: ["/"] },
  { label: "Run Jobs",  href: "/workflows",    also: [] },
  { label: "Notebooks", href: "/notebooks",    also: [] },
  { label: "Resources", href: "/provisioning", also: [] },
] as const;

export function StepNav() {
  // Under KubeSpawner the browser path carries a /user/<name> prefix that the
  // middleware strips server-side; strip it here too so matching is the same
  // in both deployments.
  const pathname = stripAppBasePath(usePathname());
  const activeIdx = STEPS.findIndex(
    (s) =>
      pathname === s.href ||
      pathname.startsWith(s.href + "/") ||
      (s.also as readonly string[]).includes(pathname),
  );

  return (
    <nav aria-label="Steps" className="hidden items-center md:flex">
      {STEPS.map((step, i) => {
        const active = i === activeIdx;
        const done = activeIdx >= 0 && i < activeIdx;
        return (
          <span key={step.href} className="flex items-center">
            {/* Progress track between steps: full when the step before is
                done, half when it is the current one, empty otherwise. */}
            {i > 0 && (
              <span className="relative mx-1 h-[2px] w-8 overflow-hidden rounded-full bg-line">
                <span
                  className="absolute inset-y-0 left-0 rounded-full bg-accent transition-all duration-500"
                  style={{ width: done ? "100%" : active ? "50%" : "0%" }}
                />
              </span>
            )}

            <AppLink
              href={step.href}
              aria-current={active ? "page" : undefined}
              className="group flex flex-col items-center gap-1"
            >
              {active ? (
                <span className="flex items-center gap-1.5 rounded-full bg-accent px-3 py-1 shadow-md shadow-accent/30 ring-4 ring-accent/10">
                  <span className="flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-white/25 text-[9px] font-black text-white">
                    {i + 1}
                  </span>
                  <span className="text-[11px] font-semibold text-white">{step.label}</span>
                </span>
              ) : (
                <span className="flex flex-col items-center gap-0.5">
                  <span
                    className={cn(
                      "flex h-5 w-5 items-center justify-center rounded-full border-2 text-[10px] font-bold transition-all",
                      done
                        ? "border-accent bg-accent/10 text-accent"
                        : "border-line bg-surface text-fgsubtle group-hover:border-accent/50 group-hover:text-accent/70",
                    )}
                  >
                    {i + 1}
                  </span>
                  <span
                    className={cn(
                      "whitespace-nowrap text-[9px] font-medium transition-colors",
                      done ? "text-fgmuted" : "text-fgsubtle group-hover:text-fgmuted",
                    )}
                  >
                    {step.label}
                  </span>
                </span>
              )}
            </AppLink>
          </span>
        );
      })}
    </nav>
  );
}
