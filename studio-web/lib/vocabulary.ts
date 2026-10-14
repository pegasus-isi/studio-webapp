/**
 * The single source of user-facing vocabulary.
 *
 * Studio's UI grew up speaking Pegasus and HTCondor — generate/plan/submit,
 * DAG, job, site, held — which is precise for a workflow engineer and opaque
 * to the domain scientists Studio is for. This module is Phase 0 of the
 * non-specialist redesign (docs/UX-NON-SPECIALIST-DESIGN.md, #241): one place
 * where every user-facing term is decided, so a later change of mind is one
 * edit rather than a grep across 60 components.
 *
 * ## Not yet consumed
 *
 * Nothing imports this yet, deliberately. A merge here deploys to live user
 * pods, so the copy flip ships behind a per-user flag in Phases 1/2/7, after
 * the terminology is validated with real users (docs/ux-terminology-test.md).
 * Landing the module first lets those phases consume a settled vocabulary
 * instead of inventing one per PR.
 *
 * ## Translate jargon, not English
 *
 * The failure mode to avoid is reflexive renaming. "Running" and "Failed" are
 * already plain English and testing them would waste a participant's hour;
 * "held", "condorpool" and "DAG" are the words that actually cost a scientist
 * their afternoon. Every entry below earns its place by being a term a
 * first-time user cannot reasonably be expected to know. Where the honest
 * answer is "this concept should not be surfaced at all", the UI field is
 * `null` and the term is hidden outside Advanced disclosures.
 */

/** One vocabulary entry: what we say, what Pegasus says, and what it means. */
export interface Term {
  /** What the UI shows. `null` = concept is hidden from non-Advanced UI. */
  readonly ui: string | null;
  /** The Pegasus/HTCondor term, kept for Advanced views and tooltips. */
  readonly pegasus: string;
  /** Plain-language definition, used by the Phase 6 glossary tooltips. */
  readonly definition: string;
}

/**
 * The mapping table. Keys are stable identifiers, not display strings — call
 * sites reference `TERMS.job.ui`, never the literal "Step", so re-deciding a
 * term does not require touching the call site.
 */
export const TERMS = {
  workflowProject: {
    ui: "Analysis",
    pegasus: "workflow project",
    definition:
      "A piece of work you can run: the steps to perform, the software they need, and the inputs you give them.",
  },
  job: {
    ui: "Step",
    pegasus: "job",
    definition:
      "One unit of work inside an analysis — typically a single program run on a single piece of data.",
  },
  dag: {
    ui: "Pipeline diagram",
    pegasus: "DAG",
    definition:
      "A picture of the steps in your analysis and which ones must finish before others can start.",
  },
  softwareEnvironment: {
    ui: "Software environment",
    pegasus: "Apptainer image (.sif)",
    definition:
      "A packaged copy of the programs your analysis needs, so it runs the same way everywhere.",
  },
  computingCapacity: {
    ui: "Computing capacity",
    pegasus: "pool / slots / machines",
    definition:
      "How many computers are available to run your steps, and how many are busy right now.",
  },
  executionSite: {
    ui: "Where it runs",
    pegasus: "execution site",
    definition:
      "Whether your analysis runs on this machine, on Studio’s own pool, or on a cluster you set up.",
  },
  analyzerOutput: {
    ui: "What went wrong",
    pegasus: "pegasus-analyzer output",
    definition:
      "The diagnosis of a failed run: which step failed and the error it produced.",
  },
  // Deliberately hidden. A run directory is an implementation detail of where
  // Pegasus put its bookkeeping; surfacing it invites users to go spelunking
  // in a directory they should never need to open.
  runDir: {
    ui: null,
    pegasus: "run directory",
    definition:
      "The folder where Pegasus keeps the bookkeeping for one run of an analysis.",
  },
  // Hidden for the same reason: generate/plan/submit are phases of Pegasus's
  // pipeline, not steps a user should have to sequence. Phase 1 folds all
  // three behind a single Run action.
  planning: {
    ui: null,
    pegasus: "generate / plan / submit",
    definition:
      "The preparation Pegasus does before your analysis starts running.",
  },
} as const satisfies Record<string, Term>;

/**
 * Where a run executes. Replaces the raw `condorpool` / `local` strings, which
 * name HTCondor concepts rather than answering the user's actual question:
 * "is this going to run here, or somewhere bigger?"
 */
export interface SiteChoice {
  readonly value: string;
  readonly label: string;
  readonly hint: string;
}

export const EXECUTION_SITES: ReadonlyArray<SiteChoice> = [
  {
    value: "local",
    label: "This machine",
    hint: "Best for small tests. Uses only this workspace's own resources.",
  },
  {
    value: "condorpool",
    label: "Studio’s own pool",
    hint: "Studio’s own HTCondor pool. Best for a full analysis; your steps may wait for a free slot.",
  },
];

const OTHER_POOL_HINT =
  "An HTCondor pool from this project’s site catalog. Your steps may wait for a free slot.";

const BATCH_CLUSTER: Omit<SiteChoice, "value"> = {
  label: "HPC cluster",
  hint: "Best for a full analysis on this cluster. Submits your steps to its batch scheduler, which may mean waiting in its queue.",
};

