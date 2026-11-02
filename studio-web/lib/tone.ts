/**
 * One status palette for the whole app (#548).
 *
 * The same state used to wear a different colour on every screen: a
 * running step was blue on its badge, cyan in the DAG, plain blue text on
 * the run page and brand purple on the progress bar; an installed tool was
 * grey, the colour that means "off" everywhere else; light mode used
 * green/red/gray while dark mode used emerald/rose/slate. Everything that
 * shows a state now takes its colour from here.
 *
 * - active: brand (pegasus). Work in progress. Deliberately not green,
 *   amber or red, so "running" never reads as a verdict.
 * - ok: emerald. Done, healthy, capacity free.
 * - warn: amber. Waiting, busy, held, needs attention.
 * - error: red (rose in dark mode, as the rest of the app already does).
 * - off: neutral. Not started, unavailable, nothing to report.
 *
 * The DAG draws with inline styles and also exports a standalone SVG, so it
 * needs raw colours: TONE_HEX is the same palette in hex, taken from the
 * Tailwind scale (and the pegasus scale in tailwind.config.ts).
 */

export type StatusTone = "active" | "ok" | "warn" | "error" | "off";

/** Map a run/job/tool status string to its tone. Unknown states are "off". */
export function statusTone(status: string): StatusTone {
  switch (status) {
    case "running":
    case "active":
    case "starting":
    case "submitting":
      return "active";
    case "succeeded":
    case "success":
    case "done":
    case "completed":
    case "installed":
      return "ok";
    case "failed":
    case "error":
      return "error";
    case "planning":
    case "planned":
    case "pending":
    case "queued":
    case "idle":
    case "held":
      return "warn";
    default:
      return "off";
  }
}

/** Pill/badge background + text. */
export const TONE_BADGE: Record<StatusTone, string> = {
  active: "bg-pegasus-100 text-pegasus-700 dark:bg-pegasus-400/15 dark:text-pegasus-300",
  ok: "bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300",
  warn: "bg-amber-100 text-amber-800 dark:bg-amber-500/15 dark:text-amber-300",
  error: "bg-red-100 text-red-700 dark:bg-rose-500/15 dark:text-rose-300",
  off: "bg-muted text-fgmuted",
};

/** Inline text (a count, a word, a check mark). */
export const TONE_TEXT: Record<StatusTone, string> = {
  active: "text-pegasus-700 dark:text-pegasus-300",
  ok: "text-emerald-700 dark:text-emerald-400",
  warn: "text-amber-700 dark:text-amber-400",
  error: "text-red-600 dark:text-rose-400",
  off: "text-fgsubtle",
};

/** Status dots. Matches the Resources page's TONE_DOT. */
export const TONE_DOT_CLASS: Record<StatusTone, string> = {
  active: "bg-pegasus-500",
  ok: "bg-emerald-500",
  warn: "bg-amber-500",
  error: "bg-red-500",
  off: "bg-fgsubtle",
};

/** Progress-bar fill. */
export const TONE_BAR: Record<StatusTone, string> = {
  active: "bg-pegasus-600",
  ok: "bg-emerald-500",
  warn: "bg-amber-500",
  error: "bg-red-500",
  off: "bg-fgsubtle",
};

/** The same palette as raw colours, for inline styles and exported SVG. */
export const TONE_HEX: Record<StatusTone, { bg: string; border: string; fg: string }> = {
  active: { bg: "#cdeafd", border: "#0ea5e9", fg: "#075985" }, // pegasus 100 / 500 / 800
  ok: { bg: "#d1fae5", border: "#10b981", fg: "#065f46" }, // emerald 100 / 500 / 800
  warn: { bg: "#fef3c7", border: "#f59e0b", fg: "#78350f" }, // amber 100 / 500 / 900
  error: { bg: "#fee2e2", border: "#ef4444", fg: "#7f1d1d" }, // red 100 / 500 / 900
  off: { bg: "#f3f4f6", border: "#d1d5db", fg: "#374151" }, // gray 100 / 300 / 700
};
