import { redirect } from "next/navigation";
import { requestBasePath } from "@/lib/server/base-path";

/**
 * The app root — where a user lands when they open Studio.
 *
 * Sends them to the welcome page. There is deliberately no login step here:
 * Studio has no auth of its own — JupyterHub and the ingress (studio-system)
 * authenticate upstream and the container runs per user — so a sign-in screen
 * inside the app would only be a dead end.
 *
 * The redirect target must carry the `/user/<name>` prefix. `redirect()` emits
 * an absolute Location header and knows nothing about the prefix the
 * middleware stripped, so a bare `/welcome` sends the browser out of the pod
 * to the hub, which has no such page (#368).
 */
export default async function RootPage() {
  const basePath = await requestBasePath();
  redirect(`${basePath}/welcome`);
}
