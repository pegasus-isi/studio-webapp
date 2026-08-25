"use client";

import { useEffect, useState } from "react";
import { useToolStore, type TerminalTab } from "@/lib/stores/tool-store";
import { TerminalView } from "./terminal-view";
import { apiUrl } from "@/lib/urls";
import { cn } from "@/lib/utils";

export function TerminalTabs() {
  const tabs = useToolStore((s) => s.tabs);
  const activeTabId = useToolStore((s) => s.activeTabId);
  const setActiveTab = useToolStore((s) => s.setActiveTab);
  const removeTab = useToolStore((s) => s.removeTab);

  // Tabs are restored from localStorage (persisted store); render only after
  // mount so the SSR markup (empty store) doesn't mismatch on hydration.
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  const closeTab = (tab: TerminalTab) => {
    // Closing the tab is the explicit "kill my shell" action — the backend
    // session would otherwise linger until the idle pruner reaps it.
    if (tab.sessionId) {
      fetch(apiUrl(`terminals/${tab.sessionId}`), { method: "DELETE" }).catch(
        () => {}
      );
    }
    removeTab(tab.id);
  };

  if (!mounted) return null;

  return (
    <div className="flex h-full flex-col">
      {/* Tab bar */}
      <div className="flex items-center gap-1 bg-gray-800 px-2 pt-1">
        {tabs.map((tab) => (
          <div
            key={tab.id}
            className={cn(
              "group flex items-center gap-2 rounded-t-md px-3 py-1.5 text-xs transition-colors",
              tab.id === activeTabId
                ? "bg-gray-900 text-white"
                : "bg-gray-700 text-gray-300 hover:bg-gray-600"
            )}
          >
            <button
              onClick={() => setActiveTab(tab.id)}
              className="max-w-[120px] truncate"
            >
              {tab.label}
            </button>
            <button
              onClick={(e) => {
                e.stopPropagation();
                closeTab(tab);
              }}
              className="ml-1 text-fgmuted hover:text-white"
            >
              ×
            </button>
          </div>
        ))}
      </div>

      {/* Terminal content */}
      <div className="relative flex-1">
        {tabs.map((tab) => (
          <div
            key={tab.id}
            className={cn(
              "absolute inset-0",
              tab.id === activeTabId ? "z-10" : "z-0 hidden"
            )}
          >
            <TerminalView
              tabId={tab.id}
              toolId={tab.toolId}
              remote={tab.remote}
            />
          </div>
        ))}
      </div>
    </div>
  );
}
