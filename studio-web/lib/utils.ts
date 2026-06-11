import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/** Merge Tailwind classes, resolving conflicts. */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/** Format a status string into a display badge color. */
export function statusColor(status: string): string {
  switch (status) {
    case "running":
      return "bg-blue-100 text-blue-700";
    case "succeeded":
      return "bg-green-100 text-green-700";
    case "failed":
      return "bg-red-100 text-red-700";
    case "installed":
      return "bg-gray-100 text-gray-700";
    case "planning":
    case "planned":
      return "bg-yellow-100 text-yellow-700";
    case "draft":
      return "bg-gray-100 text-gray-500";
    case "generated":
      return "bg-purple-100 text-purple-700";
    default:
      return "bg-gray-100 text-gray-600";
  }
}
