import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";
import { TONE_BADGE, statusTone } from "@/lib/tone";

/** Merge Tailwind classes, resolving conflicts. */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/** A count with locale thousands separators: 18234 -> "18,234". */
export function formatCount(n: number): string {
  return Number.isFinite(n) ? n.toLocaleString() : "—";
}

/** Human-friendly size: 512 B, 3.4 MB, 120 GB. */
export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value < 10 ? value.toFixed(1) : Math.round(value)} ${units[unit]}`;
}

/** Human-friendly duration: 45s, 12m, 1h 05m. */
export function formatDuration(seconds: number): string {
  // Guard NaN/Infinity/negative up front (FE-9): NaN slipped past the `< 60`
  // check and rendered "NaNm"/"NaNh".
  if (!Number.isFinite(seconds) || seconds < 0) return "—";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  // Floor, not round: round(3570/60) = 60 produced "60m" instead of "59m".
  const totalMinutes = Math.floor(seconds / 60);
  if (totalMinutes < 60) return `${totalMinutes}m`;
  const hours = Math.floor(totalMinutes / 60);
  const rest = totalMinutes % 60;
  return rest ? `${hours}h ${String(rest).padStart(2, "0")}m` : `${hours}h`;
}

/**
 * Badge colour for a status string (light + dark), from the app-wide
 * palette in lib/tone.ts. "generated" (a workflow written but not yet
 * planned) keeps its own violet: it is a lifecycle step, not a health
 * state, and no tone fits it.
 */
export function statusColor(status: string): string {
  if (status === "generated") {
    return "bg-violet-100 text-violet-700 dark:bg-violet-500/15 dark:text-violet-300";
  }
  return TONE_BADGE[statusTone(status)];
}
