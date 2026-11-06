"use client";

import { useEffect, useRef, useState } from "react";
import { SUPPORT_EMAIL } from "@/components/layout/user-menu";

/**
 * "Contact" cell for the left rail: opens a small card with the team's
 * address, a Copy button and a mailto: link.
 *
 * Not a bare mailto: link. With no mail client registered with the browser —
 * the usual case for webmail users — clicking one does nothing at all, and
 * the user is left without the one thing they came for, the address.
 *
 * The card is `position: fixed` beside the button rather than absolutely
 * positioned inside the rail, because the rail's nav scrolls
 * (`overflow-y-auto`) and would clip it.
 */
export function ContactButton({
  className,
  children,
}: {
  className: string;
  children: React.ReactNode;
}) {
  const [pos, setPos] = useState<{ left: number; bottom: number } | null>(null);
  const [copied, setCopied] = useState(false);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const cardRef = useRef<HTMLDivElement>(null);
  const open = pos !== null;

  useEffect(() => {
    if (!open) return;
    function onDown(e: MouseEvent) {
      const t = e.target as Node;
      if (cardRef.current?.contains(t) || buttonRef.current?.contains(t)) return;
      setPos(null);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setPos(null);
    }
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const toggle = () => {
    if (open) {
      setPos(null);
      return;
    }
    const r = buttonRef.current?.getBoundingClientRect();
    if (!r) return;
    // Bottom-aligned with the cell and opening rightward over the content,
    // like the account menu: the cell sits near the foot of the rail.
    setPos({ left: r.right + 8, bottom: Math.max(8, window.innerHeight - r.bottom) });
    setCopied(false);
  };

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(SUPPORT_EMAIL);
      setCopied(true);
    } catch {
      // Clipboard needs a secure context and permission; the address is
      // select-all text, so a manual copy still works.
      setCopied(false);
    }
  };

  return (
    <>
      <button
        ref={buttonRef}
        onClick={toggle}
        title="Contact us"
        aria-expanded={open}
        aria-haspopup="dialog"
        className={className}
      >
        {children}
      </button>
      {pos && (
        <div
          ref={cardRef}
          role="dialog"
          aria-label="Contact us"
          style={{ left: pos.left, bottom: pos.bottom }}
          className="fixed z-50 w-64 rounded-lg border border-line bg-surface p-4 text-sm text-fg shadow-lg"
        >
          <p className="text-xs text-fgsubtle">Questions or problems? Email us at</p>
          <p className="mt-1 select-all break-all font-medium">{SUPPORT_EMAIL}</p>
          <div className="mt-3 flex gap-2">
            <button
              onClick={copy}
              className="flex-1 rounded-md border border-line px-2 py-1.5 text-xs hover:bg-muted"
            >
              {copied ? "Copied ✓" : "Copy address"}
            </button>
            <a
              href={`mailto:${SUPPORT_EMAIL}`}
              onClick={() => setPos(null)}
              className="flex-1 rounded-md bg-accent px-2 py-1.5 text-center text-xs font-medium text-accent-fg hover:opacity-90"
            >
              Open mail app
            </a>
          </div>
        </div>
      )}
    </>
  );
}
