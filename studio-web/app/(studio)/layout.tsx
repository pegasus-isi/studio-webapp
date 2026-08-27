import { Sidebar } from "@/components/layout/sidebar";
import { GlobalChatPanel } from "@/components/layout/global-chat-panel";
import { GlobalTerminalPanel } from "@/components/layout/global-terminal-panel";
import { StatusBar } from "@/components/layout/status-bar";
import { ToastContainer } from "@/components/notifications/toast-container";

/**
 * Studio shell — sidebar, panels, status bar, theme toggle. Applied to the
 * chromed pages. Routes outside this group (e.g. /chat, embedded in
 * JupyterLab) render bare under the root layout.
 */
export default function StudioLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <>
      <ToastContainer />
      {/* h-dvh (not h-screen/100vh): the dynamic viewport height matches what
          is actually visible, so the terminal panel and status bar can never
          render below the fold on short windows — with overflow-hidden here
          there is no scrollbar to reach clipped content. */}
      <div className="flex h-dvh overflow-hidden">
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
    </>
  );
}
