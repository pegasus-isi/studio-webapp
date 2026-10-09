"use client";

/**
 * The landing page (#314).
 *
 * Rendered by both routes that show it — `/` is where a user lands, `/home` is
 * where the rail links. See `app/(studio)/home/page.tsx` for why the second
 * route exists.
 *
 * A centred welcome, four ways in, and a gallery of things to ask.
 *
 * There is deliberately no composer here. One lived at the bottom of this page
 * and all it did was open the docked chat panel with your text in it, so you
 * typed into one box and your words appeared in another — two chat inputs
 * where only one was real. The openers hand off to the same panel, but they
 * are prompts rather than an input, so nothing pretends to be the
 * conversation it is not.
 */

import { useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import type { DashboardData } from "@/lib/api/client";
import { useDashboard } from "@/lib/hooks/use-dashboard";
import { usePanelStore } from "@/lib/stores/panel-store";
import { cn } from "@/lib/utils";
import { AppLink } from "@/components/layout/app-link";
import { GlossaryList } from "@/components/ui/glossary-term";
import { GuidedTour, type TourStep } from "@/components/onboarding/guided-tour";
import {
  useUIPreferences,
  useUpdateUIPreferences,
} from "@/lib/hooks/use-ui-prefs";

type RecentRun = DashboardData["runs"]["recent"][number];

/** Pastel icon tiles, one hue per kind of thing. Tailwind needs literals. */
const TILE = {
  sky: "bg-sky-50 text-sky-500 dark:bg-sky-400/10 dark:text-sky-300",
  indigo: "bg-indigo-50 text-indigo-500 dark:bg-indigo-400/10 dark:text-indigo-300",
  violet: "bg-violet-50 text-violet-500 dark:bg-violet-400/10 dark:text-violet-300",
  mint: "bg-emerald-50 text-emerald-500 dark:bg-emerald-400/10 dark:text-emerald-300",
  rose: "bg-rose-50 text-rose-500 dark:bg-rose-400/10 dark:text-rose-300",
  sand: "bg-amber-50 text-amber-500 dark:bg-amber-400/10 dark:text-amber-300",
} as const;

type Tile = keyof typeof TILE;

function Tile({
  tone,
  children,
  size = "md",
}: {
  tone: Tile;
  children: React.ReactNode;
  size?: "md" | "sm";
}) {
  return (
    <span
      aria-hidden="true"
      className={cn(
        "flex shrink-0 items-center justify-center rounded-xl",
        size === "md" ? "h-10 w-10" : "h-8 w-8",
        TILE[tone]
      )}
    >
      {children}
    </span>
  );
}

function Icon({ d }: { d: React.ReactNode }) {
  return (
    <svg
      width="17"
      height="17"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.9"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      {d}
    </svg>
  );
}

const I = {
  workflow: (
    <>
      <circle cx="6" cy="18" r="2.2" />
      <circle cx="18" cy="6" r="2.2" />
      <path d="M6 15.8V9a3 3 0 0 1 3-3h6.8" />
    </>
  ),
  notebook: (
    <>
      <path d="M4 4h7v16H4zM13 4h7v16h-7z" />
    </>
  ),
  catalog: (
    <>
      <path d="M4 6h16v13H4z" />
      <path d="M8 6V4h8v2" />
    </>
  ),
  terminal: (
    <>
      <path d="M4 17l6-5-6-5M12 19h8" />
    </>
  ),
  files: (
    <>
      <path d="M14 3v5h5" />
      <path d="M19 8v12H5V4h9z" />
    </>
  ),
  convert: (
    <>
      <path d="M4 8h13l-3-3M20 16H7l3 3" />
    </>
  ),
  box: (
    <>
      <path d="M12 3l8 4.5v9L12 21l-8-4.5v-9z" />
      <path d="M4 7.5l8 4.5 8-4.5M12 12v9" />
    </>
  ),
  scale: (
    <>
      <path d="M4 19V5M4 19h16" />
      <path d="M8 16v-4M12 16V9M16 16v-7" />
    </>
  ),
} as const;

/**
 * Openers, in the user's language rather than Pegasus's.
 *
 * The previous set taught slash commands, and the design prototype's set
 * assumed you already knew what a scatter-gather, a DAX or an Apptainer image
 * was — on the first screen a domain scientist ever sees. All four show on
 * an empty workspace too: "check it survives scaling up" used to wait for a
 * first run, but the question is one people arrive with, and hiding it made
 * the set look incomplete next to the mockups.
 */
const OPENERS: {
  title: string;
  prompt: string;
  tone: Tile;
  icon: React.ReactNode;
}[] = [
  {
    title: "Run one analysis over many files",
    prompt:
      "I have a folder of input files and I want to run the same analysis on every one of them. Help me build a workflow that does that.",
    tone: "sky",
    icon: I.files,
  },
  {
    title: "Bring an existing pipeline over",
    prompt:
      "I already have a Snakemake pipeline and I want to run it here instead. Can you convert it?",
    tone: "indigo",
    icon: I.convert,
  },
  {
    title: "Make my script portable",
    prompt:
      "Package my script so it runs the same way on every machine in the pool, and wire it into a workflow step.",
    tone: "violet",
    icon: I.box,
  },
  {
    title: "Check it survives scaling up",
    prompt:
      "My workflow runs fine on a handful of inputs. Review it and tell me what will break when I scale it to a few hundred.",
    tone: "sand",
    icon: I.scale,
  },
];

function statusTone(run: RecentRun): "bad" | "run" | "ok" | "idle" {
  if (run.status === "failed" || run.failed_jobs > 0) return "bad";
  if (run.status === "running") return "run";
  if (run.status === "succeeded") return "ok";
  return "idle";
}

/**
 * A single strip, and only when something actually wants the user.
 *
 * The prototype's welcome screen carries no state at all, which is right for a
 * first visit and wrong on day 100 — a failed run should not need hunting for.
 * One line keeps the page as calm as the prototype when there is nothing to
 * say, which is most of the time.
 */
/**
 * Failed runs that are still the latest word on their workflow.
 *
 * `runs` is newest first. A failure followed by a newer submitted run of the
 * same workflow has been dealt with (re-run, fixed or not), so it is not news:
 * flagging it kept "streamflow needs attention" on the home page after the
 * re-run had succeeded. A newer run that was only planned, never submitted,
 * does not supersede anything.
 */
function unresolvedFailures(runs: RecentRun[]): RecentRun[] {
  const seen = new Set<string>();
  const out: RecentRun[] = [];
  for (const r of runs) {
    if (r.status === "planned" || r.status === "planning" || r.status === "unknown") {
      continue;
    }
    if (seen.has(r.name)) continue;
    seen.add(r.name);
    if (statusTone(r) === "bad") out.push(r);
  }
  return out;
}

function AttentionStrip({
  runs,
  onAsk,
}: {
  runs: RecentRun[];
  onAsk: (prompt: string) => void;
}) {
  const bad = unresolvedFailures(runs);
  const running = runs.filter((r) => statusTone(r) === "run");
  if (bad.length === 0 && running.length === 0) return null;

  const first = bad[0];
  if (first) {
    const label = first.run_name ? `${first.name} / ${first.run_name}` : first.name;
    return (
      <div className="mx-auto mb-6 flex w-full max-w-3xl flex-wrap items-center gap-x-3 gap-y-1.5 rounded-xl border border-rose-200 bg-rose-50/70 px-3.5 py-2.5 text-sm dark:border-rose-500/30 dark:bg-rose-500/10">
        <span className="font-semibold text-rose-700 dark:text-rose-300">
          {first.name} needs attention
        </span>
        <span className="text-fgmuted">
          {first.failed_jobs} of {first.total_jobs} tasks failed
          {bad.length > 1 && ` · ${bad.length - 1} more`}
        </span>
        <button
          onClick={() =>
            onAsk(
              `The run "${label}" failed — ${first.failed_jobs} of ${first.total_jobs} tasks did not complete. Explain what went wrong and how to fix it.`
            )
          }
          className="ml-auto rounded-lg bg-pegasus-600 px-2.5 py-1 text-xs font-semibold text-white hover:bg-pegasus-700"
        >
          Ask what went wrong
        </button>
        <AppLink
          href={`/workflows?run=${encodeURIComponent(first.run_id)}`}
          className="rounded-lg border border-line bg-surface px-2.5 py-1 text-xs font-medium text-fgmuted hover:text-fg"
        >
          Open
        </AppLink>
      </div>
    );
  }

  const r = running[0];
  return (
    <div className="mx-auto mb-6 flex w-full max-w-3xl flex-wrap items-center gap-x-3 gap-y-1.5 rounded-xl border border-line bg-surface px-3.5 py-2.5 text-sm">
      <span className="font-semibold text-fg">{r.name} is running</span>
      <span className="text-fgmuted">
        {r.completed_jobs} of {r.total_jobs} tasks finished
        {running.length > 1 && ` · ${running.length - 1} more running`}
      </span>
      <AppLink
        href={`/workflows?run=${encodeURIComponent(r.run_id)}`}
        className="ml-auto rounded-lg border border-line px-2.5 py-1 text-xs font-medium text-fgmuted hover:text-fg"
      >
        Watch it
      </AppLink>
    </div>
  );
}

/**
 * What a newcomer needs to be able to find (#317).
 *
 * Each step points at a real rail cell rather than describing it, and the tour
 * skips any anchor that isn't on screen — Resources and Terminal sit behind
 * the Advanced disclosure for most users, so their steps simply don't run.
 */
const TOUR: TourStep[] = [
  {
    anchor: "nav-analyses",
    title: "Analyses",
    body: "Everything you have built, and every run of it. The graph of the steps and the files a run produced both live here.",
  },
  {
    anchor: "nav-data",
    title: "Data",
    body: "Your own input files. Drag them in here and an analysis can use them — no copying to a server yourself.",
  },
  {
    anchor: "nav-examples",
    title: "Examples",
    body: "Ready-made analyses from the Pegasus community. Add one and it becomes yours to run and edit.",
  },
  {
    anchor: "nav-chat",
    title: "The assistant, anywhere",
    body: "It follows you around the app. Open it on any page to ask about whatever you are looking at.",
  },
  {
    anchor: "nav-settings",
    title: "Settings",
    body: "Which model answers you, which tools are installed, and where Studio looks for your workflows.",
  },
  {
    anchor: "search",
    title: "Find anything",
    body: "Press ⌘K anywhere to jump to an analysis, an example, or a page.",
  },
  {
    anchor: "home-openers",
    title: "Start here",
    body: "Pick whichever of these is closest to what you want. It opens the assistant with the question already written, and you can edit it before sending.",
  },
];

export function DashboardView() {
  const { data } = useDashboard();
  const openChatWith = usePanelStore((s) => s.openChatWith);
  const { prefs, isLoading: prefsLoading } = useUIPreferences();
  const updatePrefs = useUpdateUIPreferences();
  const [tourOpen, setTourOpen] = useState(false);
  const [glossary, setGlossary] = useState(false);

  // "Take a tour" in the account menu links here with ?tour=1, so the tour is
  // restartable from any page rather than only from a link at the bottom of
  // this one.
  const searchParams = useSearchParams();
  const tourRequested = searchParams.get("tour") === "1";
  useEffect(() => {
    if (tourRequested) setTourOpen(true);
  }, [tourRequested]);

  // First visit runs the tour on its own. `welcome_dismissed` is the same
  // preference the old welcome card used, so anyone who already dismissed that
  // is not re-onboarded by the replacement.
  const firstVisit = !prefsLoading && !prefs.welcome_dismissed;
  useEffect(() => {
    if (!firstVisit) return;
    // A frame, so the rail and the composer are laid out before the tour
    // measures them — a step whose anchor has no box gets skipped.
    const t = requestAnimationFrame(() => setTourOpen(true));
    return () => cancelAnimationFrame(t);
  }, [firstVisit]);

  const endTour = () => {
    setTourOpen(false);
    if (!prefs.welcome_dismissed) {
      updatePrefs.mutate({ welcome_dismissed: true });
    }
  };

  // Opens the docked chat panel with the text already in the input, so the
  // user can edit before sending.
  const send = (text: string) => {
    const t = text.trim();
    if (t) openChatWith(t);
  };

  const actionClass =
    "flex flex-col items-center gap-2 rounded-2xl border border-line bg-surface px-3 py-5 text-center transition-colors hover:border-pegasus-300 hover:bg-pegasus-50/40 dark:hover:bg-pegasus-400/5";
  const actionLabel = "text-[13px] font-medium text-fg";

  return (
    <div className="flex h-full flex-col">
      <div className="flex-1 overflow-auto">
        <div className="mx-auto max-w-3xl px-6 pb-8 pt-12">
          <AttentionStrip runs={data?.runs.recent ?? []} onAsk={send} />

          {/* Welcome */}
          <div className="flex flex-col items-center text-center">
            <span className="flex h-14 w-14 items-center justify-center rounded-2xl bg-surface shadow-sm">
              <img
                src="pegasus-ai-logo-light.png"
                alt=""
                className="block h-8 w-auto dark:hidden"
              />
              <img
                src="pegasus-ai-logo-dark.png"
                alt=""
                className="hidden h-8 w-auto dark:block"
              />
            </span>
            <h1 className="mt-5 text-3xl font-semibold tracking-tight text-fg">
              Welcome to{" "}
              <span className="text-pegasus-600 dark:text-pegasus-300">
                PegasusAI Studio
              </span>
            </h1>
            <p className="mt-2 text-sm text-fgmuted">
              Build, run, and monitor Pegasus workflows — with AI, in one
              workspace.
            </p>
          </div>

          {/* Four ways in */}
          <div className="mt-8 grid grid-cols-2 gap-3 sm:grid-cols-4">
            <button
              onClick={() =>
                send(
                  "Help me build a new Pegasus workflow. Ask me what the analysis needs to do."
                )
              }
              className={actionClass}
            >
              <Tile tone="sky">
                <Icon d={I.workflow} />
              </Tile>
              <span className={actionLabel}>New workflow</span>
            </button>
            <AppLink href="/notebooks" className={actionClass}>
              <Tile tone="indigo">
                <Icon d={I.notebook} />
              </Tile>
              <span className={actionLabel}>Open notebook</span>
            </AppLink>
            <AppLink href="/marketplace" className={actionClass}>
              <Tile tone="violet">
                <Icon d={I.catalog} />
              </Tile>
              <span className={actionLabel}>Browse examples</span>
            </AppLink>
            <AppLink href="/workflows" className={actionClass}>
              <Tile tone="mint">
                <Icon d={I.scale} />
              </Tile>
              <span className={actionLabel}>My analyses</span>
            </AppLink>
          </div>

          {/* Openers */}
          <p className="mt-9 text-[11px] font-semibold uppercase tracking-[0.13em] text-fgsubtle">
            Try an example
          </p>
          <div
            data-tour="home-openers"
            className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2"
          >
            {OPENERS.map((o) => (
              <button
                key={o.title}
                onClick={() => send(o.prompt)}
                className="flex items-start gap-3 rounded-2xl border border-line bg-surface p-3.5 text-left transition-colors hover:border-pegasus-300 hover:bg-pegasus-50/40 dark:hover:bg-pegasus-400/5"
              >
                <Tile tone={o.tone} size="sm">
                  <Icon d={o.icon} />
                </Tile>
                <span className="min-w-0">
                  <span className="block text-[13px] font-semibold text-fg">
                    {o.title}
                  </span>
                  {/* Shown in full. A card that truncates the prompt asks you
                      to send text you were not allowed to read. */}
                  <span className="mt-1 block text-xs leading-relaxed text-fgmuted">
                    {o.prompt}
                  </span>
                </span>
              </button>
            ))}
          </div>

          {/* Onboarding, on demand. The old welcome card explained Studio on
              the one screen with nothing to point at, and was gone by the time
              you reached the pages it described (#317). The same tour is in
              the account menu, so it is restartable from anywhere. */}
          <div className="mt-8 flex flex-wrap items-center gap-x-4 gap-y-2 border-t border-line pt-5 text-xs">
            <button
              onClick={() => setTourOpen(true)}
              className="font-semibold text-pegasus-700 hover:underline dark:text-pegasus-300"
            >
              Show me around
            </button>
            <button
              onClick={() => setGlossary((v) => !v)}
              aria-expanded={glossary}
              className="text-fgmuted hover:text-fg"
            >
              What do the words mean?
            </button>
            <span className="text-fgsubtle">
              You can restart the tour any time from the account menu.
            </span>
          </div>

          {glossary && (
            <div className="mt-3 rounded-xl border border-line bg-surface p-4">
              <GlossaryList />
            </div>
          )}
        </div>
      </div>

      <GuidedTour steps={TOUR} open={tourOpen} onClose={endTour} />
    </div>
  );
}
