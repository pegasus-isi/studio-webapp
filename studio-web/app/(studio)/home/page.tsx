import { DashboardView } from "@/components/dashboard/dashboard-view";

/**
 * Home, as a real route — the destination of the Home nav link (#292).
 *
 * Studio runs under a dynamic `/user/<name>` prefix that middleware strips by
 * rewriting. For every other route that trims a prefix off a deeper path
 * (`/user/<name>/workflows` → `/workflows`) and client-side navigation works.
 * The app root was the sole exception: it collapsed to `/`, changing the route
 * *depth* rather than trimming, and navigating to it left the URL committed
 * with nothing rendered.
 *
 * Rather than keep guessing at that — two theories had already failed, and it
 * cannot be reproduced without a live prefix — this route removes the
 * exception. `/home` rewrites to `/home`, exactly like every route that
 * demonstrably works, so Home stops being a special case.
 *
 * The cost is that the dashboard has two URLs. `/` remains valid and is what a
 * user lands on; clicking Home moves them to `/home`. Both render the same
 * view, and the nav treats either as active.
 */
export default function HomePage() {
  return <DashboardView />;
}
