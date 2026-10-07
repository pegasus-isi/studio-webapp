"use client";

/**
 * Drag-to-resize plus persistence for a pane, along either axis.
 *
 * Extracted from `components/layout/resizable-pane.tsx` when the graph pane
 * needed the same behaviour vertically. The maths is the only thing that
 * differs between the cases — which edge the handle is on and therefore which
 * coordinate grows the pane — so it is a parameter rather than a second
 * implementation.
 *
 * Size persists in localStorage rather than the settings DB. It is genuinely a
 * per-screen preference — a laptop and an external monitor want different
 * numbers — so unlike the interface preferences in `use-ui-prefs`, following
 * the user across devices would be the wrong behaviour here.
 */

import { useCallback, useEffect, useRef, useState } from "react";

interface Options {
  /** Stable key for persisting this pane's size. */
  storageKey: string;
  defaultSize: number;
  minSize: number;
  maxSize: number;
  /** "x" resizes width, "y" resizes height. */
  axis?: "x" | "y";
  /**
   * True when the handle is on the leading edge (a right-anchored pane's left
   * side), so the pane grows as the pointer moves *toward* the origin. Getting
   * this wrong makes a pane shrink when you pull it wider.
   */
  invert?: boolean;
}

export function usePaneSize({
  storageKey,
  defaultSize,
  minSize,
  maxSize,
  axis = "x",
  invert = false,
}: Options) {
  const [size, setSize] = useState(defaultSize);
  const [collapsed, setCollapsed] = useState(false);
  const [dragging, setDragging] = useState(false);
  const paneRef = useRef<HTMLDivElement>(null);

  // Read persisted state after mount, not during render: touching
  // localStorage during SSR throws, and reading it in a lazy initializer
  // produces a hydration mismatch when server and client disagree.
  useEffect(() => {
    try {
      const stored = localStorage.getItem(`pane:${storageKey}`);
      if (!stored) return;
      const parsed = JSON.parse(stored);
      // `width` is the legacy key, written before this hook existed; read it
      // so nobody's saved workflow-list width resets on upgrade.
      const saved =
        typeof parsed.size === "number"
          ? parsed.size
          : typeof parsed.width === "number"
            ? parsed.width
            : null;
      if (saved !== null) setSize(Math.min(maxSize, Math.max(minSize, saved)));
      if (typeof parsed.collapsed === "boolean") setCollapsed(parsed.collapsed);
    } catch {
      /* unreadable or unparseable — the defaults are fine */
    }
  }, [storageKey, minSize, maxSize]);

  const persist = useCallback(
    (next: { size: number; collapsed: boolean }) => {
      try {
        localStorage.setItem(
          `pane:${storageKey}`,
          // Keep writing `width` too: a downgrade should not lose the value.
          JSON.stringify({ ...next, width: next.size })
        );
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
      let raw: number;
      if (axis === "y") {
        raw = invert
          ? (box?.bottom ?? 0) - e.clientY
          : e.clientY - (box?.top ?? 0);
      } else {
        raw = invert
          ? (box?.right ?? 0) - e.clientX
          : e.clientX - (box?.left ?? 0);
      }
      setSize(Math.min(maxSize, Math.max(minSize, raw)));
    };
    const onUp = () => {
      setDragging(false);
      setSize((s) => {
        persist({ size: s, collapsed: false });
        return s;
      });
    };

    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
    // Stop the drag selecting text across the page while it is in progress.
    const previousSelect = document.body.style.userSelect;
    document.body.style.userSelect = "none";
    document.body.style.cursor = axis === "y" ? "row-resize" : "col-resize";
    return () => {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      document.body.style.userSelect = previousSelect;
      document.body.style.cursor = "";
    };
  }, [dragging, minSize, maxSize, persist, axis, invert]);

  const toggleCollapsed = useCallback(() => {
    setCollapsed((c) => {
      const next = !c;
      persist({ size, collapsed: next });
      return next;
    });
  }, [persist, size]);

  /**
   * Back to the default size. A pane dragged somewhere silly (or clamped by
   * a smaller window) needs a one-click way home that is not "find 300px by
   * eye".
   */
  const reset = useCallback(() => {
    setSize(defaultSize);
    setCollapsed(false);
    persist({ size: defaultSize, collapsed: false });
  }, [defaultSize, persist]);

  /** Keyboard resizing, so the pane isn't mouse-only. */
  const onHandleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      const step = e.shiftKey ? 64 : 16;
      const [less, more] =
        axis === "y" ? ["ArrowUp", "ArrowDown"] : ["ArrowLeft", "ArrowRight"];
      if (e.key === less || e.key === more) {
        e.preventDefault();
        const grow = invert ? e.key === less : e.key === more;
        const next = Math.min(
          maxSize,
          Math.max(minSize, size + (grow ? step : -step))
        );
        setSize(next);
        persist({ size: next, collapsed });
      } else if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        toggleCollapsed();
      }
    },
    [axis, invert, size, minSize, maxSize, persist, collapsed, toggleCollapsed]
  );

  return {
    size,
    collapsed,
    setCollapsed,
    toggleCollapsed,
    dragging,
    startDrag: () => setDragging(true),
    reset,
    onHandleKeyDown,
    paneRef,
  };
}
