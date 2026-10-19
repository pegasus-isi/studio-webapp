"use client";

/**
 * What went wrong, and what to do about it (#241, Phase 5b).
 *
 * Replaces the red boxes of raw stderr and the verbatim `pegasus-analyzer`
 * dump as the *first* thing a user sees after a failure. Both are still here —
 * under "Show technical details" — because a Pegasus-literate user reads them
 * faster than any summary. They just stop being the only thing on offer.
 *
 * ## Every card has an exit
 *
 * The design rule from the plan: a state that demands attention must come with
 * something the user can do. The backend guarantees a non-empty `action` for
 * every card, and this component adds "Explain this failure" on top, so even a
 * cause we failed to classify leads somewhere rather than dead-ending in a
 * stack trace.
 */

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import type { DiagnosisCard } from "@/lib/api/client";
import * as api from "@/lib/api/client";
import { usePanelStore } from "@/lib/stores/panel-store";
import { cn } from "@/lib/utils";

// How much raw log to carry into the chat prompt. Enough for the assistant to
// see the actual error, short enough not to bury the question in a wall of
// text it then has to summarise back.
const EXPLAIN_LOG_CHARS = 2000;

export function FailureCards({
  runId,
  runFinished,
}: {
  runId: string;
  runFinished: boolean;
}) {
  const { data, isLoading } = useQuery({
    queryKey: ["run-diagnosis", runId],
    queryFn: () => api.getRunDiagnosis(runId),
    // While the run is going, new failures keep appearing; once it's done the
    // set is final.
    refetchInterval: runFinished ? false : 15_000,
    retry: false,
  });

  const cards = data?.cards ?? [];
  if (isLoading || cards.length === 0) return null;

  return (
    <section className="space-y-3">
      <h3 className="text-sm font-semibold text-fg">
        {cards.length === 1
          ? "Something went wrong"
          : `${cards.length} problems`}
      </h3>
      {cards.map((card, i) => (
        <FailureCard key={`${card.kind}-${card.step}-${i}`} card={card} />
      ))}
    </section>
  );
}

function FailureCard({ card }: { card: DiagnosisCard }) {
  const [showTechnical, setShowTechnical] = useState(false);
  const openChatWith = usePanelStore((s) => s.openChatWith);

  // Hand the assistant the failure and ask for the explanation in the user's
  // terms. /debug already knows how to read Pegasus logs; this turns invoking
  // it from a slash command a scientist has to know about into a button.
  const explain = () => {
    const log = (card.technical || "").slice(-EXPLAIN_LOG_CHARS);
    const where = card.step ? ` in the step "${card.step}"` : "";
    openChatWith(
      `/debug My workflow failed${where}. Please explain what went wrong in ` +
        `plain language — I'm not a Pegasus user — and tell me what to change ` +
        `to fix it.\n\nWhat Studio reported: ${card.cause}\n\n` +
        (log ? `The error output was:\n\n\`\`\`\n${log}\n\`\`\`\n` : "")
    );
  };

  return (
    <div className="rounded-xl border border-red-300 bg-red-50 p-4 dark:border-rose-500/40 dark:bg-rose-500/10">
      {card.step && (
        <p className="text-2xs font-semibold uppercase tracking-wider text-red-700/70 dark:text-rose-300/70">
          {card.step}
          {card.exitcode != null && card.exitcode !== "" && (
            <span className="font-normal"> · exit {card.exitcode}</span>
          )}
        </p>
      )}

      <p className="mt-0.5 text-sm font-semibold text-red-800 dark:text-rose-200">
        {card.cause}
      </p>
      {/* The action is never optional — see the module docstring. */}
      <p className="mt-1.5 text-sm text-red-700 dark:text-rose-200/90">
        {card.action}
      </p>

      <div className="mt-3 flex flex-wrap items-center gap-3">
        <button
          onClick={explain}
          className="rounded-md bg-pegasus-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-pegasus-700"
        >
          Explain this failure
        </button>
        {card.technical && (
          <button
            onClick={() => setShowTechnical((v) => !v)}
            aria-expanded={showTechnical}
            className={cn(
              "text-xs font-medium underline-offset-2 hover:underline",
              "text-red-700/80 dark:text-rose-300/80"
            )}
          >
            {showTechnical ? "Hide" : "Show"} technical details
          </button>
        )}
      </div>

      {showTechnical && card.technical && (
        <pre className="mt-2 max-h-56 overflow-auto whitespace-pre-wrap [overflow-wrap:anywhere] rounded border border-red-200 bg-white/60 p-3 font-mono text-xs text-red-900 dark:border-rose-500/30 dark:bg-black/20 dark:text-rose-200">
          {card.technical}
        </pre>
      )}
    </div>
  );
}
