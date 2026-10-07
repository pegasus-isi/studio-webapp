"use client";

import { useEffect, useState } from "react";
import { apiUrl } from "@/lib/urls";
import { feedbackFormUrl } from "@/lib/feedback";

/**
 * "Feedback" cell for the left rail: opens the early-user survey in a new
 * tab with the Studio username filled in.
 *
 * In the rail rather than only in the account menu because early users are
 * asked to report as they go, and a report filed two clicks deep is a report
 * not filed.
 */
export function FeedbackButton({
  className,
  children,
}: {
  className: string;
  children: React.ReactNode;
}) {
  // Fetched up front, not on click: window.open must run synchronously inside
  // the click handler, or the browser treats the new tab as a popup and
  // blocks it.
  const [user, setUser] = useState<string | null>(null);
  useEffect(() => {
    fetch(apiUrl("whoami"))
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => setUser(d?.user ?? null))
      .catch(() => setUser(null));
  }, []);

  const open = () => {
    window.open(feedbackFormUrl({ user }), "_blank", "noopener,noreferrer");
  };

  return (
    <button
      onClick={open}
      title="Send feedback — opens the early-user survey in a new tab"
      data-tour="nav-feedback"
      className={className}
    >
      {children}
    </button>
  );
}
