"use client";

/**
 * The first thing a new user sees (#241, Phase 6).
 *
 * Studio had no onboarding at all: the landing page opened on a grid of ten
 * cards with nothing answering "what is this and what do I do first". This is
 * three steps and a way out, shown once.
 *
 * Deliberately not a tour. A guided overlay walks someone through a product
 * they have no reason to care about yet; three sentences and a button to the
 * examples gets them to a running analysis, which is the only thing that
 * actually teaches the product.
 */

import { useState } from "react";
import { GlossaryList } from "@/components/ui/glossary-term";
import { useUIPreferences, useUpdateUIPreferences } from "@/lib/hooks/use-ui-prefs";
import { AppLink } from "@/components/layout/app-link";

const STEPS = [
  {
    n: 1,
    title: "Pick a starting point",
    body: "Add a ready-made example from the Marketplace, or describe what you want to the assistant and it will build one.",
  },
  {
    n: 2,
    title: "Give it your inputs",
    body: "Fill in the run form, adding your own data files if the analysis needs them. Studio prepares everything else.",
  },
  {
    n: 3,
    title: "Run it and collect the results",
    body: "Press Run and watch the steps. When it finishes, the files it produced are on the Results tab.",
  },
];

export function WelcomeCard() {
  const { prefs, isLoading } = useUIPreferences();
  const update = useUpdateUIPreferences();
  const [showGlossary, setShowGlossary] = useState(false);

  // Never flash for a returning user: the fallback assumes dismissed, so this
  // only renders once we positively know it hasn't been.
  if (isLoading || prefs.welcome_dismissed) return null;

  const dismiss = () => update.mutate({ ...prefs, welcome_dismissed: true });

  return (
    <section className="mb-8 rounded-xl border border-pegasus-300 bg-pegasus-50 p-5 dark:border-pegasus-400/40 dark:bg-pegasus-400/10">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold text-fg">
            New here? Here&apos;s the short version.
          </h2>
          <p className="mt-1 text-sm text-fgmuted">
            Studio runs scientific analyses across many machines for you. You
            don&apos;t need to know how that part works.
          </p>
        </div>
        <button
          onClick={dismiss}
          disabled={update.isPending}
          aria-label="Dismiss the welcome"
          className="shrink-0 rounded px-2 py-0.5 text-sm text-fgmuted hover:bg-surface hover:text-fg"
        >
          ✕
        </button>
      </div>

      <ol className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-3">
        {STEPS.map((step) => (
          <li key={step.n} className="rounded-lg bg-surface p-3">
            <span className="text-xs font-semibold text-pegasus-700 dark:text-pegasus-300">
              Step {step.n}
            </span>
            <span className="mt-0.5 block text-sm font-medium text-fg">
              {step.title}
            </span>
            <span className="mt-1 block text-xs leading-relaxed text-fgmuted">
              {step.body}
            </span>
          </li>
        ))}
      </ol>

      <div className="mt-4 flex flex-wrap items-center gap-3">
        <AppLink
          href="/marketplace"
          className="rounded-md bg-pegasus-600 px-4 py-2 text-sm font-medium text-white hover:bg-pegasus-700"
        >
          Browse examples
        </AppLink>
        <button
          onClick={() => setShowGlossary((v) => !v)}
          aria-expanded={showGlossary}
          className="text-sm font-medium text-pegasus-700 underline-offset-2 hover:underline dark:text-pegasus-300"
        >
          What do the words mean?
        </button>
        <button
          onClick={dismiss}
          disabled={update.isPending}
          className="ml-auto text-sm text-fgmuted hover:text-fg"
        >
          Don&apos;t show this again
        </button>
      </div>

      {showGlossary && (
        <div className="mt-4 rounded-lg bg-surface p-4">
          <GlossaryList />
        </div>
      )}
    </section>
  );
}
