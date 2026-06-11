"use client";

import { usePanelStore } from "@/lib/stores/panel-store";
import { useWorkspaceStore } from "@/lib/stores/workspace-store";
import { useToolStore } from "@/lib/stores/tool-store";

export function StatusBar() {
  const chatOpen = usePanelStore((s) => s.chatOpen);
  const terminalOpen = usePanelStore((s) => s.terminalOpen);
  const toggleChat = usePanelStore((s) => s.toggleChat);
  const toggleTerminal = usePanelStore((s) => s.toggleTerminal);
  const jupyterStatus = useWorkspaceStore((s) => s.jupyterStatus);
  const tabCount = useToolStore((s) => s.tabs.length);

  return (
    <div className="flex h-6 items-center justify-between border-t border-gray-300 bg-gray-100 px-3 text-xs select-none">
      {/* Left: status indicators */}
      <div className="flex items-center gap-3">
        {/* JupyterLab status */}
        <div className="flex items-center gap-1.5">
          <span
            className={`inline-block h-2 w-2 rounded-full ${
              jupyterStatus === "running"
                ? "bg-green-500"
                : jupyterStatus === "starting"
                  ? "bg-yellow-400 animate-pulse"
                  : "bg-gray-400"
            }`}
          />
          <span className="text-gray-600">
            JupyterLab{" "}
            {jupyterStatus === "running"
              ? "Running"
              : jupyterStatus === "starting"
                ? "Starting..."
                : "Stopped"}
          </span>
        </div>

        {/* Terminal tab count */}
        {tabCount > 0 && (
          <span className="text-gray-500">
            {tabCount} terminal{tabCount !== 1 ? "s" : ""}
          </span>
        )}
      </div>

      {/* Right: panel toggles */}
      <div className="flex items-center gap-1">
        <button
          onClick={toggleTerminal}
          className={`flex items-center gap-1.5 rounded px-2 py-0.5 transition-colors ${
            terminalOpen
              ? "bg-gray-800 text-white"
              : "text-gray-600 hover:bg-gray-200"
          }`}
          title="Toggle terminal panel"
        >
          <svg
            width="12"
            height="12"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <polyline points="4 17 10 11 4 5" />
            <line x1="12" y1="19" x2="20" y2="19" />
          </svg>
          Terminal
        </button>

        <button
          onClick={toggleChat}
          className={`flex items-center gap-1.5 rounded px-2 py-0.5 transition-colors ${
            chatOpen
              ? "bg-pegasus-600 text-white"
              : "text-gray-600 hover:bg-gray-200"
          }`}
          title="Toggle chat panel"
        >
          <svg
            width="12"
            height="12"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
          </svg>
          Chat
        </button>
      </div>
    </div>
  );
}
