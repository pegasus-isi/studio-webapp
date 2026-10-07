/**
 * The early-user survey (Google Forms), opened with the user's Studio
 * username filled in.
 *
 * This is the "PegasusAI Studio Early-User Survey": research field, what you
 * tried, ease of use, value, and a contact section. It is the form the rail
 * Feedback cell points at, so the button is one click from anywhere and the
 * answers can be joined to the user's runs through the username.
 *
 * Google's pre-fill URL (`?usp=pp_url&entry.<id>=value`) fills the username
 * field; the user still sees and can edit it. The survey has no technical
 * details section (page, run, model), so nothing else is passed — the quick
 * bug-report form that carried those was replaced by this survey.
 *
 * The entry id belongs to that one form. Rebuilding the form, or swapping it
 * for another, changes it — regenerate it from the form's "Get pre-filled
 * link" and update both constants together, or the field silently arrives
 * empty.
 */

const FORM_URL =
  "https://docs.google.com/forms/d/e/1FAIpQLSeEJdtddoXKb8NoNkCQ06gs5T8_la2DIUFP1vKDlSdxkpry0Q/viewform";

const ENTRY = {
  user: "entry.871756020", // "Studio username"
} as const;

export type FeedbackContext = Partial<Record<keyof typeof ENTRY, string | null>>;

export function feedbackFormUrl(ctx: FeedbackContext): string {
  const params = new URLSearchParams({ usp: "pp_url" });
  for (const [key, entry] of Object.entries(ENTRY)) {
    const value = ctx[key as keyof typeof ENTRY];
    // Bound the value so an odd username can't push the URL past what
    // browsers and Google accept.
    if (value) params.set(entry, value.slice(0, 300));
  }
  return `${FORM_URL}?${params.toString()}`;
}
