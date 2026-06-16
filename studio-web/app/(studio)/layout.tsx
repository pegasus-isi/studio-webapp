import { Sidebar } from "@/components/layout/sidebar";
import { GlobalChatPanel } from "@/components/layout/global-chat-panel";
import { GlobalTerminalPanel } from "@/components/layout/global-terminal-panel";
import { StatusBar } from "@/components/layout/status-bar";
import { UserMenu } from "@/components/layout/user-menu";

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
      <UserMenu />
      <div className="flex h-screen overflow-hidden">
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
