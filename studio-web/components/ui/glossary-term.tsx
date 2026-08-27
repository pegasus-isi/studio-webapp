"use client";

/**
 * A term with its definition one click away (#241, Phase 6).
 *
 * Backed by `lib/vocabulary.ts`, which until now has been a decision recorded
 * but not consumed. The glossary is the payoff: every word the redesign chose
 * carries the Pegasus term it replaced, so a user who meets "held" in a log
 * file, in Chat, or in Pegasus's own documentation can connect it back to the
 * "Stuck" they saw here.
 *
 * A tooltip on hover alone would be useless on touch and invisible to keyboard
 * users, so this is a button with a popover — reachable by tab, dismissible by
 * Escape.
 */

import { useEffect, useRef, useState } from "react";
import { GLOSSARY } from "@/lib/vocabulary";

export function GlossaryTerm({ term }: { term: string }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLSpanElement>(null);

  const entry = GLOSSARY.find(
    (e) => e.term.toLowerCase() === term.toLowerCase()
  );

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

  // An unknown term renders as plain text rather than a dead affordance: a "?"
  // that explains nothing is worse than no "?".
  if (!entry) return null;

  return (
    <span ref={ref} className="relative inline-block">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-label={`What does "${entry.term}" mean?`}
        className="ml-1 inline-flex h-4 w-4 items-center justify-center rounded-full border border-line text-[10px] font-semibold text-fgsubtle align-middle hover:border-pegasus-400 hover:text-pegasus-600 dark:hover:text-pegasus-300"
      >
        ?
      </button>
      {open && (
        <span
          role="tooltip"
          className="absolute left-0 top-6 z-40 block w-64 rounded-lg border border-line bg-surface p-3 text-left shadow-lg"
        >
          <span className="block text-xs font-semibold text-fg">
            {entry.term}
          </span>
          <span className="mt-1 block text-xs leading-relaxed text-fgmuted">
            {entry.definition}
          </span>
          {entry.alsoKnownAs && (
            // The Pegasus word is deliberately kept, not hidden: it is what
            // they will meet in logs and in Pegasus's own docs, and withholding
            // it strands them there.
            <span className="mt-1.5 block text-[11px] text-fgsubtle">
              Pegasus calls this “{entry.alsoKnownAs}”.
            </span>
          )}
        </span>
      )}
    </span>
  );
}

/** The whole glossary, for the welcome card and any "what do these mean" view. */
export function GlossaryList() {
  return (
    <dl className="space-y-2">
      {GLOSSARY.map((entry) => (
        <div key={entry.term}>
          <dt className="text-xs font-semibold text-fg">
            {entry.term}
            {entry.alsoKnownAs && (
              <span className="ml-1.5 font-normal text-fgsubtle">
                (Pegasus: {entry.alsoKnownAs})
              </span>
            )}
          </dt>
          <dd className="text-xs leading-relaxed text-fgmuted">
            {entry.definition}
          </dd>
        </div>
      ))}
    </dl>
  );
}
