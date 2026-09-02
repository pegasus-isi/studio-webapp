import { Sidebar } from "@/components/layout/sidebar";
import { TopBar } from "@/components/layout/top-bar";
import { GlobalChatPanel } from "@/components/layout/global-chat-panel";
import { GlobalTerminalPanel } from "@/components/layout/global-terminal-panel";
import { StatusBar } from "@/components/layout/status-bar";
import { ToastContainer } from "@/components/notifications/toast-container";

/**
 * Studio shell — top bar, icon rail, panels, status bar. Applied to the
 * chromed pages. Routes outside this group (e.g. /chat, embedded in
 * JupyterLab) render bare under the root layout.
 *
 * The bar spans the full width above the rail (#314): identity, search and
 * account belong to the app rather than to any page, and moving them up is
 * what lets the left nav be a 56px rail of icons.
 */
export default function StudioLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <>
      <ToastContainer />
      <div className="flex h-screen flex-col overflow-hidden">
        <TopBar />
        <div className="flex flex-1 overflow-hidden">
          <Sidebar />
          <div className="flex flex-1 flex-col overflow-hidden">
            <div className="flex flex-1 overflow-hidden">
              <main className="flex-1 overflow-auto bg-base">{children}</main>
              <GlobalChatPanel />
            </div>
            <GlobalTerminalPanel />
            <StatusBar />
          </div>
        </div>
      </div>
    </>
  );
}
