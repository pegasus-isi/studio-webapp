"use client";

import { useState, useRef, useEffect } from "react";
import {
  usePanelStore,
  TERMINAL_HEIGHT_DEFAULT,
  TERMINAL_HEIGHT_MIN,
} from "@/lib/stores/panel-store";
import { useToolStore } from "@/lib/stores/tool-store";
import { useTools, useStartTool } from "@/lib/hooks/use-tools";
import { useActionToast } from "@/lib/hooks/use-action-toast";
import { TerminalTabs } from "@/components/terminal/terminal-tabs";
import * as api from "@/lib/api/client";

export function GlobalTerminalPanel() {
  const terminalOpen = usePanelStore((s) => s.terminalOpen);
  const setTerminalOpen = usePanelStore((s) => s.setTerminalOpen);
  const terminalHeight = usePanelStore((s) => s.terminalHeight);
  const setTerminalHeight = usePanelStore((s) => s.setTerminalHeight);

  // Drag-to-resize state. The height itself lives in the (persisted) panel
  // store; xterm reflow needs no wiring here — TerminalView's ResizeObserver
  // refits and sends the PTY resize whenever the container changes, so a
  // remote TUI gets its SIGWINCH live while dragging.
  const dragState = useRef<{ startY: number; startHeight: number } | null>(
    null
  );
  const dragRaf = useRef<number | null>(null);
  const preMaxHeight = useRef<number | null>(null);

  // Keep ~120px of window for the main content + status bar; mirrors the
  // CSS maxHeight clamp on the wrapper.
  const maxPanelHeight = () => window.innerHeight - 120;

  const onHandlePointerDown = (e: React.PointerEvent) => {
    dragState.current = { startY: e.clientY, startHeight: terminalHeight };
    (e.target as HTMLElement).setPointerCapture(e.pointerId);
  };
  const onHandlePointerMove = (e: React.PointerEvent) => {
    const drag = dragState.current;
    if (!drag) return;
    const next = Math.min(
      maxPanelHeight(),
      Math.max(
        TERMINAL_HEIGHT_MIN,
        drag.startHeight + (drag.startY - e.clientY)
      )
    );
    // One store write per frame: every write reflows xterm and resizes the
    // PTY over the websocket, so unthrottled mousemove would spam both.
    if (dragRaf.current !== null) cancelAnimationFrame(dragRaf.current);
    dragRaf.current = requestAnimationFrame(() => setTerminalHeight(next));
  };
  const onHandlePointerUp = (e: React.PointerEvent) => {
    dragState.current = null;
    (e.target as HTMLElement).releasePointerCapture(e.pointerId);
  };

  const toggleMaximize = () => {
    const max = maxPanelHeight();
    if (terminalHeight >= max - 10 && preMaxHeight.current !== null) {
      setTerminalHeight(preMaxHeight.current);
      preMaxHeight.current = null;
    } else {
      preMaxHeight.current = terminalHeight;
      setTerminalHeight(max);
    }
  };
  const tabs = useToolStore((s) => s.tabs);
  const addTab = useToolStore((s) => s.addTab);

  const { data } = useTools();
  const startTool = useStartTool();
  const runAction = useActionToast();
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  // The remote-shell item appears only when a remote compute backend is
  // active; a plain local setup keeps the dropdown exactly as it was.
  const [remoteBackend, setRemoteBackend] =
    useState<api.ExecutionBackendInfo | null>(null);
  useEffect(() => {
    api
      .getExecutionBackend()
      .then((s) => {
        const active = s.backends.find((b) => b.id === s.backend);
        setRemoteBackend(active && active.id !== "local" ? active : null);
      })
      .catch(() => setRemoteBackend(null));
  }, []);

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
    const result = await runAction(`Launch ${toolName}`, () =>
      startTool.mutateAsync(toolId)
    );
    if (result) addTab(toolId, toolName);
  };

  if (!terminalOpen) return null;

  return (
    <div
      className="relative flex shrink-0 flex-col border-t border-line"
      style={{
        height: terminalHeight,
        minHeight: TERMINAL_HEIGHT_MIN,
        maxHeight: "calc(100dvh - 120px)",
      }}
    >
      {/* Drag handle: straddles the top border (absolute, so it adds no
          layout height). Double-click restores the default height. */}
      <div
        role="separator"
        aria-orientation="horizontal"
        aria-label="Resize terminal panel"
        onPointerDown={onHandlePointerDown}
        onPointerMove={onHandlePointerMove}
        onPointerUp={onHandlePointerUp}
        onDoubleClick={() => setTerminalHeight(TERMINAL_HEIGHT_DEFAULT)}
        className="absolute -top-1 left-0 z-30 h-2 w-full cursor-row-resize touch-none hover:bg-pegasus-500/40"
      />

      {/* Toolbar */}
      <div className="flex items-center justify-between bg-gray-800 px-3 py-1">
        <div className="flex items-center gap-2">
          <span className="text-xs font-medium text-gray-300">Terminal</span>

          {/* Open Terminal dropdown */}
          <div className="relative" ref={dropdownRef}>
            <button
              onClick={() => setDropdownOpen(!dropdownOpen)}
              className="flex items-center gap-1 rounded px-2 py-0.5 text-xs text-fgsubtle hover:bg-gray-700 hover:text-white"
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

                {remoteBackend && (
                  <button
                    onClick={() => {
                      setDropdownOpen(false);
                      addTab(null, `${remoteBackend.label} (remote)`, true);
                    }}
                    className="block w-full px-4 py-2 text-left text-sm text-gray-300 hover:bg-gray-700"
                  >
                    Remote shell — {remoteBackend.label}
                  </button>
                )}

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

        <span className="flex items-center gap-3">
        <button
          onClick={toggleMaximize}
          className="text-fgsubtle hover:text-white"
          aria-label="Maximize or restore terminal panel"
          title="Maximize / restore (drag the top edge to resize)"
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
            <polyline points="7 9 12 4 17 9" />
            <polyline points="7 15 12 20 17 15" />
          </svg>
        </button>
        <button
          onClick={() => setTerminalOpen(false)}
          className="text-fgsubtle hover:text-white"
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
        </span>
      </div>

      {/* Terminal content */}
      <div className="flex-1 bg-gray-900">
        {tabs.length > 0 ? (
          <TerminalTabs />
        ) : (
          <div className="flex h-full items-center justify-center text-fgmuted">
            <p className="text-sm">
              Click &quot;+ New&quot; to open a bash session or AI tool
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
