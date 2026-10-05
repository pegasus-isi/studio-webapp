"use client";

/**
 * Guided tour (#317).
 *
 * Replaces the one-off welcome card, which explained Studio on the one screen
 * where there is nothing to point at, and was gone by the time the user
 * reached the pages it had described.
 *
 * This points at real elements instead. A step names an anchor
 * (`data-tour="..."`), and the tour spotlights whatever is on screen with that
 * attribute. A step whose anchor is missing — a rail cell behind the Advanced
 * disclosure, a panel the user has closed — is skipped rather than pointing at
 * nothing, which is what makes one step list safe to reuse across states.
 *
 * The tour does not navigate. Every step is on the page you are already on,
 * because a tour that changes routes mid-sequence loses the highlight, the
 * scroll position and usually the user.
 *
 * A step with no anchor is a centred card over the whole page — for things
 * that are not *on* the page, like what Studio records about you. Such a step
 * may render its own content (`render`), and when it brings its own buttons
 * (`ownControls`) the tour's Back/Next row is replaced by them, so a form step
 * cannot be skipped past by a Next that discards what was typed.
 */

import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useState,
  type ReactNode,
} from "react";
import { cn } from "@/lib/utils";

export interface TourStep {
  /** Value of the `data-tour` attribute to spotlight. Omit for a centred
   *  card that is always shown. */
  anchor?: string;
  title: string;
  body?: string;
  /** Custom content below the body. `next` advances (or closes on the last
   *  step), for steps that finish themselves. */
  render?: (ctx: { next: () => void }) => ReactNode;
  /** The step's own content supplies its buttons; hide Skip/Back/Next. */
  ownControls?: boolean;
  /** Card width in px (defaults to 300); forms need more room. */
  width?: number;
}

interface Rect {
  top: number;
  left: number;
  width: number;
  height: number;
}

const PAD = 6;
const CARD_W = 300;
const GAP = 12;

function rectOf(anchor: string | undefined): Rect | null {
  if (!anchor) return null;
  const el = document.querySelector(`[data-tour="${anchor}"]`);
  if (!el) return null;
  const r = el.getBoundingClientRect();
  // A zero-size box means the element is present but not laid out (a closed
  // <details>, `display:none`) — treat that as absent.
  if (r.width === 0 && r.height === 0) return null;
  return { top: r.top, left: r.left, width: r.width, height: r.height };
}

