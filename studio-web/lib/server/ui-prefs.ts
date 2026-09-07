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

export async function skipWelcomePreferred(): Promise<boolean> {
  try {
    const res = await fetch(`${API_URL}/api/settings/ui`, {
      cache: "no-store",
      signal: AbortSignal.timeout(1500),
    });
    if (!res.ok) return false;
    const prefs = (await res.json()) as { skip_welcome?: unknown };
    return prefs.skip_welcome === true;
  } catch {
    return false;
  }
}
