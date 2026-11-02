import { redirect } from "next/navigation";
import { requestBasePath } from "@/lib/server/base-path";

/**
 * The app root — where a user lands when they open Studio. Always Home.
 *
 * It used to send people to a /welcome page first, with a preference to skip
 * it (#370). That introduction now lives on the JupyterHub login page
 * (studio-system `jupyterhub/assets/login.html`), where it reaches people
 * *before* they sign in, which is when they need it (2026-11 launch
 * feedback). There is deliberately no login step here: Studio has no auth of
 * its own — JupyterHub and the ingress (studio-system) authenticate upstream
 * and the container runs per user — so a sign-in screen inside the app would
 * only be a dead end.
 *
 * The redirect target must carry the `/user/<name>` prefix. `redirect()` emits
 * an absolute Location header and knows nothing about the prefix the
 * middleware stripped, so a bare `/home` sends the browser out of the pod
 * to the hub, which has no such page (#368).
 */
export default async function RootPage() {
  redirect(`${await requestBasePath()}/home`);
}
