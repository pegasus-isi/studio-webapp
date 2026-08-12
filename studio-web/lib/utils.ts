import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/** Merge Tailwind classes, resolving conflicts. */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
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

/** Format a status string into a display badge color (light + dark variants). */
export function statusColor(status: string): string {
  switch (status) {
    case "running":
      return "bg-blue-100 text-blue-700 dark:bg-blue-500/15 dark:text-blue-300";
    case "succeeded":
      return "bg-green-100 text-green-700 dark:bg-emerald-500/15 dark:text-emerald-300";
    case "failed":
      return "bg-red-100 text-red-700 dark:bg-rose-500/15 dark:text-rose-300";
    case "installed":
      return "bg-gray-100 text-gray-700 dark:bg-slate-500/20 dark:text-slate-200";
    case "planning":
    case "planned":
      return "bg-yellow-100 text-yellow-700 dark:bg-amber-500/15 dark:text-amber-300";
    case "draft":
      return "bg-gray-100 text-gray-500 dark:bg-slate-500/15 dark:text-slate-400";
    case "generated":
      return "bg-purple-100 text-purple-700 dark:bg-violet-500/15 dark:text-violet-300";
    default:
      return "bg-gray-100 text-gray-600 dark:bg-slate-500/15 dark:text-slate-300";
  }
}
