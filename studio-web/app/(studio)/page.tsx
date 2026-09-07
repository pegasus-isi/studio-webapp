import { redirect } from "next/navigation";

/**
 * The app root — where a user lands when they open Studio.
 *
 * Sends them to the welcome page. There is deliberately no login step here:
 * Studio has no auth of its own — JupyterHub and the ingress (studio-system)
 * authenticate upstream and the container runs per user — so a sign-in screen
 * inside the app would only be a dead end.
 */
export default function RootPage() {
  redirect("/welcome");
}
