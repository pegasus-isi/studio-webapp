"use client";

/**
 * The "Did the AI help?" card and the "Want the AI to look?" nudge.
 *
 * Never a modal: it sits in the chat panel above the composer, or on the
 * dashboard when the chat panel is closed, and goes away on its own once
 * answered. Whether there is a prompt at all is the server's call (see
 * lib/feedback/feedback.ts); this only renders it, and holds it back while
 * the AI is streaming — a question about the AI's help, asked while it is
 * still mid-answer, gets answered about the wrong thing.
 */

import { useEffect, useState } from "react";
import * as api from "@/lib/api/client";
import {
  askAboutFailedRun,
  feedbackSessionId,
  useFeedbackStore,
} from "@/lib/feedback/feedback";
import { usePanelStore } from "@/lib/stores/panel-store";
import { cn } from "@/lib/utils";

const HELP_CHOICES: { answer: api.FeedbackAnswer; label: string }[] = [
  { answer: "fixed", label: "Fixed it" },
  { answer: "partly", label: "Partly" },
  { answer: "no_help", label: "Didn't help" },
  { answer: "did_not_try", label: "Didn't try it" },
];

/** Answers where "what was missing?" is a question with an answer. */
const ASK_WHAT_WAS_MISSING: api.FeedbackAnswer[] = ["partly", "no_help"];

type Phase =
  | { step: "ask" }
  | { step: "comment"; prompt: api.FeedbackPrompt; answer: api.FeedbackAnswer }
  | { step: "thanks" };

export function FeedbackPromptCard({
  placement,
  busy = false,
}: {
  placement: "chat" | "dashboard";
  /** The AI is streaming a reply; hold the card until it finishes. */
  busy?: boolean;
}) {
  const prompt = useFeedbackStore((s) => s.prompt);
  const handle = useFeedbackStore((s) => s.handle);
  const chatOpen = usePanelStore((s) => s.chatOpen);
  const [phase, setPhase] = useState<Phase>({ step: "ask" });
  const [comment, setComment] = useState("");

  // The thank-you is a beat, not a fixture.
  useEffect(() => {
    if (phase.step !== "thanks") return;
    const t = setTimeout(() => setPhase({ step: "ask" }), 2500);
    return () => clearTimeout(t);
  }, [phase]);

  // The dashboard is the fallback for when the chat cannot show the card,
  // which is when the chat is closed.
  if (placement === "dashboard" && chatOpen) return null;

  if (phase.step === "thanks") {
    return (
      <Shell placement={placement}>
        <p role="status" className="text-fgmuted">
          Thanks — that helps us make the assistant better.
        </p>
      </Shell>
    );
  }

  if (phase.step === "comment") {
    const send = (text: string) => {
      const trimmed = text.trim();
      if (trimmed) {
        void api
          .answerEpisode(phase.prompt.episode_id, {
            answer: phase.answer,
            comment: trimmed,
            session: feedbackSessionId(),
          })
          .catch(() => undefined);
      }
      setComment("");
      setPhase({ step: "thanks" });
    };
    return (
      <Shell placement={placement}>
        <label
          htmlFor={`feedback-missing-${placement}`}
          className="block font-medium text-fg"
        >
          What was missing? <span className="text-fgsubtle">(optional)</span>
        </label>
        <textarea
          id={`feedback-missing-${placement}`}
          value={comment}
          onChange={(e) => setComment(e.target.value)}
          rows={2}
          maxLength={2000}
          className="mt-1.5 w-full resize-none rounded-md border border-line bg-surface px-2 py-1.5 text-xs text-fg focus:border-pegasus-500 focus:ring-pegasus-500"
        />
        <div className="mt-1.5 flex justify-end gap-2">
          <button
            type="button"
            onClick={() => send("")}
            className="rounded-md px-2.5 py-1 text-xs font-medium text-fgmuted hover:text-fg"
          >
            Skip
          </button>
          <button
            type="button"
            onClick={() => send(comment)}
            disabled={!comment.trim()}
            className="rounded-md bg-pegasus-600 px-2.5 py-1 text-xs font-semibold text-white hover:bg-pegasus-700 disabled:opacity-50"
          >
            Send
          </button>
        </div>
      </Shell>
    );
  }

  if (!prompt || busy) return null;

  const record = (answer: api.FeedbackAnswer) => {
    handle(prompt);
    // Not awaited: the click is the answer. If the write fails the server
    // still holds the prompt as pending, and it comes back on a later poll —
    // a retry, rather than a lost answer.
    void api
      .answerEpisode(prompt.episode_id, {
        answer,
        session: feedbackSessionId(),
      })
      .catch(() => undefined);
  };

  const dismiss = () => {
    record("dismissed");
    setPhase({ step: "ask" });
  };

  const isNudge = prompt.kind === "nudge";
  const name = prompt.workflow || "your workflow";

  return (
    <Shell placement={placement} onDismiss={dismiss}>
      <p className="pr-6 font-medium text-fg">{prompt.message}</p>
      <div className="mt-2 flex flex-wrap gap-1.5" role="group" aria-label={prompt.message}>
        {isNudge ? (
          <>
            <button
              type="button"
              onClick={() => {
                record("ask_ai");
                askAboutFailedRun({
                  runId: prompt.run_id,
                  label: name,
                  draft: `The run of "${name}" failed. Explain what went wrong and how to fix it.`,
                });
              }}
              className="rounded-md bg-pegasus-600 px-2.5 py-1 text-xs font-semibold text-white hover:bg-pegasus-700"
            >
              Ask the AI
            </button>
            <button
              type="button"
              onClick={() => {
                record("fixed_myself");
                setPhase({ step: "thanks" });
              }}
              className={choiceClass}
            >
              Fixed it myself
            </button>
            <button type="button" onClick={dismiss} className={choiceClass}>
              Dismiss
            </button>
          </>
        ) : (
          HELP_CHOICES.map((c) => (
            <button
              key={c.answer}
              type="button"
              onClick={() => {
                record(c.answer);
                setPhase(
                  ASK_WHAT_WAS_MISSING.includes(c.answer)
                    ? { step: "comment", prompt, answer: c.answer }
                    : { step: "thanks" }
                );
              }}
              className={choiceClass}
            >
              {c.label}
            </button>
          ))
        )}
      </div>
    </Shell>
  );
}

const choiceClass =
  "rounded-md border border-line bg-surface px-2.5 py-1 text-xs font-medium text-fg hover:border-pegasus-300 hover:bg-pegasus-50/40 dark:hover:bg-pegasus-400/5";

function Shell({
  placement,
  onDismiss,
  children,
}: {
  placement: "chat" | "dashboard";
  onDismiss?: () => void;
  children: React.ReactNode;
}) {
  return (
    <section
      aria-label="Feedback on the AI assistant"
      className={cn(
        "relative rounded-xl border border-pegasus-300 bg-pegasus-50 px-3.5 py-2.5 text-sm dark:border-pegasus-400/40 dark:bg-pegasus-400/10",
        placement === "dashboard" && "mx-auto mb-6 w-full max-w-3xl"
      )}
    >
      {onDismiss && (
        <button
          type="button"
          onClick={onDismiss}
          aria-label="Dismiss this question"
          title="Dismiss"
          className="absolute right-2 top-2 rounded p-1 text-fgsubtle hover:bg-muted hover:text-fg"
        >
          <svg
            width="12"
            height="12"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.5"
            strokeLinecap="round"
            aria-hidden="true"
          >
            <path d="M6 6l12 12M18 6L6 18" />
          </svg>
        </button>
      )}
      {children}
    </section>
  );
}