export function GuidedTour({
  steps,
  open,
  onClose,
}: {
  steps: TourStep[];
  open: boolean;
  onClose: () => void;
}) {
  const [i, setI] = useState(0);
  const [rect, setRect] = useState<Rect | null>(null);

  // Only steps whose anchor is actually on the page.
  const [live, setLive] = useState<TourStep[]>([]);
  useLayoutEffect(() => {
    if (!open) return;
    setLive(steps.filter((s) => !s.anchor || rectOf(s.anchor) !== null));
    setI(0);
  }, [open, steps]);

  const step = live[i];

  const measure = useCallback(() => {
    if (!step) return;
    setRect(rectOf(step.anchor));
  }, [step]);

  useLayoutEffect(measure, [measure]);

  useEffect(() => {
    if (!open) return;
    window.addEventListener("resize", measure);
    window.addEventListener("scroll", measure, true);
    return () => {
      window.removeEventListener("resize", measure);
      window.removeEventListener("scroll", measure, true);
    };
  }, [open, measure]);

  const close = useCallback(() => {
    setI(0);
    onClose();
  }, [onClose]);

  const next = useCallback(() => {
    if (i + 1 >= live.length) close();
    else setI(i + 1);
  }, [i, live.length, close]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      // Inside a form field the arrows move the caret and Enter submits the
      // form; hijacking them would turn "fix a typo" into "go back a step".
      // Escape still closes — it is the one key nobody types into a field.
      const t = e.target as HTMLElement | null;
      const typing =
        !!t &&
        (t.tagName === "INPUT" ||
          t.tagName === "SELECT" ||
          t.tagName === "TEXTAREA" ||
          t.isContentEditable);
      if (typing && e.key !== "Escape") return;
      if (step?.ownControls && e.key !== "Escape") return;
      if (e.key === "Escape") {
        e.preventDefault();
        close();
      } else if (e.key === "ArrowRight" || e.key === "Enter") {
        e.preventDefault();
        next();
      } else if (e.key === "ArrowLeft") {
        e.preventDefault();
        setI((n) => Math.max(0, n - 1));
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, close, next, step]);

  if (!open || !step) return null;

  // Prefer the right of the anchor (the rail runs down the left edge); flip to
  // the left when there is no room, and centre when the anchor is unmeasurable.
  const cardW = step.width ?? CARD_W;
  let cardStyle: React.CSSProperties;
  if (rect) {
    const spaceRight = window.innerWidth - (rect.left + rect.width);
    const placeRight = spaceRight > cardW + GAP * 2;
    const left = placeRight
      ? rect.left + rect.width + GAP
      : Math.max(GAP, rect.left - cardW - GAP);
    const top = Math.min(
      Math.max(GAP, rect.top + rect.height / 2 - 70),
      Math.max(GAP, window.innerHeight - 190)
    );
    cardStyle = { top, left, width: cardW };
  } else {
    cardStyle = {
      top: "50%",
      left: "50%",
      width: cardW,
      maxWidth: `calc(100vw - ${GAP * 2}px)`,
      maxHeight: `calc(100vh - ${GAP * 2}px)`,
      overflowY: "auto",
      transform: "translate(-50%, -50%)",
    };
  }

  return (
    <div
      className="fixed inset-0 z-[70]"
      role="dialog"
      aria-modal="true"
      aria-labelledby="guided-tour-title"
    >
      {/* Scrim, with a hole cut for the anchor. Four panes rather than an SVG
          mask so the spotlit element stays fully interactive. */}
      {rect ? (
        <>
          <div
            className="absolute inset-x-0 top-0 bg-black/45"
            style={{ height: Math.max(0, rect.top - PAD) }}
            onClick={close}
          />
          <div
            className="absolute inset-x-0 bottom-0 bg-black/45"
            style={{ top: rect.top + rect.height + PAD }}
            onClick={close}
          />
          <div
            className="absolute left-0 bg-black/45"
            style={{
              top: rect.top - PAD,
              height: rect.height + PAD * 2,
              width: Math.max(0, rect.left - PAD),
            }}
            onClick={close}
          />
          <div
            className="absolute right-0 bg-black/45"
            style={{
              top: rect.top - PAD,
              height: rect.height + PAD * 2,
              left: rect.left + rect.width + PAD,
            }}
            onClick={close}
          />
          <div
            aria-hidden="true"
            className="pointer-events-none absolute rounded-xl ring-2 ring-pegasus-400"
            style={{
              top: rect.top - PAD,
              left: rect.left - PAD,
              width: rect.width + PAD * 2,
              height: rect.height + PAD * 2,
            }}
          />
        </>
      ) : (
        <div className="absolute inset-0 bg-black/45" onClick={close} />
      )}

      <div
        style={cardStyle}
        className="absolute rounded-xl border border-line bg-surface p-4 shadow-2xl"
      >
        <p className="text-[10px] font-semibold uppercase tracking-[0.13em] text-fgsubtle">
          {i + 1} of {live.length}
        </p>
        <h2
          id="guided-tour-title"
          className="mt-1.5 text-sm font-semibold text-fg"
        >
          {step.title}
        </h2>
        {step.body && (
          <p className="mt-1.5 text-xs leading-relaxed text-fgmuted">
            {step.body}
          </p>
        )}
        {step.render && <div className="mt-2.5">{step.render({ next })}</div>}

        {!step.ownControls && (
          <div className="mt-3.5 flex items-center gap-2">
            <div className="flex gap-1" aria-hidden="true">
              {live.map((s, n) => (
                <span
                  key={s.anchor ?? `step-${n}`}
                  className={cn(
                    "h-1.5 w-1.5 rounded-full",
                    n === i ? "bg-pegasus-600" : "bg-line"
                  )}
                />
              ))}
            </div>
            <button
              onClick={close}
              className="ml-auto rounded-lg px-2 py-1 text-xs font-medium text-fgmuted hover:text-fg"
            >
              Skip
            </button>
            {i > 0 && (
              <button
                onClick={() => setI(i - 1)}
                className="rounded-lg border border-line px-2.5 py-1 text-xs font-medium text-fgmuted hover:text-fg"
              >
                Back
              </button>
            )}
            <button
              onClick={next}
              className="rounded-lg bg-pegasus-600 px-2.5 py-1 text-xs font-semibold text-white hover:bg-pegasus-700"
            >
              {i + 1 >= live.length ? "Done" : "Next"}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
