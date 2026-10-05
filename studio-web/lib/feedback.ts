/**
 * The early-user feedback form (Google Forms), opened with the context a
 * report would otherwise lack.
 *
 * A bare "send feedback" link gets "the run failed" with no run, no page and
 * no model, and the follow-up question usually goes unanswered. The form has a
 * "Technical details" section of plain text fields; Google's pre-fill URL
 * (`?usp=pp_url&entry.<id>=value`) puts this context in them, and the user can
 * still see and edit what is sent.
 *
 * The entry ids belong to that one form. Rebuilding the form, or swapping it
 * for another, changes them — regenerate them from the form's "Get pre-filled
 * link" and update both constants together, or fields silently arrive empty.
 */

const FORM_URL =
  "https://docs.google.com/forms/d/e/1FAIpQLScxf48NcVYjpF-dMzpZbB7TCDrrFyOS8q4bNYWRRRtd69RWAQ/viewform";

const ENTRY = {
  page: "entry.410034087",
  workflow: "entry.216327917",
  run: "entry.688262674",
  model: "entry.1133005254",
  agent: "entry.68650138",
  version: "entry.123710869",
  user: "entry.1573709106",
} as const;

export type FeedbackContext = Partial<Record<keyof typeof ENTRY, string | null>>;

export function feedbackFormUrl(ctx: FeedbackContext): string {
  const params = new URLSearchParams({ usp: "pp_url" });
  for (const [key, entry] of Object.entries(ENTRY)) {
    const value = ctx[key as keyof typeof ENTRY];
    // Long values (a prompt pasted as a run label, say) would push the URL
    // past what browsers and Google accept; the start is enough to find it.
    if (value) params.set(entry, value.slice(0, 300));
  }
  return `${FORM_URL}?${params.toString()}`;
}
