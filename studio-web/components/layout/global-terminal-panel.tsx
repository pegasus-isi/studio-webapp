"use client";

import { useState, useRef, useEffect, useLayoutEffect } from "react";
import { usePanelStore } from "@/lib/stores/panel-store";
import { useToolStore } from "@/lib/stores/tool-store";
import { useTools, useStartTool } from "@/lib/hooks/use-tools";
import { useActionToast } from "@/lib/hooks/use-action-toast";
import { usePaneSize } from "@/lib/hooks/use-pane-size";
import { cn } from "@/lib/utils";
import { TerminalTabs } from "@/components/terminal/terminal-tabs";

/** The height the panel opens at and that the reset control returns to. */
const DEFAULT_HEIGHT = 300;

export function GlobalTerminalPanel() {
  const terminalOpen = usePanelStore((s) => s.terminalOpen);
  const setTerminalOpen = usePanelStore((s) => s.setTerminalOpen);
  const tabs = useToolStore((s) => s.tabs);
  const addTab = useToolStore((s) => s.addTab);

  const { data } = useTools();
  const startTool = useStartTool();
  const runAction = useActionToast();
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  // The panel used to be a fixed 300px, which is wrong in both directions: too
  // short for a build log or an htop, too tall once you only want a prompt
  // while reading a workflow above it. Same hook as the workflow list and the
  // graph pane, so the height persists per browser like theirs do. The
  // terminal view refits xterm and tells the PTY on every container resize,
  // so the shell sees the new rows without anything further here.
  // `invert` because the panel hangs from the bottom of the viewport: the
  // handle is on its top edge and dragging *up* makes it taller.
  // The ceiling follows the window rather than being a number: a fixed max
  // either stops short of full height on a big monitor or lets the panel
  // push the status bar off a small one. The limit is the column the panel
  // sits in minus the status bar below it, measured from the DOM so a
  // change to either bar's height cannot silently break it; before the
  // panel has mounted, fall back to the window less the bars' known sizes.
  const [maxHeight, setMaxHeight] = useState(() =>
    typeof window === "undefined" ? 900 : Math.max(120, window.innerHeight - 72)
  );
  const { size: height, dragging, startDrag, reset, onHandleKeyDown, paneRef } =
    usePaneSize({
      storageKey: "terminal-pane",
      defaultSize: DEFAULT_HEIGHT,
      minSize: 120,
      maxSize: maxHeight,
      axis: "y",
      invert: true,
    });
  useLayoutEffect(() => {
    if (!terminalOpen) return;
    const measure = () => {
      const pane = paneRef.current;
      const column = pane?.parentElement;
      const statusBar = pane?.nextElementSibling as HTMLElement | null;
      const available = column
        ? column.clientHeight - (statusBar?.offsetHeight ?? 0)
        : window.innerHeight - 72;
      setMaxHeight(Math.max(120, available));
    };
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, [terminalOpen, paneRef]);

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
      ref={paneRef}
      className="relative flex shrink-0 flex-col border-t border-line"
      style={{ height }}
    >
      {/* Drag handle along the top edge. Taller than the 1px border it sits
          on (-top-1 .. h-2) because a hairline is a poor drag target; it
          overlaps the bottom of the main area by a pixel, which nothing
          there needs. */}
      <div
        role="separator"
        aria-orientation="horizontal"
        aria-label="Resize the terminal panel"
        tabIndex={0}
        onMouseDown={(e) => {
          e.preventDefault();
          startDrag();
        }}
        onDoubleClick={reset}
        onKeyDown={onHandleKeyDown}
        className={cn(
          "absolute -top-1 left-0 z-10 h-2 w-full cursor-row-resize",
          "hover:bg-pegasus-400/40 focus:bg-pegasus-400/60 focus:outline-none",
          dragging && "bg-pegasus-400/60"
        )}
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

        <div className="flex items-center gap-2">
          {/* Reset to the default height. Sits beside the close control
              because that is where the eye goes for "undo what I did to
              this panel"; disabled rather than hidden at the default so it
              does not pop in and out as the handle crosses 300px. */}
          <button
            onClick={reset}
            disabled={height === DEFAULT_HEIGHT}
            className="text-fgsubtle hover:text-white disabled:cursor-default disabled:opacity-30 disabled:hover:text-fgsubtle"
            aria-label="Reset terminal panel height"
            title="Reset height"
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
              aria-hidden="true"
            >
              <path d="M3 12a9 9 0 1 0 3-6.7L3 8" />
              <polyline points="3 3 3 8 8 8" />
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
        </div>
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
