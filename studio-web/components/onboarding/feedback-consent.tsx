"use client";

/**
 * Early-user feedback program: the "what we collect" notice, the
 * "Ask me for feedback" switch, and the four-question profile.
 *
 * Shared by the closing steps of the guided tour and the Feedback & privacy
 * tab in Settings, so the two can never describe the collection differently —
 * a notice that says one thing at first run and another in Settings is worse
 * than no notice at all.
 */

import { useEffect, useId, useState } from "react";
import * as api from "@/lib/api/client";
import { cn } from "@/lib/utils";

/** The notification kind the feedback prompts are raised under. Muting it is
 *  the whole off switch: the engine drops a muted kind before it can toast. */
export const FEEDBACK_NOTIFICATION_KIND = "feedback";

/**
 * The plain-language notice. `where` changes only the pointer at the controls:
 * in Settings they are on the same page ("below"); in the tour they are not.
 */
export function CollectionNotice({
  where,
  className,
}: {
  where: "tour" | "settings";
  className?: string;
}) {
  const there = where === "settings" ? "below" : "in Settings";
  return (
    <div className={cn("space-y-2 text-xs leading-relaxed text-fgmuted", className)}>
      <p>
        Studio is in early access. To improve it we record when you reach
        milestones (your first workflow, first run and first success), the
        ratings and answers you give to feedback prompts, and which parts of
        Studio you use.
      </p>
      <p>
        We do not read your chat messages unless you turn on content sharing{" "}
        {there}. You can switch off feedback prompts {there} at any time.
      </p>
    </div>
  );
}

const ROLE_OPTIONS: [api.ProfileRole, string][] = [
  ["graduate_student", "Graduate student"],
  ["postdoc", "Postdoc"],
  ["research_scientist", "Research scientist or staff"],
  ["faculty", "Faculty"],
  ["research_software_engineer", "Research software engineer"],
  ["other", "Other"],
];

const EXPERIENCE_OPTIONS: [api.PegasusExperience, string][] = [
  ["never", "Never"],
  ["tried", "Tried once or twice"],
  ["regular", "Use it regularly"],
];

type Draft = Omit<api.UserProfile, "answered_at">;

const EMPTY: Draft = {
  research_field: null,
  role: null,
  pegasus_experience: null,
  coding_comfort: null,
};

/**
 * The four optional questions.
 *
 * Every field may be left blank, and saving with all of them blank is still
 * recorded (it stamps `answered_at`), because "asked and declined" is a
 * different fact from "never asked" and the tour uses it to not ask again.
 *
 * `onDone(saved)` fires after a successful save (true) or a skip (false);
 * with `onSkip` absent no Skip button is shown, which is the Settings case.
 */
