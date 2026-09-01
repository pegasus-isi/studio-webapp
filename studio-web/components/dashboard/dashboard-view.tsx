"use client";

/**
 * The landing page (#314).
 *
 * Rendered by both routes that show it — `/` is where a user lands, `/home` is
 * where the rail links. See `app/(studio)/home/page.tsx` for why the second
 * route exists.
 *
 * This used to be a grid of ~14 cards with the assistant behind a button. If
 * the assistant is how Studio is meant to be driven, that is backwards: the
 * conversation should be the surface and the cards should be things the
 * assistant hands you. So the page is a chat opening turn — your state arrives
 * as something to act on rather than a dashboard to read, and the suggestions
 * are full sentences you can actually finish reading before you send them.
 *
 * The composer here is a handoff, not a second chat implementation: submitting
 * opens the real chat panel with the text already in it. One conversation,
 * one place it lives.
 */

import { useState } from "react";
import type { DashboardData } from "@/lib/api/client";
import { useDashboard } from "@/lib/hooks/use-dashboard";
import { usePanelStore } from "@/lib/stores/panel-store";
import { cn } from "@/lib/utils";
import { WelcomeCard } from "@/components/dashboard/welcome-card";
import { AppLink } from "@/components/layout/app-link";

type RecentRun = DashboardData["runs"]["recent"][number];

/**
 * Openers, in the user's language rather than Pegasus's.
 *
 * The previous set taught slash commands, and the design prototype's set
 * assumed you already knew what a scatter-gather, a DAX or an Apptainer image
 * was — on the first screen a domain scientist ever sees. `needsRuns` keeps
 * "why did my run fail" off an empty workspace, where it cannot be answered.
 */
const OPENERS: { title: string; prompt: string; needsRuns?: boolean }[] = [
  {
    title: "Run one analysis over a folder of files",
    prompt:
      "I have a folder of input files and I want to run the same analysis on every one of them. Help me build a workflow that does that.",
  },
  {
    title: "Bring an existing pipeline over",
    prompt:
      "I already have a Snakemake pipeline and I want to run it here instead. Can you convert it?",
  },
  {
    title: "Make my script portable",
    prompt:
      "Package my script so it runs the same way on every machine in the pool, and wire it into a workflow step.",
  },
  {
    title: "Check it will survive scaling up",
    prompt:
      "My workflow runs fine on a handful of inputs. Review it and tell me what will break when I scale it to a few hundred.",
    needsRuns: true,
  },
];

function statusTone(run: RecentRun): "bad" | "run" | "ok" | "idle" {
  if (run.status === "failed" || run.failed_jobs > 0) return "bad";
  if (run.status === "running") return "run";
  if (run.status === "succeeded") return "ok";
  return "idle";
}

const TONE_PILL: Record<string, string> = {
  bad: "bg-red-50 text-red-700 dark:bg-rose-500/10 dark:text-rose-300",
  run: "bg-pegasus-50 text-pegasus-700 dark:bg-pegasus-400/10 dark:text-pegasus-300",
  ok: "bg-green-50 text-green-700 dark:bg-emerald-500/10 dark:text-emerald-300",
  idle: "bg-muted text-fgmuted",
};

const TONE_WORD: Record<string, string> = {
  bad: "needs attention",
  run: "running",
  ok: "finished",
  idle: "ready",
};

/** One run, as the assistant would hand it to you: what happened, what to do. */
function RunCard({
  run,
  onAsk,
}: {
  run: RecentRun;
  onAsk: (prompt: string) => void;
}) {
  const tone = statusTone(run);
  const done = run.completed_jobs;
  const total = run.total_jobs;
  const pct = total > 0 ? Math.round((done / total) * 100) : 0;
  const label = run.run_name ? `${run.name} / ${run.run_name}` : run.name;

  return (
    <div
      className={cn(
        "rounded-xl border bg-surface p-3",
        tone === "bad" ? "border-red-300 dark:border-rose-500/40" : "border-line"
      )}
    >
      <div className="flex items-center gap-2">
        <span
          className={cn(
            "rounded-full px-2 py-0.5 text-[11px] font-semibold",
            TONE_PILL[tone]
          )}
        >
          {TONE_WORD[tone]}
        </span>
        <span className="min-w-0 flex-1 truncate text-sm font-semibold text-fg">
          {label}
        </span>
      </div>

      {tone === "bad" && run.failed_jobs > 0 ? (
        <p className="mt-1.5 text-xs text-fgmuted">
          {run.failed_jobs} of {total} task{total !== 1 ? "s" : ""} failed.
        </p>
      ) : total > 0 ? (
        <p className="mt-1.5 text-xs text-fgmuted">
          {done} of {total} task{total !== 1 ? "s" : ""} finished.
        </p>
      ) : null}

      {total > 0 && (
        <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-muted">
          <div
            className={cn(
              "h-full rounded-full",
              tone === "bad" ? "bg-red-500" : "bg-pegasus-600"
            )}
            style={{ width: `${pct}%` }}
          />
        </div>
      )}

      <div className="mt-2.5 flex flex-wrap items-center gap-2">
        {tone === "bad" && (
          <button
            onClick={() =>
              onAsk(
                `The run "${label}" failed — ${run.failed_jobs} of ${total} tasks did not complete. Explain what went wrong and how to fix it.`
              )
            }
            className="rounded-md bg-pegasus-600 px-2.5 py-1 text-xs font-semibold text-white hover:bg-pegasus-700"
          >
            Ask what went wrong
          </button>
        )}
        <AppLink
          href={`/workflows?run=${encodeURIComponent(run.run_id)}`}
          className="rounded-md border border-line px-2.5 py-1 text-xs font-medium text-fgmuted hover:bg-base hover:text-fg"
        >
          {tone === "ok" ? "View results" : "Open"}
        </AppLink>
      </div>
    </div>
  );
}

