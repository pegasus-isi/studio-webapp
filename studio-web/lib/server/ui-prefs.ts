/**
 * Server-side read of the per-user UI preferences (`GET /api/settings/ui`).
 *
 * Server components run inside the same container as studio-api, so they can
 * ask it directly on its own port — the same address `next.config.ts` proxies
 * dev API calls to. Only the app-root redirect needs this: whether to show the
 * welcome page is decided before any client code runs, and deciding it in the
 * browser would mean rendering the welcome page and then leaving it (#370).
 *
 * Failure means "show the welcome page": a slow or absent API must not turn
 * the front door into an error, and the welcome page has its own way onward.
 */

const API_URL = process.env.API_URL || "http://localhost:8080";

/**
 * On a gated session (#391) studio-api refuses anything that did not arrive
 * through the front proxy. This fetch deliberately does not — it is a sibling
 * process talking to loopback — so it presents the secret itself. Absent on the
 * hosted path, where the header is meaningless and the API ignores it.
 *
 * Getting this wrong is invisible: the catch below returns false, which means
 * "show the welcome page", so the symptom would be every user pinned to the
 * welcome screen with nothing logged.
 */
function internalHeaders(): Record<string, string> {
  const secret = process.env.STUDIO_INTERNAL_SECRET;
  return secret ? { "X-Studio-Internal": secret } : {};
}

export async function skipWelcomePreferred(): Promise<boolean> {
  try {
    const res = await fetch(`${API_URL}/api/settings/ui`, {
      cache: "no-store",
      headers: internalHeaders(),
      signal: AbortSignal.timeout(1500),
    });
    if (!res.ok) return false;
    const prefs = (await res.json()) as { skip_welcome?: unknown };
    return prefs.skip_welcome === true;
  } catch {
    return false;
  }
}
