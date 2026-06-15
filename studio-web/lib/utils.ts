import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/** Merge Tailwind classes, resolving conflicts. */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
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
