import { DashboardView } from "@/components/dashboard/dashboard-view";

/**
 * The app root — where a user lands when they open Studio.
 *
 * The Home *nav link* points at `/home` instead, which renders the same view;
 * see that route for why. This one stays so the bare workspace URL works.
 */
export default function RootPage() {
  return <DashboardView />;
}
