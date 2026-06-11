"use client";

import { useToolStore, type TerminalTab } from "@/lib/stores/tool-store";
import { TerminalView } from "./terminal-view";
import { cn } from "@/lib/utils";

export function TerminalTabs() {
  const tabs = useToolStore((s) => s.tabs);
  const activeTabId = useToolStore((s) => s.activeTabId);
  const setActiveTab = useToolStore((s) => s.setActiveTab);
  const removeTab = useToolStore((s) => s.removeTab);

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
                removeTab(tab.id);
              }}
              className="ml-1 text-gray-500 hover:text-white"
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
            />
          </div>
        ))}
      </div>
    </div>
  );
}
