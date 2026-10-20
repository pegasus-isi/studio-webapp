"use client";

/**
 * A "?" that opens a short help panel (#588). The same affordance as
 * GlossaryTerm, for help that is not a vocabulary word: a button, not a
 * hover tooltip, so it works on touch and from the keyboard, and Escape or
 * a click elsewhere closes it.
 */

import { useEffect, useRef, useState, type ReactNode } from "react";

export function HelpPopover({
  label,
  title,
  children,
  width = "w-80",
}: {
  /** Accessible name of the button, e.g. "How to find your account". */
  label: string;
  title?: string;
  children: ReactNode;
  width?: string;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    const onClick = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onClick);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onClick);
    };
  }, [open]);

  return (
    <span ref={ref} className="relative inline-block">
      <button
        type="button"
        // Keep this outside any <label>: a label takes the first labelable
        // element inside it as its control, and a button is one, so the
        // field would lose its name (#590). Pair the label with htmlFor.
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-label={label}
        title={label}
        className="ml-1 inline-flex h-4 w-4 items-center justify-center rounded-full border border-line text-2xs font-semibold text-fgsubtle align-middle hover:border-pegasus-400 hover:text-pegasus-600 dark:hover:text-pegasus-300"
      >
        ?
      </button>
      {open && (
        <span
          role="dialog"
          aria-label={title ?? label}
          className={`absolute left-0 top-6 z-40 block ${width} rounded-lg border border-line bg-surface p-3 text-left font-normal normal-case tracking-normal shadow-lg`}
        >
          {title && <span className="block text-xs font-semibold text-fg">{title}</span>}
          <span className="mt-1 block space-y-1.5 text-xs leading-relaxed text-fgmuted">{children}</span>
        </span>
      )}
    </span>
  );
}
