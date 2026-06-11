"use client";

import { useState, useRef, useEffect } from "react";
import { usePanelStore } from "@/lib/stores/panel-store";
import { useToolStore } from "@/lib/stores/tool-store";
import { useTools, useStartTool } from "@/lib/hooks/use-tools";
import { TerminalTabs } from "@/components/terminal/terminal-tabs";

export function GlobalTerminalPanel() {
  const terminalOpen = usePanelStore((s) => s.terminalOpen);
  const setTerminalOpen = usePanelStore((s) => s.setTerminalOpen);
  const tabs = useToolStore((s) => s.tabs);
  const addTab = useToolStore((s) => s.addTab);

  const { data } = useTools();
  const startTool = useStartTool();
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  // Close dropdown on outside click
  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (
        dropdownRef.current &&
        !dropdownRef.current.contains(e.target as Node)
      ) {
        setDropdownOpen(false);
      }
    }
    if (dropdownOpen) {
      document.addEventListener("mousedown", handleClick);
      return () => document.removeEventListener("mousedown", handleClick);
    }
  }, [dropdownOpen]);

  const terminalTools = (data?.tools ?? []).filter(
    (t) => t.installed && t.info.type === "terminal"
  );

  const handleLaunchTool = async (toolId: string, toolName: string) => {
    setDropdownOpen(false);
    await startTool.mutateAsync(toolId);
    addTab(toolId, toolName);
  };

  if (!terminalOpen) return null;

  return (
    <div
      className="flex flex-col border-t border-gray-300"
      style={{ height: "300px" }}
    >
      {/* Toolbar */}
      <div className="flex items-center justify-between bg-gray-800 px-3 py-1">
        <div className="flex items-center gap-2">
          <span className="text-xs font-medium text-gray-300">Terminal</span>

          {/* Open Terminal dropdown */}
          <div className="relative" ref={dropdownRef}>
            <button
              onClick={() => setDropdownOpen(!dropdownOpen)}
              className="flex items-center gap-1 rounded px-2 py-0.5 text-xs text-gray-400 hover:bg-gray-700 hover:text-white"
            >
              + New
              <svg
                className="h-3 w-3"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M19 9l-7 7-7-7"
                />
              </svg>
            </button>

            {dropdownOpen && (
              <div className="absolute bottom-full left-0 z-20 mb-1 w-48 rounded-md border border-gray-600 bg-gray-800 py-1 shadow-lg">
                <button
                  onClick={() => {
                    setDropdownOpen(false);
                    addTab(null, "Terminal");
                  }}
                  className="block w-full px-4 py-2 text-left text-sm text-gray-300 hover:bg-gray-700"
                >
                  Bash
                </button>

                {terminalTools.length > 0 && (
                  <div className="my-1 border-t border-gray-700" />
                )}

                {terminalTools.map((tool) => (
                  <button
                    key={tool.info.id}
                    onClick={() =>
                      handleLaunchTool(tool.info.id, tool.info.name)
                    }
                    disabled={startTool.isPending}
                    className="block w-full px-4 py-2 text-left text-sm text-gray-300 hover:bg-gray-700 disabled:opacity-50"
                  >
                    {tool.info.name}
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>

        <button
          onClick={() => setTerminalOpen(false)}
          className="text-gray-400 hover:text-white"
          aria-label="Close terminal panel"
        >
          <svg
            xmlns="http://www.w3.org/2000/svg"
            width="14"
            height="14"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <line x1="18" y1="6" x2="6" y2="18" />
            <line x1="6" y1="6" x2="18" y2="18" />
          </svg>
        </button>
      </div>

      {/* Terminal content */}
      <div className="flex-1 bg-gray-900">
        {tabs.length > 0 ? (
          <TerminalTabs />
        ) : (
          <div className="flex h-full items-center justify-center text-gray-500">
            <p className="text-sm">
              Click &quot;+ New&quot; to open a bash session or AI tool
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
