# Designing Studio for Non-Computer-Scientists

**Status:** proposed — tracking issue: [#241](https://github.com/pegasus-isi/studio-webapp/issues/241)
**Date:** 2026-08-26 (rev. 2 — adds data ingress, Chat vocabulary sync,
baseline telemetry, run-flow orchestration/failure spec, rollout strategy)
**Scope:** `studio-web/` UI copy, information architecture, and run flow;
additive `studio-api/` endpoints (typed params, upload, run-flow orchestration,
run outputs); a vocabulary addition to the knowledge overlay so Chat speaks the
same language. No changes to the Pegasus/HTCondor execution model.

## Problem

Studio's UI is faithful to Pegasus's mental model — generate, build, plan,
submit, DAG, jobs, sites, held state — which makes it legible to workflow
engineers and opaque to the domain scientists it is meant to serve. A
researcher who wants "run this analysis on my data and show me the result"
currently has to learn Pegasus vocabulary, argparse flags, and HTCondor queue
semantics before the first successful run.

## Review findings (against the code as of 2026-08-26)

1. **The primary flow exposes Pegasus's phases, not the user's task.**
   `workflow-project-detail.tsx` presents *Generate Workflow*, *Build Image*,
   *Plan*, *Run* as four sibling buttons with no ordering, no gating, and no
   explanation of the Plan/Run distinction. A non-specialist cannot tell which
   to press first.
2. **No onboarding.** No tour, first-run hint, or getting-started state exists
   anywhere in `studio-web/`. The first screen is a grid of ~10 cards.
3. **Untranslated jargon throughout.** `condorpool`, `output_site`,
   "Apptainer def", "generator", "yml" (workflow list badges), "PegasusLite",
   `monitord.pid` (an output hint tells users to delete it), "Held jobs",
   "slots", "OSPool (OSG)", "CPUs requested (idle jobs)", "Querying HTCondor…".
   The Resources page is effectively an HTCondor admin console.
4. **The run form is raw argparse.** `ParamField` labels fields by stripping
   dashes from the CLI flag, renders every non-choice value as a monospace text
   box, and uses the argparse help string as the caption. No date pickers, file
   pickers, numeric types, or validation — although `routers/files.py` already
   provides the file-listing API a picker needs.
5. **Failure output is a terminal.** Green-on-black `<pre>` of
   `pegasus-plan`/`pegasus-run` stdout; "Run Analyzer" appends raw
   `pegasus-analyzer` text. `outputHint()` is the right instinct but speaks
   sysadmin ("remove the run's monitord.pid file and retry").
6. **No results view.** The largest gap on the output side.
   `routers/workflows.py` has no outputs endpoint and the UI never shows
   produced files. The entire reason a scientist runs an analysis is the
   output; today they must leave for JupyterLab and hunt the filesystem.
7. **No data ingress.** The largest gap on the input side. A scientist's data
   is on their laptop; the only routes into the workspace are JupyterLab's
   upload or scp — exactly the escape hatches this plan demotes. Without an
   upload path the Runner persona cannot complete their core task at all.
8. **Progress is machine-shaped.** A job table (`job_id / status / duration /
   exit`); the existing remaining-time estimate (`useWorkflowEstimate`) is
   buried rather than the headline.
9. **Chat is presented as a CLI.** Empty state and dashboard cards teach
   `/scaffold`, `/debug`, `/review` — a programmer idiom — instead of
   plain-language starter prompts.
10. **Escape hatches have prime real estate.** Terminal appears in both
    sidebar Quick Access and the status bar; Settings leads with LLM provider
    base URLs and registry tokens across five tabs.

**Worth keeping:** the dashboard already reorders cards for an empty workspace;
`SKILL_META` already maps skills to task language ("Create a new workflow");
the delete-run confirmation copy is in exactly the right register; the
Marketplace "Add to workspace" one-click flow is the best non-specialist moment
in the app.

## Personas

Design for two concrete users, not "non-computer-scientist" in the abstract:

- **P1 — Runner.** A domain scientist who has an existing workflow (their
  lab's, or a Marketplace example) and wants to run it on their own inputs and
  see the outputs. Never opens a terminal. Success = first result file viewed.
- **P2 — Author.** A researcher who can describe a pipeline in prose ("align
  the reads, then call variants, then plot") and uses Chat to build it. Some
  scripting literacy, no Pegasus knowledge. Chat is P2's primary tool; it is a
  panel available on every page rather than a nav destination, and stays that
  way.

## Rollout strategy (cross-cutting, decided up front)

A merge to `main` deploys to live user pods on next restart, so this redesign
must not land as a big-bang flip:

- Phases 1, 2, and 7 (run flow, ingress, IA/vocabulary flip) ship behind a
  per-user setting (default off initially, flipped to default on once Phase 8
  validates), with the current view reachable throughout the transition.
- Purely additive phases (outputs view, failure cards, onboarding) can ship
  unflagged.
- The Advanced disclosure state persists per user in the settings DB —
  Pegasus-literate users must not have to reopen it on every visit.

## Phase 0 — Vocabulary, personas, and baseline (blocks the copy-touching phases)

**Vocabulary.** A single term-mapping table becomes the source of all UI copy.
Pegasus terms survive only in Advanced disclosures and technical-detail panes.

| Pegasus / HTCondor | UI |
|---|---|
| workflow project | Analysis |
| generate / plan / submit | (hidden — folded into **Run**) |
| job | Step |
| DAG | Pipeline diagram |
| held | Stuck (always paired with an action — see Phase 5) |
| site / `condorpool` / `output_site` | Where it runs → *This machine* / *Shared cluster* |
| Apptainer def / `.sif` | Software environment |
| pool / slots / machines | Computing capacity |
| pegasus-analyzer output | What went wrong |
| run dir | (hidden; surfaced only in technical details) |

Deliverables:

- `studio-web/lib/vocabulary.ts` (or equivalent constants module) so copy is
  changed in one place, plus a glossary used by "?" tooltips.
- **Validate the terms before building on them**: a one-hour terminology check
  (card sort / preference test) with 3–4 target users. "Analysis" competes
  with "pipeline" and "experiment" depending on field; losing that bet in
  Phase 8 would invalidate every phase in between.
- **Chat speaks the same language.** Chat is driven by the knowledge layer
  (`pegasus-isi/claude-plugin-marketplace`, composed with
  `knowledge/overlay/STUDIO_ENV.md`). Without a sync step, the UI says "Step 4
  is stuck" while Chat says "job 4 is held in the DAG" — two vocabularies is
  worse for a novice than one hard one. Add the mapping table to the Studio
  overlay so Chat translates in the same direction (teaching Pegasus terms on
  request, leading with plain language), and audit backend-generated copy
  (notifications, output hints) against the same table.
- **Baseline telemetry.** Instrument **time from first login to first
  successful run** *now*, before anything changes. This is the success metric
  for the whole effort (Phase 8); built at the end it would measure only the
  redesigned app, with no baseline to claim improvement against.

## Phase 1 — One Run flow (biggest single win)

- Replace the four sibling verbs with one primary **Run** button plus a
  "Set up your run" form.
- **Server-side orchestration, not client-side sequencing.** A new endpoint
  (one POST starting a run-flow, one SSE/event stream reporting it) sequences
  generate → build (only if the image is missing/stale) → plan → submit.
  Client-side chaining of the existing endpoints would strand a half-prepared
  project when the tab closes, and the stages are a mix of synchronous calls
  and an SSE build stream that need one coherent progress channel. The flow
  must be resumable: reopening the page reattaches to an in-flight run-flow.
- Live step indicator: *Preparing… → Checking software… → Sending to the
  cluster… → Running*.
- **Pre-run failures are Phase 1's job, not Phase 5's.** The unified flow
  fails most often *before* a run exists — generate throws a Python error,
  build fails on a bad def file, plan rejects a catalog — and there is no
  pegasus-analyzer for those. Each stage failure gets its own card: which
  stage, plain-language cause, one suggested action, technical log collapsed.
  (The current code already special-cases build logs because `outputHint()`
  gives wrong advice on them; the per-stage design removes that class of bug.)
- Keep Generate/Build/Plan/Run as individual actions under an **Advanced**
  disclosure for Pegasus-literate users (state persisted per user).
- Disable Run until required parameters validate, so illegal orderings become
  unreachable instead of being error messages.

## Phase 2 — Data ingress

The Runner persona's task starts with "on my own inputs," and today there is
no way to provide them without JupyterLab or scp. Requirements:

- Drag-and-drop / browse upload, available both from a simple **My data** area
  and inline inside the run form (upload directly into a file parameter).
- Backed by a new upload endpoint (multipart, size-limited, streaming to the
  workspace; `routers/files.py` gains a sibling, it already handles listing
  and paths safely).
- Show where the file landed in plain terms ("added to your workspace") and
  make uploaded files immediately selectable in Phase 3's file pickers.
- Large-data honesty: above a size threshold, explain the alternatives instead
  of failing silently (this is where a link to JupyterLab's uploader or a
  staged-data story belongs, stated as such).

## Phase 3 — Humanize the run form

- Extend `GET /projects/{id}/params` to infer types from argparse (`type=`,
  `choices`, plus dest heuristics: `*_dir`, `*_file`, `*_date`, `*_count`).
  **Inference is best-effort and must degrade gracefully**: heuristics will
  misfire (a "file" that is actually a URL, a nonstandard date format), so
  every typed widget falls back to the current free-text input and a wrong
  guess can never block submission.
- Render: date pickers, file/folder pickers backed by `routers/files.py`
  (including Phase 2 uploads), numeric fields with units, checkboxes with
  sentence labels.
- Label from the `help` text first; show the raw flag only in Advanced.
- Replace `site`/`output_site` free-text with plain-language radio options.
- Add a pre-submit summary: "here's what will happen" (inputs, where it runs,
  rough scale).

## Phase 4 — Progress and results in the scientist's terms

- Headline status: *"Running — step 4 of 12, about 18 minutes left"*,
  promoting the existing estimate — **confidence-gated**. The estimate is a
  historical-mean heuristic; on a first run (exactly this plan's target user)
  there is no history, and a non-specialist will trust the number literally.
  With no basis, say "this can take a while — we'll notify you when it's
  done" instead of inventing precision.
- Steps as a readable list; the DAG becomes an optional "Diagram" tab.
- **Build the missing Outputs view**:
  - Discovery: resolve the run's output/storage directory from the run's
    metadata (braindump / planned output site) and list what's there;
    fall back to statting the storage dir if catalog data is unavailable.
  - UI: inline preview for images, CSV, and text; download; "Open in
    JupyterLab" at that path for anything richer.
  - Scale honesty: paginate beyond ~100 files, no inline preview above a size
    threshold (offer download instead), never load a multi-GB file into the
    browser.
- Wire notifications to "Your analysis finished" → deep link straight to the
  results, not the job table.
- This phase's API work has no dependency on Phase 0 vocabulary and can start
  on day one.

## Phase 5 — Failures a non-programmer can act on

- Never lead with raw stdout. Failure card shows: which step failed, a
  plain-language cause, one recommended action button, and *Show technical
  details* collapsed. (Pre-run stage failures are covered in Phase 1; this
  phase covers failures of the running workflow.)
- **A state that demands attention must come with an action.** "Stuck" is only
  shown alongside something the user can do: Retry, Explain this failure, or —
  when nothing self-service exists — explicit "contact your facilitator" copy.
  Never a red badge with no exit.
- Generalize `outputHint()` into a rules table: pattern → cause → suggested
  fix → optional action.
- Add **Explain this failure**: pipes analyzer output + logs into Chat with a
  prompt to explain non-technically and propose a fix. The `/debug` skill
  already does this work; this turns it into a button.

## Phase 6 — Onboarding and in-context help

- One-time three-step welcome (*pick an example → give it your inputs → run
  it*), dismissal persisted in the settings DB.
- Replace teach-by-CLI empty states (the workflow list currently suggests
  running `pegasus-plan --submit` in a terminal) with a "Browse examples"
  button.
- Glossary-backed "?" tooltips on every term that survives from Phase 0.
- Replace the chat empty state's slash-command hints with plain-language
  starter prompts ("Build me a workflow that…"); slashes keep working.

## Phase 7 — Progressive disclosure of infrastructure

- Default nav: *Home, My Analyses, Examples, Notebooks*. Collapsed Advanced
  group: *Resources, Terminal, Settings*. Chat remains an always-available
  panel (its current, stronger placement), not a nav route — this is why it
  does not appear in the default nav list.
- Resources page leads with one sentence answering "is there capacity for my
  run right now?"; the inventory/demand tables move under Details.
- Settings defaults to Appearance/Notifications; providers, tools, and
  registry credentials move under Advanced.
- Ships behind the rollout flag (see Rollout strategy).

## Phase 8 — Validate

- Five-participant task-based test with actual domain scientists:
  *"run the example air-quality workflow on last month's data and show me the
  plot"* — no coaching. Include a data-upload step so Phase 2 is exercised.
- Compare **time from first login to first successful run** against the
  Phase 0 baseline; this comparison is the effort's success criterion and the
  gate for flipping the rollout flag to default-on.

## Cross-cutting requirements

- Accessibility, with testable acceptance criteria: every form input has a
  programmatic label (`htmlFor`/`aria-label`); every action is reachable and
  operable by keyboard alone (verified by tabbing through each page); log and
  status panes meet WCAG AA contrast (the current green-on-black `<pre>` does
  not in light mode); no meaning carried by emoji or color alone.
- All copy changes flow through the Phase 0 vocabulary module, on both the
  frontend and backend-generated strings (notifications, hints).

## Sequencing

Phase 0 first: cheap, and every copy-touching phase references it — but its
telemetry deliverable is the true blocker (no baseline, no verdict). Phase 4's
outputs API can start immediately in parallel. Phases 1 and 4 deliver most of
the perceived change; 2 and 3 complete the Runner's input side and depend on
1's form; 5 extends 1's failure pattern to runs; 6 and 7 are polish that pays
off only once the core flow is coherent; 8 closes the loop and gates the
default-on flip.
