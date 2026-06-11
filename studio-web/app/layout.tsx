import type { Metadata } from "next";
import { headers } from "next/headers";
import "./globals.css";
import { Providers } from "./providers";
import { Sidebar } from "@/components/layout/sidebar";
import { GlobalChatPanel } from "@/components/layout/global-chat-panel";
import { GlobalTerminalPanel } from "@/components/layout/global-terminal-panel";
import { StatusBar } from "@/components/layout/status-bar";
import { normalizeBasePath } from "@/lib/urls";

export const metadata: Metadata = {
  title: "PegasusAI Studio",
  description: "AI-powered scientific workflow development platform",
};

export default async function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const requestHeaders = await headers();
  const basePath = normalizeBasePath(
    requestHeaders.get("x-forwarded-prefix") ||
      requestHeaders.get("x-script-name") ||
      process.env.NEXT_PUBLIC_BASE_PATH
  );
  const baseHref = `${basePath || ""}/`;

  return (
    <html lang="en">
      <head>
        <base href={baseHref} />
        <meta name="studio-base-path" content={basePath} />
      </head>
      <body className="antialiased">
        <Providers>
          <div className="flex h-screen overflow-hidden">
            <Sidebar />
            <div className="flex flex-1 flex-col overflow-hidden">
              {/* Main content + optional chat sidebar */}
              <div className="flex flex-1 overflow-hidden">
                <main className="flex-1 overflow-auto bg-gray-50">
                  {children}
                </main>
                <GlobalChatPanel />
              </div>
              {/* Global terminal bottom pane */}
              <GlobalTerminalPanel />
              {/* VS Code-style status bar */}
              <StatusBar />
            </div>
          </div>
        </Providers>
      </body>
    </html>
  );
}
