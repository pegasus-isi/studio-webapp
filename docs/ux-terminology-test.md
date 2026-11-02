# Terminology validation protocol

> **Status: written, not yet run** (re-checked 2026-11-02). The vocabulary it
> was meant to gate shipped default-on without it (#269; see
> [`UX-NON-SPECIALIST-DESIGN.md`](UX-NON-SPECIALIST-DESIGN.md), Rollout
> strategy). The UI wording has moved on since: the execution-site choice is
> now "Where Workflow Runs" with "Studio's Compute Resources" and "Bring Your
> Own Compute" (#619, #627, #631). Refresh Part 1 and Part 2 against
> `studio-web/lib/vocabulary.ts` and the current screens before running it.

**Purpose:** decide the words in `studio-web/lib/vocabulary.ts` with evidence
rather than taste, before Phases 1–7 build on them.
**Effort:** ~45 minutes per participant, 3–4 participants, one session each.
**Blocking:** Phase 1 should not ship a flipped vocabulary until this runs.

Why this exists as its own document: "validate the terms with users" written
inside a plan is an aspiration, and aspirations get skipped when a phase is
late. This is the script, the materials, and the decision rule, so running it
is a scheduling problem rather than a design problem.

## Who to recruit

Three or four people who match the **Runner** persona:

- Uses computational analysis in their research (any domain).
- Has **not** used Pegasus, HTCondor, Nextflow, or Snakemake. This is the
  hard requirement. Someone who already knows what a DAG is cannot tell you
  whether "DAG" is confusing, and workflow-literate colleagues are exactly who
  you will be tempted to grab.
- Not a member of the Studio or Pegasus teams.

Three is enough. Terminology problems are not subtle, and the fourth
participant is insurance against a bad recruit rather than statistical power.

## Materials

- The candidate terms below, one per index card or slide.
- A screen-share of the current Studio UI (do not pre-clean it).
- Recording, with consent, or a second person taking notes.

## Session script

Read the framing aloud so every participant hears the same thing:

> I am testing the words this software uses, not you. There are no wrong
> answers, and if something makes no sense that is the finding I need. Please
> think out loud.

### Part 1 — Comprehension, before any priming (15 min)

Show each Pegasus term **in the sentence the UI actually uses today**, and ask:
"in your own words, what do you think this means?" Do not offer options yet;
the point is to catch confident misreadings, which are more dangerous than
blank incomprehension.

| Shown as | Listening for |
|---|---|
| "3 jobs held" | Do they think held is *good* — reserved, protected, saved for them? This is the specific misread the redesign exists to fix. |
| "Visualize DAG" | Any guess at all, or a shrug? |
| "Plan" next to "Run" | Do they expect Plan to be a preview, a schedule, a save, or nothing? |
| "Site: condorpool" | Do they read "site" as a website or a physical location? |
| "Apptainer def" | Do they connect it to software at all? |
| "output_site: local" | Do they know where their results will end up? |

Record verbatim quotes, not summaries. "I assumed it was saving my spot" is
usable evidence; "confused about held" is not.

### Part 2 — Preference, between candidates (15 min)

For each concept, show the candidates **in randomized order** and ask which
they would expect to click for a given goal. Phrase the goal in their world,
never in ours.

| Concept | Goal to state | Candidates |
|---|---|---|
| workflow project | "You want to run the analysis your collaborator sent you." | Analysis · Pipeline · Experiment · Project · Workflow |
| job | "One piece of that analysis is taking a long time." | Step · Task · Job · Stage |
| DAG | "You want to see the order things happen in." | Pipeline diagram · Steps · Flowchart · Diagram |
| held | "Something has gone wrong and it isn't progressing." | Stuck · Blocked · Paused · Needs attention · Held |
| execution site | "You need to choose where the work happens." | Where it runs · Compute location · Run on · Destination |
| `.sif` / Apptainer | "The analysis needs specific software versions." | Software environment · Container · Toolbox · Software package |

Two questions per concept: **which would you click**, and **what would you
expect to happen next**. The second catches a term that wins on familiarity but
sets a wrong expectation.

### Part 3 — The real task (15 min)

Screen-share the current UI. Ask them to run the example air-quality workflow
and show you the result. Do not help. Stop at 15 minutes regardless.

Record: where they first hesitate, what they click that does not do what they
expected, the point at which they would have given up outside a study, and
whether they ever find their output files. This part also produces the Phase 8
comparison anecdotes, so keep the recordings.

## Decision rule

Fix the rule before collecting data, so a favourite term cannot be rescued
after the fact:

1. **A candidate wins outright** if 3 of 4 (or 3 of 3) pick it *and* their
   "what happens next" answers match what the UI will do. Adopt it and update
   `vocabulary.ts`.
2. **Split vote, no misexpectation:** keep the current entry in
   `vocabulary.ts`. A tie means the choice does not matter much, and churn has
   its own cost.
3. **Any candidate that produces a confident wrong expectation is
   disqualified**, even if it wins the vote. This is the "held" failure mode:
   popular, comprehensible, and wrong.
4. **If the Pegasus term wins**, keep the Pegasus term. That is a real result,
   not a failed study, and it saves a translation layer.

Record the outcome per concept in a table appended to this file, including the
losers. A later reader asking "why is it called Analysis?" should find the
answer here rather than re-litigating it.

## What this protocol does not cover

Layout, iconography, and flow. Those are Phase 8's task-based test, which needs
something built to test against. Keep the two separate: asking a participant to
judge words and screens in the same breath gets you opinions about neither.

## Results

_Not yet run. Append one row per concept, plus the date and participant count._

| Concept | Candidates tested | Winner | Rule applied | Notes |
|---|---|---|---|---|
| | | | | |