export function ProfileForm({
  onDone,
  onSkip,
  saveLabel = "Save",
  compact = false,
}: {
  onDone?: (saved: boolean) => void;
  onSkip?: () => void;
  saveLabel?: string;
  compact?: boolean;
}) {
  const id = useId();
  const [draft, setDraft] = useState<Draft | null>(null);
  const [answeredAt, setAnsweredAt] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [savedOnce, setSavedOnce] = useState(false);

  useEffect(() => {
    let live = true;
    api
      .getUserProfile()
      .then((p) => {
        if (!live) return;
        const { answered_at, ...rest } = p;
        setDraft(rest);
        setAnsweredAt(answered_at);
      })
      // A failed load leaves an empty form rather than none: the questions are
      // still answerable, and the save will surface any real problem.
      .catch(() => live && setDraft(EMPTY));
    return () => {
      live = false;
    };
  }, []);

  const set = <K extends keyof Draft>(key: K, value: Draft[K]) => {
    setDraft((d) => ({ ...(d ?? EMPTY), [key]: value }));
    setSavedOnce(false);
  };

  const save = async () => {
    if (!draft || saving) return;
    setSaving(true);
    setError(null);
    try {
      const saved = await api.updateUserProfile({
        ...draft,
        research_field: draft.research_field?.trim() || null,
      });
      const { answered_at, ...rest } = saved;
      setDraft(rest);
      setAnsweredAt(answered_at);
      setSavedOnce(true);
      onDone?.(true);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  if (draft === null) {
    return <p className="text-xs text-fgsubtle">Loading…</p>;
  }

  const fieldCls =
    "mt-1 block w-full rounded-md border border-line bg-surface px-2.5 py-1.5 text-sm text-fg focus:border-pegasus-500 focus:ring-pegasus-500";
  const labelCls = "block text-xs font-medium text-fg";

  return (
    <form
      className={cn(compact ? "space-y-2.5" : "space-y-3")}
      onSubmit={(e) => {
        e.preventDefault();
        void save();
      }}
    >
      <div>
        <label htmlFor={`${id}-field`} className={labelCls}>
          Research field
        </label>
        <input
          id={`${id}-field`}
          type="text"
          maxLength={200}
          placeholder="e.g. hydrology, genomics"
          value={draft.research_field ?? ""}
          onChange={(e) => set("research_field", e.target.value || null)}
          className={fieldCls}
        />
      </div>

      <div>
        <label htmlFor={`${id}-role`} className={labelCls}>
          Role
        </label>
        <select
          id={`${id}-role`}
          value={draft.role ?? ""}
          onChange={(e) =>
            set("role", (e.target.value || null) as api.ProfileRole | null)
          }
          className={fieldCls}
        >
          <option value="">Prefer not to say</option>
          {ROLE_OPTIONS.map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
      </div>

      <div>
        <label htmlFor={`${id}-exp`} className={labelCls}>
          Experience with Pegasus or HTCondor
        </label>
        <select
          id={`${id}-exp`}
          value={draft.pegasus_experience ?? ""}
          onChange={(e) =>
            set(
              "pegasus_experience",
              (e.target.value || null) as api.PegasusExperience | null
            )
          }
          className={fieldCls}
        >
          <option value="">Prefer not to say</option>
          {EXPERIENCE_OPTIONS.map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
      </div>

      <fieldset>
        <legend className={labelCls}>
          How comfortable are you writing code?
        </legend>
        <div
          role="radiogroup"
          aria-label="Coding comfort, 1 (not at all) to 5 (very)"
          className="mt-1 flex items-center gap-1.5"
        >
          <span className="text-[11px] text-fgsubtle">Not at all</span>
          {[1, 2, 3, 4, 5].map((n) => {
            const on = draft.coding_comfort === n;
            return (
              <button
                key={n}
                type="button"
                role="radio"
                aria-checked={on}
                aria-label={`${n} of 5`}
                // Clicking the selected value clears it: every question is
                // optional, and a radio set with no way back to "unanswered"
                // would make this one quietly mandatory once touched.
                onClick={() => set("coding_comfort", on ? null : n)}
                className={cn(
                  "h-7 w-7 rounded-md border text-xs font-medium",
                  on
                    ? "border-pegasus-600 bg-pegasus-600 text-white"
                    : "border-line text-fgmuted hover:text-fg"
                )}
              >
                {n}
              </button>
            );
          })}
          <span className="text-[11px] text-fgsubtle">Very</span>
        </div>
      </fieldset>

      {error && (
        <p className="text-xs text-red-700 dark:text-rose-400">
          Couldn&apos;t save: {error}
        </p>
      )}

      <div className="flex items-center gap-2 pt-1">
        {!onSkip && (
          <span className="text-[11px] text-fgsubtle" aria-live="polite">
            {savedOnce
              ? "Saved."
              : answeredAt
                ? `Last saved ${new Date(answeredAt).toLocaleDateString()}.`
                : "All questions are optional."}
          </span>
        )}
        {onSkip && (
          <button
            type="button"
            onClick={onSkip}
            className="ml-auto rounded-lg px-2 py-1 text-xs font-medium text-fgmuted hover:text-fg"
          >
            Skip
          </button>
        )}
        <button
          type="submit"
          disabled={saving}
          className={cn(
            "rounded-lg bg-pegasus-600 px-2.5 py-1 text-xs font-semibold text-white hover:bg-pegasus-700 disabled:opacity-50",
            !onSkip && "ml-auto"
          )}
        >
          {saving ? "Saving…" : saveLabel}
        </button>
      </div>
    </form>
  );
}

/**
 * "Ask me for feedback" — mutes or unmutes the `feedback` notification kind.
 *
 * Reads the muted list fresh before every write rather than keeping its own
 * copy: the same list is edited from the Notifications tab, and a PUT built
 * from a stale copy would silently unmute whatever was muted there since.
 */
export function FeedbackPromptsToggle() {
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .getNotificationPrefs()
      .then((p) => setEnabled(!p.muted.includes(FEEDBACK_NOTIFICATION_KIND)))
      .catch((e) => setError((e as Error).message));
  }, []);

  const toggle = async (next: boolean) => {
    if (saving || enabled === null) return;
    setSaving(true);
    setError(null);
    const prev = enabled;
    setEnabled(next); // optimistic
    try {
      const current = (await api.getNotificationPrefs()).muted;
      const muted = next
        ? current.filter((k) => k !== FEEDBACK_NOTIFICATION_KIND)
        : Array.from(new Set([...current, FEEDBACK_NOTIFICATION_KIND]));
      const saved = await api.updateNotificationPrefs(muted);
      setEnabled(!saved.muted.includes(FEEDBACK_NOTIFICATION_KIND));
    } catch (e) {
      setEnabled(prev);
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const on = enabled === true;

  return (
    <div>
      <div className="flex items-start justify-between gap-4">
        <div>
          <h3 className="text-sm font-semibold text-fg">Ask me for feedback</h3>
          <p className="mt-1 text-xs leading-relaxed text-fgmuted">
            Occasional short questions, for example after a run fails and you
            try again. Turning this off stops the prompts; anything you already
            answered is kept.
          </p>
        </div>
        <button
          role="switch"
          aria-checked={on}
          aria-label="Ask me for feedback"
          disabled={enabled === null || saving}
          onClick={() => toggle(!on)}
          className={cn(
            "relative mt-0.5 inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors disabled:opacity-50",
            on ? "bg-pegasus-600" : "bg-muted"
          )}
        >
          <span
            className={cn(
              "inline-block h-4 w-4 transform rounded-full bg-white transition-transform",
              on ? "translate-x-4" : "translate-x-0.5"
            )}
          />
        </button>
      </div>
      {error && (
        <p className="mt-2 text-xs text-red-700 dark:text-rose-400">{error}</p>
      )}
    </div>
  );
}
