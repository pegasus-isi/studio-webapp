"use client";

/**
 * A pane the user can drag wider or narrower, and collapse entirely.
 *
 * The workflow list was a fixed `w-96`, which is wrong in both directions: too
 * narrow for a long workflow name (they truncate to uselessness) and too wide
 * once you are watching a run and want the space for the run itself.
 *
 * Sizing and persistence live in `lib/hooks/use-pane-size.ts`, shared with the
 * graph pane, which needed the same behaviour on the vertical axis. See there
 * for why width is a localStorage preference rather than a settings-DB one.
 */

import { cn } from "@/lib/utils";
import { usePaneSize } from "@/lib/hooks/use-pane-size";

interface Props {
  /** Stable key for persisting this pane's width. */
  storageKey: string;
  defaultWidth?: number;
  minWidth?: number;
  maxWidth?: number;
  /**
   * Which edge the pane is anchored to. A left pane's drag handle is on its
   * right and widens as the pointer moves right; a right pane is the mirror
   * of that, and getting the sign wrong makes it shrink when you pull it
   * wider.
   */
  side?: "left" | "right";
  /** Panes with their own close control don't need a second way to hide. */
  collapsible?: boolean;
  /**
   * Whether the pane scrolls its children. False for anything that manages
   * its own scrolling internally — the chat has a scrolling transcript and a
   * pinned composer, and an outer scroller would fight both.
   */
  scroll?: boolean;
  label?: string;
  children: React.ReactNode;
}

export function ResizablePane({
  storageKey,
  defaultWidth = 384, // the previous fixed w-96, so nothing moves on first load
  minWidth = 240,
  maxWidth = 720,
  side = "left",
  collapsible = true,
  scroll = true,
  label = "the list",
  children,
}: Props) {
  // Drag/persist machinery is shared with the graph pane, which needed the
  // same behaviour on the vertical axis (lib/hooks/use-pane-size.ts). The
  // hook also reads the legacy `width` key, so saved widths survive.
  const {
    size: width,
    collapsed,
    toggleCollapsed,
    dragging,
    startDrag,
    onHandleKeyDown,
    paneRef,
  } = usePaneSize({
    storageKey,
    defaultSize: defaultWidth,
    minSize: minWidth,
    maxSize: maxWidth,
    axis: "x",
    invert: side === "right",
  });

  if (collapsed && collapsible) {
    return (
      <div
        className={cn(
          "flex shrink-0 items-start bg-surface",
          side === "right" ? "border-l border-line" : "border-r border-line"
        )}
      >
        <button
          onClick={toggleCollapsed}
          title={`Show ${label}`}
          aria-label={`Show ${label}`}
          aria-expanded={false}
          className="px-2 py-3 text-fgsubtle hover:text-fg"
        >
          <svg
            width="15"
            height="15"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
          >
            <polyline
              points={side === "right" ? "15 18 9 12 15 6" : "9 18 15 12 9 6"}
            />
          </svg>
        </button>
      </div>
    );
  }

  return (
    <div
      ref={paneRef}
      style={{ width }}
      className={cn(
        "relative flex shrink-0 flex-col bg-surface",
        side === "right" ? "border-l border-line" : "border-r border-line"
      )}
    >
      {/* Collapse control, top-right (#314).
          It used to sit at the bottom of the pane, below the list — past a
          scroll on any real workspace, which is the same as not being there.
          Once you have picked a workflow the list is the least useful thing on
          screen, so getting rid of it has to be the easiest thing to find. */}
      {collapsible && (
        <button
          onClick={toggleCollapsed}
          title={`Hide ${label}`}
          aria-label={`Hide ${label}`}
          aria-expanded
          className="absolute right-1.5 top-1.5 z-10 rounded-md p-1 text-fgsubtle transition-colors hover:bg-muted hover:text-fg"
        >
          <svg
            width="15"
            height="15"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
          >
            <polyline points="15 18 9 12 15 6" />
          </svg>
        </button>
      )}

      <div
        className={cn(
          "min-h-0 flex-1",
          scroll ? "overflow-y-auto" : "overflow-hidden"
        )}
      >
        {children}
      </div>

      {/* Drag handle. Wider than it looks (-right-1 .. w-2) because a 1px
          target is a fine line to hit and this is not a precision task. */}
      <div
        role="separator"
        aria-orientation="vertical"
        aria-label={`Resize ${label}`}
        tabIndex={0}
        onMouseDown={(e) => {
          e.preventDefault();
          startDrag();
        }}
        onKeyDown={onHandleKeyDown}
        className={cn(
          "absolute top-0 z-10 h-full w-2 cursor-col-resize",
          side === "right" ? "-left-1" : "-right-1",
          "hover:bg-pegasus-400/40 focus:bg-pegasus-400/60 focus:outline-none",
          dragging && "bg-pegasus-400/60"
        )}
      />
    </div>
  );
}
