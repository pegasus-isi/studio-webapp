import { redirect } from "next/navigation";
import { cookies } from "next/headers";

/**
 * Root route — show the launch/welcome screen, or /login if no session.
 */
export default async function RootPage() {
  const apiUrl = process.env.API_URL ?? "";
  const isRemote = apiUrl !== "" && !apiUrl.includes("localhost") && !apiUrl.includes("127.0.0.1");

  if (isRemote && !process.env.HUB_COOKIE) {
    const cookieStore = await cookies();
    if (!cookieStore.get("hub_session")) {
      redirect("/login");
    }
  }

  redirect("/welcome");
}
