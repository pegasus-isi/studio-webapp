import { redirect } from "next/navigation";
import { requestBasePath } from "@/lib/server/base-path";

/**
 * The old welcome page's address, kept so bookmarks and links to it land on
 * Home instead of a 404. Its content moved to the JupyterHub login page
 * (see app/(studio)/page.tsx).
 */
export default async function WelcomeRedirect() {
  redirect(`${await requestBasePath()}/home`);
}