/**
 * "Where it runs" choices for the sites a project can actually plan against
 * (from the backend). Keeps the plain-language labels: the default condor
 * pool is "Studio's own pool", a Slurm or other batch site the "HPC cluster"
 * — the scheduler's name is the answer to a question the user did not ask.
 * Another condor pool shows its own name: calling it Studio's would claim
 * something the project's catalog, not Studio, defines (#514). A site whose
 * kind is unknown but came from a hosted catalog is a batch cluster (that is
 * all hosted catalogs describe). When two sites would share a label the site
 * name is appended, so the choice is never ambiguous.
 */
export function siteChoices(
  sites: ReadonlyArray<{ name: string; style: string | null; source: string }> | undefined
): ReadonlyArray<SiteChoice> {
  if (!sites || sites.length === 0) return EXECUTION_SITES;
  const base = sites.map((s): SiteChoice => {
    const known = EXECUTION_SITES.find((e) => e.value === s.name);
    if (s.name === "local" && known) return known;
    const batch =
      s.style === "slurm" ||
      s.style === "batch" ||
      (s.style === null && s.source === "hosted");
    if (batch) return { value: s.name, ...BATCH_CLUSTER };
    if (known) return known;
    if (s.style === "condor") {
      return { value: s.name, label: s.name, hint: OTHER_POOL_HINT };
    }
    return {
      value: s.name,
      label: s.name,
      hint: "A site from this project's site catalog.",
    };
  });
  const counts = new Map<string, number>();
  for (const c of base) counts.set(c.label, (counts.get(c.label) ?? 0) + 1);
  return base.map((c) =>
    (counts.get(c.label) ?? 0) > 1 && c.label !== c.value
      ? { ...c, label: `${c.label} (${c.value})` }
      : c
  );
}

/**
 * Status labels.
 *
 * Most Pegasus statuses are already plain English and are passed through
 * unchanged — renaming "Running" buys nothing and costs recognizability. Only
 * four are translated, and each for a specific reason:
 *
 * - `held` → "Stuck": "held" reads as intentional ("someone is holding it for
 *   me") when it means the opposite. This is the single most misread status in
 *   the app. Phase 5 requires it always be shown with an action.
 * - `idle` / `queued` → "Waiting": "idle" implies the system is doing nothing
 *   wrong, when the user wants to know they are waiting for capacity.
 * - `submitted` → "Starting": "submitted" describes what the *user* did, not
 *   what the run is doing.
 * - `generated` / `planned` → "Ready to run": these name Pegasus pipeline
 *   phases; the user only cares that the next step is available.
 */
const STATUS_LABELS: Readonly<Record<string, string>> = {
  held: "Stuck",
  idle: "Waiting",
  queued: "Waiting",
  submitted: "Starting",
  generated: "Ready to run",
  planned: "Ready to run",
  planning: "Preparing",
};

/**
 * Human label for a run/step status. Unknown or already-plain statuses are
 * title-cased and returned as-is, so a new backend status degrades to
 * something readable instead of rendering blank.
 */
export function statusLabel(status: string): string {
  const key = status.trim().toLowerCase();
  if (!key) return "Unknown";
  const mapped = STATUS_LABELS[key];
  if (mapped) return mapped;
  return key.charAt(0).toUpperCase() + key.slice(1);
}

/**
 * True when a status needs the user to do something. Phase 5 requires that
 * anything answering true here is rendered alongside a real action (Retry,
 * Explain this failure, or explicit "contact your facilitator" copy) — a red
 * badge with no exit is worse than no badge.
 */
export function statusNeedsAttention(status: string): boolean {
  const key = status.trim().toLowerCase();
  return key === "held" || key === "failed";
}

/** One glossary row, as rendered by the Phase 6 "?" tooltips. */
export interface GlossaryEntry {
  readonly term: string;
  readonly definition: string;
  /** The Pegasus/HTCondor word, so a user can still search the real docs. */
  readonly alsoKnownAs?: string;
}

/**
 * Statuses aren't in TERMS (they're values, not concepts), but they are what a
 * confused user hovers first, so the glossary carries them too.
 */
const STATUS_GLOSSARY: readonly GlossaryEntry[] = [
  {
    term: "Stuck",
    definition:
      "The cluster accepted your step but stopped trying to run it, usually because something it needs is missing or unavailable. It will not proceed on its own.",
    alsoKnownAs: "held",
  },
  {
    term: "Waiting",
    definition:
      "Your step is queued and will start once a machine is free. Nothing is wrong.",
    alsoKnownAs: "idle / queued",
  },
];

function buildGlossary(): readonly GlossaryEntry[] {
  const entries: GlossaryEntry[] = [];
  // `TERMS` is `as const`, so Object.values() yields the literal member types
  // rather than `Term`; widen once here so the null-check narrows normally.
  for (const term of Object.values(TERMS) as readonly Term[]) {
    if (term.ui === null) continue; // hidden concept — nothing to define
    entries.push({
      term: term.ui,
      definition: term.definition,
      alsoKnownAs: term.pegasus,
    });
  }
  entries.push(...STATUS_GLOSSARY);
  return entries.sort((a, b) => a.term.localeCompare(b.term));
}

/**
 * Glossary entries for the Phase 6 "?" tooltips: every term that survives into
 * the UI, plus the Pegasus words users will still meet in logs, Advanced
 * views, and Chat. Sorted for stable rendering.
 */
export const GLOSSARY: readonly GlossaryEntry[] = buildGlossary();
