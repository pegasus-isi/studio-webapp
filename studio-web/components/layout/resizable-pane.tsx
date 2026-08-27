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
  children: React.ReactNode;
}

export function ResizablePane({
  storageKey,
  defaultWidth = 384, // the previous fixed w-96, so nothing moves on first load
  minWidth = 240,
  maxWidth = 720,
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
      const left = paneRef.current?.getBoundingClientRect().left ?? 0;
      const next = Math.min(maxWidth, Math.max(minWidth, e.clientX - left));
      setWidth(next);
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
  }, [dragging, minWidth, maxWidth, persist]);

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
      const delta = e.key === "ArrowLeft" ? -step : step;
      const next = Math.min(maxWidth, Math.max(minWidth, width + delta));
      setWidth(next);
      persist({ width: next, collapsed });
    } else if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      toggleCollapsed();
    }
  };

  if (collapsed) {
    return (
      <div className="flex shrink-0 items-start border-r border-line bg-surface">
        <button
          onClick={toggleCollapsed}
          title="Show the workflow list"
          aria-label="Show the workflow list"
          aria-expanded={false}
          className="px-2 py-3 text-fgsubtle hover:text-fg"
        >
          ›
        </button>
      </div>
    );
  }

  return (
    <div
      ref={paneRef}
      style={{ width }}
      className="relative flex shrink-0 flex-col border-r border-line bg-surface"
    >
      <div className="min-h-0 flex-1 overflow-y-auto">{children}</div>

      {/* Collapse control sits at the bottom, out of the way of the list. */}
      <button
        onClick={toggleCollapsed}
        title="Hide the workflow list"
        aria-label="Hide the workflow list"
        aria-expanded
        className="border-t border-line px-3 py-1.5 text-left text-xs text-fgsubtle hover:text-fg"
      >
        ‹ Hide
      </button>

      {/* Drag handle. Wider than it looks (-right-1 .. w-2) because a 1px
          target is a fine line to hit and this is not a precision task. */}
      <div
        role="separator"
        aria-orientation="vertical"
        aria-label="Resize the workflow list"
        tabIndex={0}
        onMouseDown={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onKeyDown={onHandleKeyDown}
        className={cn(
          "absolute -right-1 top-0 z-10 h-full w-2 cursor-col-resize",
          "hover:bg-pegasus-400/40 focus:bg-pegasus-400/60 focus:outline-none",
          dragging && "bg-pegasus-400/60"
        )}
      />
    </div>
  );
}