export function DashboardView() {
  const { data, isLoading } = useDashboard();
  const openChatWith = usePanelStore((s) => s.openChatWith);
  const [draft, setDraft] = useState("");

  const hasWorkflows =
    (data?.projects.total ?? 0) > 0 || (data?.runs.total ?? 0) > 0;

  // Runs worth putting in front of someone, most urgent first: anything that
  // failed, then anything still going, then the most recent finish. Three at
  // most — this is an opening turn, not a report.
  const recent = data?.runs.recent ?? [];
  const attention = recent.filter((r) => statusTone(r) === "bad");
  const running = recent.filter((r) => statusTone(r) === "run");
  const rest = recent.filter(
    (r) => statusTone(r) !== "bad" && statusTone(r) !== "run"
  );
  const highlighted = [...attention, ...running, ...rest].slice(0, 3);

  const openers = OPENERS.filter((o) => !o.needsRuns || hasWorkflows);

  const send = (text: string) => {
    const t = text.trim();
    if (!t) return;
    openChatWith(t);
    setDraft("");
  };

  const greeting = attention.length
    ? `${attention.length} ${attention.length === 1 ? "analysis needs" : "analyses need"} attention.`
    : running.length
      ? `${running.length} ${running.length === 1 ? "analysis is" : "analyses are"} running.`
      : hasWorkflows
        ? "Nothing is running right now."
        : "Nothing here yet — describe what you want to analyse and I'll build it.";

  return (
    <div className="h-full overflow-auto">
      <div className="mx-auto flex min-h-full max-w-3xl flex-col px-6 py-8">
        {/* First-run onboarding (#241, Phase 6) — shown once, then never. */}
        <WelcomeCard />

        {/* The assistant's opening turn. Everything the old dashboard showed as
            tiles is in here, as things to act on. */}
        <div className="flex gap-3">
          <span
            aria-hidden="true"
            className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-pegasus-50 text-[11px] font-bold text-pegasus-700 dark:bg-pegasus-400/10 dark:text-pegasus-300"
          >
            AI
          </span>
          <div className="min-w-0 flex-1">
            <p className="text-sm leading-relaxed text-fg">
              {isLoading ? "Checking your workspace…" : greeting}
            </p>

            {highlighted.length > 0 && (
              <div className="mt-3 space-y-2">
                {highlighted.map((r) => (
                  <RunCard key={r.run_id} run={r} onAsk={send} />
                ))}
              </div>
            )}

            {recent.length > highlighted.length && (
              <AppLink
                href="/workflows"
                className="mt-2 inline-block text-xs font-medium text-pegasus-700 hover:underline dark:text-pegasus-300"
              >
                All analyses →
              </AppLink>
            )}

            <p className="mt-5 text-sm text-fgmuted">
              {hasWorkflows ? "Or start something new:" : "Try one of these:"}
            </p>
            <div className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-2">
              {openers.map((o) => (
                <button
                  key={o.title}
                  onClick={() => send(o.prompt)}
                  className="rounded-xl border border-line bg-surface p-3 text-left transition-colors hover:border-pegasus-500 hover:bg-base"
                >
                  <span className="block text-xs font-semibold text-fg">
                    {o.title}
                  </span>
                  {/* Shown in full. A card that truncates the prompt asks you
                      to send text you were not allowed to read. */}
                  <span className="mt-1 block text-xs leading-relaxed text-fgmuted">
                    {o.prompt}
                  </span>
                </button>
              ))}
            </div>

            {!hasWorkflows && (
              <p className="mt-3 text-xs text-fgmuted">
                Rather start from something ready-made?{" "}
                <AppLink
                  href="/marketplace"
                  className="font-medium text-pegasus-700 hover:underline dark:text-pegasus-300"
                >
                  Browse examples
                </AppLink>
                .
              </p>
            )}
          </div>
        </div>

        {/* Composer. Hands off to the chat panel rather than reimplementing it. */}
        <div className="mt-8">
          <form
            onSubmit={(e) => {
              e.preventDefault();
              send(draft);
            }}
            className="rounded-xl border border-line bg-surface p-2.5 shadow-sm focus-within:border-pegasus-500"
          >
            <label htmlFor="home-composer" className="sr-only">
              Describe what you want to build, run, or fix
            </label>
            <textarea
              id="home-composer"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  send(draft);
                }
              }}
              rows={2}
              placeholder="Describe what you want to build, run, or fix"
              className="w-full resize-none border-0 bg-transparent px-1 py-1 text-sm text-fg outline-none placeholder:text-fgsubtle"
            />
            <div className="flex items-center gap-2 px-1">
              <span className="text-[11px] text-fgsubtle">
                Enter to send · Shift+Enter for a new line
              </span>
              <button
                type="submit"
                disabled={!draft.trim()}
                className="ml-auto rounded-lg bg-pegasus-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-pegasus-700 disabled:cursor-not-allowed disabled:opacity-40"
              >
                Send
              </button>
            </div>
          </form>
        </div>

        {/* Resource pressure — only when something needs attention. */}
        {(data?.resources?.pressure?.length ?? 0) > 0 && (
          <AppLink
            href="/provisioning"
            className="mt-4 flex items-start gap-2 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-800 hover:bg-amber-100 dark:border-amber-500/40 dark:bg-amber-500/10 dark:text-amber-300"
          >
            <span aria-hidden="true">⚠</span>
            <span>
              {data?.resources.pressure[0].title}
              {(data?.resources.pressure.length ?? 0) > 1 &&
                ` +${(data?.resources.pressure.length ?? 1) - 1} more`}{" "}
              — check compute
            </span>
          </AppLink>
        )}
      </div>
    </div>
  );
}
