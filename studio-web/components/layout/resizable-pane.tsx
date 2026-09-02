"use client";

/**
 * A pane the user can drag wider or narrower, and collapse entirely.
 *
 * The workflow list was a fixed `w-96`, which is wrong in both directions: too
 * narrow for a long workflow name (they truncate to uselessness) and too wide
 * once you are watching a run and want the space for the run itself.
 *
 * Width persists in localStorage rather than the settings DB. It is genuinely
 * a per-screen preference — a laptop and an external monitor want different
 * numbers — so unlike the interface preferences in `use-ui-prefs`, following
 * the user across devices would be the wrong behaviour here.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { cn } from "@/lib/utils";

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
  const [width, setWidth] = useState(defaultWidth);
  const [collapsed, setCollapsed] = useState(false);
  const [dragging, setDragging] = useState(false);
  const paneRef = useRef<HTMLDivElement>(null);

  // Read persisted state after mount, not during render: touching
  // localStorage during SSR throws, and reading it in a lazy initializer
  // produces a hydration mismatch when server and client disagree.
  useEffect(() => {
    try {
      const stored = localStorage.getItem(`pane:${storageKey}`);
      if (stored) {
        const parsed = JSON.parse(stored);
        if (typeof parsed.width === "number") {
          setWidth(Math.min(maxWidth, Math.max(minWidth, parsed.width)));
        }
        if (typeof parsed.collapsed === "boolean") setCollapsed(parsed.collapsed);
      }
    } catch {
      /* unreadable or unparseable — the defaults are fine */
    }
  }, [storageKey, minWidth, maxWidth]);

  const persist = useCallback(
    (next: { width: number; collapsed: boolean }) => {
      try {
        localStorage.setItem(`pane:${storageKey}`, JSON.stringify(next));
      } catch {
        /* private mode / quota — resizing still works for this session */
      }
    },
    [storageKey]
  );

  // Drag handling lives on the document, not the handle: a fast drag outruns
  // the pointer and the handle stops receiving events, which would leave the
  // pane stuck mid-resize.
  useEffect(() => {
    if (!dragging) return;

    const onMove = (e: MouseEvent) => {
      const box = paneRef.current?.getBoundingClientRect();
      const raw =
        side === "right"
          ? (box?.right ?? 0) - e.clientX
          : e.clientX - (box?.left ?? 0);
      setWidth(Math.min(maxWidth, Math.max(minWidth, raw)));
    };
    const onUp = () => {
      setDragging(false);
      setWidth((w) => {
        persist({ width: w, collapsed: false });
        return w;
      });
    };

    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
    // Stop the drag selecting text across the page while it is in progress.
    const previousSelect = document.body.style.userSelect;
    document.body.style.userSelect = "none";
    document.body.style.cursor = "col-resize";
    return () => {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      document.body.style.userSelect = previousSelect;
      document.body.style.cursor = "";
    };
  }, [dragging, minWidth, maxWidth, persist, side]);

  const toggleCollapsed = () => {
    const next = !collapsed;
    setCollapsed(next);
    persist({ width, collapsed: next });
  };

  // Keyboard resizing, so the pane isn't mouse-only.
  const onHandleKeyDown = (e: React.KeyboardEvent) => {
    const step = e.shiftKey ? 64 : 16;
    if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
      e.preventDefault();
      const grow = side === "right" ? e.key === "ArrowLeft" : e.key === "ArrowRight";
      const delta = grow ? step : -step;
      const next = Math.min(maxWidth, Math.max(minWidth, width + delta));
      setWidth(next);
      persist({ width: next, collapsed });
    } else if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      toggleCollapsed();
    }
  };

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
          setDragging(true);
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
