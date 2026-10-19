"use client";

/**
 * One shape for every "how is it doing, and by how much" card on the
 * Resources page (#544): Studio's pool, the OSPool and the user's annex.
 *
 * They were three formats (a strip inside the verdict card, a separate box
 * of tiles, a tinted card with smaller uppercase labels), so the same kind
 * of number looked different depending on where it was. Everything here
 * renders through this component: a status dot and headline, an optional
 * action, a strip of figures in one face with tabular digits, and an
 * optional warning footer.
 *
 * The rest of the page's shared look lives here too (section titles, input
 * fields, labels, hints), so the sections stop drifting apart one card at a
 * time. Monospace is for things typed or pasted exactly (commands, paths,
 * ClassAd expressions), never for an ordinary input like a project name.
 */

import { cn } from "@/lib/utils";

export type Tone = "ok" | "warn" | "off";

export const TONE_DOT: Record<Tone, string> = {
  ok: "bg-emerald-500",
  warn: "bg-amber-500",
  off: "bg-fgsubtle",
};

const TONE_BORDER: Record<Tone, string> = {
  ok: "border-green-300 dark:border-emerald-500/40",
  warn: "border-amber-300 dark:border-amber-500/40",
  off: "border-line",
};

/** Section title and subtitle, above the section's card(s). */
export function SectionHeader({ title, subtitle }: { title: string; subtitle?: React.ReactNode }) {
  return (
    <div className="mb-3">
      <h2 className="text-sm font-semibold text-fg">{title}</h2>
      {subtitle && <div className="mt-0.5 text-xs text-fgmuted">{subtitle}</div>}
    </div>
  );
}

/** A plain card; StatusCard is the same shape with a tone. */
export const CARD = "rounded-xl border border-line bg-surface";
/** Text inputs and selects. */
export const FIELD =
  "mt-1 block w-full rounded-md border border-line bg-base px-2.5 py-1.5 text-sm text-fg outline-none focus:border-pegasus-400";
/** A field's label. */
export const LABEL = "block text-xs font-medium text-fgmuted";
/** Help text under a field or at the foot of a card. */
export const HINT = "text-xs text-fgsubtle";

export type StatItem = { label: string; value: string | number };

/** Counts get locale separators; strings ("12 / 48", "—") pass through. */
export const fmt = (v: string | number) => (typeof v === "number" ? v.toLocaleString() : v);

export function StatusCard({
  tone,
  headline,
  detail,
  action,
  stats,
  footer,
  children,
}: {
  tone: Tone;
  headline?: React.ReactNode;
  detail?: React.ReactNode;
  action?: React.ReactNode;
  stats?: StatItem[];
  footer?: React.ReactNode;
  children?: React.ReactNode;
}) {
  return (
    <div className={cn("overflow-hidden rounded-xl border bg-surface", TONE_BORDER[tone])}>
      {headline && (
        <div className="flex flex-wrap items-start gap-3 p-5">
          <span className={cn("mt-2 h-2 w-2 shrink-0 rounded-full", TONE_DOT[tone])} />
          <div className="min-w-0 flex-1">
            <p className="text-base font-semibold text-fg">{headline}</p>
            {detail && <p className="mt-1 text-sm text-fgmuted">{detail}</p>}
            {children}
          </div>
          {action && <div className="flex shrink-0 items-center gap-2">{action}</div>}
        </div>
      )}
      {stats && stats.length > 0 && (
        <dl
          className={cn(
            "grid grid-cols-2 gap-x-6 gap-y-4 px-5 py-4 sm:grid-cols-4",
            headline && "border-t border-line"
          )}
        >
          {stats.map((s) => (
            <div key={s.label} className="min-w-0">
              <dt className="text-xs text-fgmuted">{s.label}</dt>
              <dd className="mt-0.5 text-xl font-semibold tabular-nums text-fg">{fmt(s.value)}</dd>
            </div>
          ))}
        </dl>
      )}
      {footer && (
        <div className="border-t border-amber-300 bg-amber-50 px-5 py-3 text-sm text-amber-800 dark:border-amber-500/40 dark:bg-amber-500/10 dark:text-amber-200">
          {footer}
        </div>
      )}
    </div>
  );
}
