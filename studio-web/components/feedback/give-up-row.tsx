"use client";

/**
 * One row under a chat turn the user stopped, or that ended in an engine
 * error: why it went wrong, in one click, then it is gone.
 *
 * A stop is the strongest "this isn't helping" signal the chat produces and
 * the one nobody explains unprompted, so this asks right there — inline under
 * the message, never a dialog, and skippable by simply carrying on.
 */

import type { GiveUpTag } from "@/lib/api/client";

const TAGS: { tag: GiveUpTag; label: string }[] = [
  { tag: "wrong_direction", label: "Wrong direction" },
  { tag: "too_slow", label: "Too slow" },
  { tag: "error", label: "Error" },
  { tag: "other", label: "Other" },
];

export function GiveUpRow({
  reason,
  onPick,
  onClose,
}: {
  reason: "stop" | "error";
  onPick: (tag: GiveUpTag) => void;
  onClose: () => void;
}) {
  const question =
    reason === "stop" ? "Why did you stop it?" : "What went wrong?";
  return (
    <div
      role="group"
      aria-label={question}
      className="flex flex-wrap items-center gap-1.5 text-xs"
    >
      <span className="text-fgmuted">{question}</span>
      {TAGS.map((t) => (
        <button
          key={t.tag}
          type="button"
          onClick={() => onPick(t.tag)}
          className="rounded-full border border-line bg-surface px-2 py-0.5 text-fgmuted hover:border-pegasus-300 hover:text-fg"
        >
          {t.label}
        </button>
      ))}
      <button
        type="button"
        onClick={onClose}
        aria-label="Hide this question"
        title="Hide"
        className="rounded p-0.5 text-fgsubtle hover:text-fg"
      >
        <svg
          width="10"
          height="10"
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
    </div>
  );
}
