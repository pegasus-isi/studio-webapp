"use client";

import { useEffect } from "react";
import { cn } from "@/lib/utils";

interface Props {
  title: string;
  message: string;
  confirmLabel?: string;
  cancelLabel?: string;
  /** Style the confirm button as destructive (red). */
  destructive?: boolean;
  /** Disable the buttons while the action is in flight. */
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

/**
 * Small modal that asks the user to confirm an action before it runs. Used for
 * destructive operations (e.g. deleting a run). Click the backdrop or press
 * Escape to cancel.
 */
export function ConfirmDialog({
  title,
  message,
  confirmLabel = "Confirm",
  cancelLabel = "Cancel",
  destructive = false,
  busy = false,
  onConfirm,
  onCancel,
}: Props) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !busy) onCancel();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel, busy]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onClick={() => !busy && onCancel()}
    >
      <div
        role="dialog"
        aria-modal="true"
        onClick={(e) => e.stopPropagation()}
        className="w-full max-w-md rounded-xl border border-line bg-surface p-5 shadow-xl"
      >
        <h3 className="text-base font-semibold text-fg">{title}</h3>
        <p className="mt-2 text-sm leading-relaxed text-fgmuted">{message}</p>
        <div className="mt-5 flex justify-end gap-2">
          <button
            onClick={onCancel}
            disabled={busy}
            className="rounded-md border border-line bg-surface px-4 py-2 text-sm font-medium text-fg hover:bg-base disabled:opacity-50"
          >
            {cancelLabel}
          </button>
          <button
            onClick={onConfirm}
            disabled={busy}
            className={cn(
              "rounded-md px-4 py-2 text-sm font-medium text-white disabled:opacity-50",
              destructive
                ? "bg-red-600 hover:bg-red-700"
                : "bg-pegasus-600 hover:bg-pegasus-700"
            )}
          >
            {busy ? "Working…" : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
