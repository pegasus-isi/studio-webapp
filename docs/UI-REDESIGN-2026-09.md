# Studio Interface Redesign — September 2026

**Status:** revision 2 — the foundation is implemented in #314; the chat half is not
**Date:** 2026-09-01 (rev. 2 — reframed around chat as the primary driver)
**Issue:** [#314](https://github.com/pegasus-isi/studio-webapp/issues/314)
**Mocks:** `docs/ui-redesign-2026-09-mocks.html` (open in a browser; it has a
light and a dark rendering of every screen)
**Inputs:** five screen captures of a design prototype (2026-09-01) and the
project meeting recap (2026-08-28). Neither is committed — the captures are
screen-shares of someone else's working tree and the recap is raw meeting
notes; both are attached to [#314](https://github.com/pegasus-isi/studio-webapp/issues/314)
if you need to check a finding against them.
**Scope:** `studio-web/` information architecture, the four screens the captures
cover (Home, Workflows, Catalog, Chat composer), plus the small number of
`studio-api/` data-quality fixes those screens depend on. No changes to the
Pegasus/HTCondor execution model.
**Relationship to [`UX-NON-SPECIALIST-DESIGN.md`](UX-NON-SPECIALIST-DESIGN.md):**
that document made Studio usable without Pegasus literacy and shipped in full
(#241, seven phases). This one is narrower and later. It is about the things
that stayed broken, or broke again, once real workflows and the real PegasusHub
registry were loaded into it.

---

## 0. Provenance — read this before acting on the screenshots

The captures are **not** current `main`. They are a
prototype, screen-shared over Zoom by someone else on the team.

Evidence:

| Observation | Where |
|---|---|
| The chrome in the captures (top app bar, centered `1 Ask AI → 2 Run Jobs → 3 Notebooks → 4 Resources` stepper, icon-only left rail) exists nowhere in this repo | `git log --all -S"Ask AI" -- studio-web/` returns nothing; `app/(studio)/layout.tsx` renders sidebar + main + panels, no header |
| Workflow paths read `/Users/haezasafri/wor…` | capture `1.29.49` |
| The Catalog page is headed "Catalog"; ours is headed "Marketplace" | capture `1.32.05` vs `app/(studio)/marketplace/page.tsx:37` |
| Project rows show "0 runs"; ours suppresses a zero count | capture `1.29.49` vs `workflow-list.tsx:92` |
| Parameter labels are raw lowercase flags (`execution site`, `src path`, `s`); ours humanize from the author's help text | capture `1.29.49` vs `studio-api/introspect_argparse.py:109` (`_label_for`, shipped in #259) |
| The left pane has `All / Running / Success / Failed` tabs and a search box | capture `1.29.49`; neither exists in `workflow-list.tsx` |

So the captures are a mix of two different things, and the redesign has to keep
them apart:

- **Prototype-only** problems. Not our bugs. Some of them are still worth
  recording, because they are the ones a second designer walked into
  independently, which makes them likely to be real perception problems rather
  than implementation slips.
- **Shared** problems, where the prototype is showing a defect that also
  reproduces in `main`. These are the ones with teeth. Each is marked
  **Reproduces in `main`** below, with the file and line that causes it.

Every finding below carries that tag. Do not open an issue against a
prototype-only finding without first reproducing it here.

---

## 0b. Revision 2 — what the prototype was actually arguing

Revision 1 of this document read the prototype as a set of layout choices and
graded them one by one. That was the wrong frame. The prototype is arguing a
product thesis, and the three choices revision 1 pushed back on are all
consequences of it:

> **Everything should be drivable from the chat.**

Given that, the giant chat landing is not a hero with a composer stapled on; it
is the product surface. The DAG taking the analyses screen is not a matter of
taste; it is what is left once the assistant, not the form, is how you
configure a run. And the icon rail is not a memory test for its own sake; it is
the density an instrument panel wants.

Three positions from revision 1 are withdrawn:

| Rev 1 said | Rev 2 says |
|---|---|
| Home should lead with a state dashboard, chat below (R5) | Home **is** the chat. State arrives as the assistant's opening turn — same information, delivered as things to act on rather than tiles to read. |
| Keep the labelled sidebar; an icon-only rail is a memory test (R7, F36) | Adopt the rail. The amendment is a 9px label under each icon, which costs 22px and removes the guessing. |
| Keep Studio's sky palette | Adopt the prototype's cornflower system, with the one measured defect fixed (see R9). |

What does **not** change: the defects in §1 marked *Reproduces in `main`* are
orthogonal to the thesis. A 170-node DAG is unreadable whether or not the chat
drives it — arguably more so once the canvas is the main event.

### The part that decides whether the thesis holds

Chat-driven only works if the assistant's replies are **operable**. If a reply
is prose that ends with "now open Analyses and press Run", chat is a help desk
with extra steps rather than a way to drive anything. Every reply that produced
or changed something has to return that thing with its real controls attached:
a built workflow with Run / Change settings / Show code, a rescue DAG with
Watch / Undo, a failure with Retry.

That is mostly backend surface area — assistant tools that return typed objects
and a client renderer per object type — and it is deliberately **not** in #314.
Until it exists, "chat drives everything" is a claim about layout. It needs its
own issue, and it is the one that matters most.

---

## 1. Findings

### Home / welcome (captures `1.24.54`, `1.25.09`)

**F1 — Every example prompt is truncated mid-sentence.** *Prototype-only, but
the pattern is ours to avoid.* All six "TRY AN EXAMPLE" cards clip: "I have 500
FASTQ files in ~/data/samples/. Build a…", "My workflow has been running for 2
hours but 4…", "Write a Pegasus wrapper for gatk HaplotypeCalle…". The card is
a button that sends the prompt. The user is being asked to send text they have
not been allowed to read. A truncated *label* is a nuisance; a truncated
*payload* is a trust problem.

**F2 — The examples all presume Pegasus fluency.** *Prototype-only.*
"Scatter-gather pipeline", "Convert Snakemake pipeline", "Review DAX for
scaling", "Container for PyTorch", "Wrap a GATK tool", "Jobs stuck in idle".
Six for six require the reader to already know what a DAX, a scatter-gather, an
Apptainer image, or an idle job is. This is the exact failure #241 Phase 0 was
written to fix, reintroduced on the first screen a new user sees. ("DAX" is
also dead Pegasus vocabulary — v5 calls it the abstract workflow.)

**F3 — "Jobs stuck in idle" is offered on an empty workspace.** *Prototype-only.*
You cannot debug a stalled run when you have no runs. The example set is static;
it does not consult what the user actually has. `main`'s dashboard does better
already — `dashboard-view.tsx:155` reorders cards on `hasWorkflows` — so this is
a capability the prototype dropped, not one we lack.

**F4 — The home screen carries no state.** *Prototype-only.* Identical on day 1
and day 100: no recent runs, no active workflow, no data, no pool status.
`main` shows "At a glance" and "Recent runs" (`dashboard-view.tsx:266-343`).
Worth stating explicitly so the redesign does not lose it.

**F5 — ~25% of the viewport is dead space.** *Prototype-only.* Between the last
example card (y≈420) and the composer (y≈580) in a 679px-tall capture. The
composer is pinned to the bottom and the content is top-aligned, with nothing
bridging them.

**F6 — Four "quick actions" at equal weight are four different kinds of thing.**
*Prototype-only.* "New workflow" creates an object, "Open notebook" launches
another application, "Browse catalog" is a navigation, "Terminal" is a raw
shell. Giving a domain scientist a shell as one of four equally-sized entry
points on the onboarding screen is a statement about who the product is for.

### Chat composer and mode picker (captures `1.24.54`, `1.28.56`)

**F7 — The placeholder frames the assistant as a documentation search box.**
*Shared in spirit.* "Ask about Pegasus workflows…" tells the user this thing
answers questions about Pegasus. It builds, edits, containerizes, submits and
diagnoses workflows. This is the same complaint the meeting recorded as
"improving chat context awareness" — users infer capability from the prompt.

**F8 — Nothing in the composer says what the chat can see.** *Prototype-only;
`main` has `context-chip.tsx`.* The single most requested change in
the meeting recap is that chat be aware of the specific workflow you are
looking at. The prototype's composer has no context affordance at all. Ours does, and
the redesign should make it louder rather than quieter.

**F9 — The model is named "default".** *Reproduces in `main`.*
`chat-input.tsx:319` renders "Provider default" when no model is pinned, and
the gateway config supplies the actual identity. A user reporting "the model
gave a bad answer" cannot tell us which model. This has already cost debugging
time.

**F10 — One menu, three incompatible kinds of choice.** *Shared.* The picker
stacks `MODE` (a state), `AGENTS` (a persona: Pegasus Expert, Data Scientist,
HPC Specialist) and `SKILLS` (an action: `/pegasus-scaffold`) as siblings in one
scrolling list. Picking "Data Scientist" and picking "/pegasus-scaffold" are not
the same operation — one persists, one fires once — but they are presented
identically and are mutually exclusive by construction of the menu.

**F11 — The currently-selected item is the one row that is clipped.**
*Prototype-only.* "General" is cut in half by the top of the scroll container.

**F12 — Every description in the menu truncates mid-word.** *Prototype-only.*
"Deep knowledge of Pegasus WMS interna…", "HTCondor pool tuning, resource
requests…", "Create a new Pegasus workflow with…". Three for three. The popover
is roughly 180px tall with an internal scrollbar on a page with ~400px of unused
vertical space.

**F13 — Two unlabeled adjacent dropdowns.** *Shared.* `General ⌄` and
`LLM Gateway · default ⌄` sit side by side. One selects who answers, one selects
what answers. Neither is labeled.

### Workflow project detail (capture `1.29.49`)

**F14 — A 170-node DAG renders as one illegible horizontal row.**
**Reproduces in `main`, with arithmetic.** `dag-viewer.tsx` lays out with
`rankdir: "TB"`, `NODE_W = 190`, `nodesep = 30`, then renders with `fitView` and
`minZoom={0.1}`. A Pegasus scatter of 167 sibling jobs is a single dagre rank
167 × 220 ≈ **36,700px wide**. Fitting that into a ~1,200px pane needs zoom
≈ 0.033; `minZoom` clamps at 0.1. `fitView` therefore *cannot* fit, and the
graph silently overflows. At zoom 0.1 each node is 19px × 5px — the labels in
the capture are unreadable for exactly this reason. There is no node cap
anywhere in the client or in `routers/workflows.py`. **This is the single worst
thing in the captures and it is our bug.**

**F15 — The DAG pane is ~70% empty while the graph is unreadable.** *Follows
from F14.* The vertical extent is three ranks; the horizontal extent is off
screen in both directions. The banner ("large graph — scroll / drag to explore,
use ⛶ to fit all") asks the user to compensate manually for a layout that has
already given up, and points at a fit control that cannot fit.

**F16 — Generate / Plan / Run as three peer buttons.** *Prototype-only —
regression against #241 Phase 1.* Three verbs, no ordering, no gating, no
indication that Run implies the other two. #241 Finding 1 identified precisely
this and Phase 1 replaced it with one Run flow (#247/#249). The prototype put it
back.

**F17 — A red ✗ with no explanation and no consequence.** *Shared.* The FILES
list shows `workflow_generator.py ✓`, `workflow.yml ✓`, `Apptainer def ✗`. Red
means error; the user is not told what is wrong, what to do, or whether it
blocks anything. Run stays enabled beside it. Either the missing definition
matters, in which case say so and block, or it does not, in which case it is not
red.

**F18 — A required parameter labeled `s *`.** **Reproduces in `main` for
short-only flags.** `introspect_argparse.py:126` picks the longest option string
and strips dashes, so a generator that declares only `-s` with help text longer
than 48 characters yields the label `s`. Every other field on that form has a
readable name; the one field you *must* fill in is a single letter, and its help
text is cut off by the pane.

**F19 — Required fields sit below ten optional ones, off screen.** *Shared.*
`s *` is at the bottom of the scroll, after `submit`, `execution site`,
`dataset`, `datafile`, `bash jobs`, `individuals jobs`, `src path`, `decaf`,
`pmc`, `dir name`, `sites catalog`. `_is_advanced()` already refuses to demote
required parameters (`introspect_argparse.py:131`), but nothing *promotes* them,
so required-before-optional is inverted in practice.

**F20 — The form is grouped by which CLI consumes the flag.** *Shared.* The
section headings are "Generate" and "Plan / Submit" — that is Pegasus's
pipeline, not the user's decision structure. A scientist wants "my data",
"analysis settings", "where it runs". They do not care which binary reads which
flag.

**F21 — Two controls for where the run executes.** *Fixed in `main` (#257),
present in the prototype.* An `execution site = local` under Generate and an
`s * = condorpool` under Plan/Submit, on the same form, with nothing reconciling
them. Recording it because the prototype demonstrates how easily it comes back.

**F22 — Help text outweighs the fields.** *Shared.* The `individuals jobs`
caption is five lines of low-contrast gray, longer than the label, value and
every neighbouring field combined. It is the argparse help string rendered
verbatim.

**F23 — Filter tabs that cannot filter the list beneath them.**
*Prototype-only.* `All / Running / Success / Failed` are run states; the list
below is PROJECTS, each showing "0 runs". No selection other than "All" can
return anything.

**F24 — "0 runs" three times, "generated"/"draft" as the only other signal.**
*Prototype-only for the zero count.* Nothing tells the user what these projects
do, when they were touched, or how big they are. "generated" and "draft" are
lifecycle states of our scaffolding machinery, not facts about the science.

**F25 — A permanent-looking "Scanning…".** *Shared.* `workflows/page.tsx:47`
shows "Scanning..." while `isLoading`, then a count. In the capture it is still
showing while three projects are already listed below it, so header and list
disagree. There is no timeout and no error state for a scan that never
finishes — relevant now that scan roots are user-configurable (3ae5d02) and can
point at a slow or dead mount.

**F26 — The job inspector shows one opaque identifier.** *Prototype-only in this
form; the data poverty is shared.* A full-width bottom drawer, ~10% of the
viewport, containing `mutation_overlap`, `unsubmitted`, and `JOB ID
ID0000035`. Nothing about what the step does, its inputs, outputs, command or
container. `main`'s Steps table (`workflow-project-detail.tsx:753-790`) is
better but still `job_id / status / duration / exit` — machine-shaped.

### Catalog / Marketplace (capture `1.32.05`)

**F27 — Thirteen filter chips for twenty workflows, most of them duplicates.**
**Reproduces in `main`.** The chips read `Machine Learning 3`,
`Machine-Learning 1`, `Machine-Learning, Nlp 1`,
`Machine-Learning, Optuna, Pytorch-Cnn, Transfer-Learning 1`,
`Bioinformatics 2`, `Bioinformatics, Genomics 1`, `Training Workflow 1`,
`Training 1`, `Brain-Imaging, Freesurfer 1`. Two separate defects:
a comma-joined tag list is being treated as one category, and case/hyphen
variants of the same word are not folded. Cause:
`routers/marketplace.py:137` takes `entry.get("topic") or ""` raw from the
community-editable registry and `marketplace/page.tsx:28` builds the chip set
from a bare `Set` of those strings. Nine of thirteen filters return exactly one
result.

**F28 — Half the catalog has no description.** **Reproduces in `main`.**
`marketplace.py:136` defaults `description` to `""`. The prototype substitutes
"A ready-made Pegasus workflow." and prints it twelve times down the grid, which
is worse than blank — it looks like content and carries none. Ours renders
nothing, which at least does not lie, but the card is then just a slug.

**F29 — Card titles are repo slugs.** **Reproduces in `main`.**
`variant-calling-pegasus-workflow`, `compare-large-language-models-workflow`,
`montage-workflow-v3`, `lung-instance-segmentation-workflow`. From
`_id_from_repo()` (`marketplace.py:119`) when the registry omits `name`. The
word "workflow" then appears in the page title, the search placeholder, the
description sentence, and every single card name.

**F30 — Twenty identical full-width saturated CTAs.** *Shared.* "Add to
workspace" is the heaviest element on every card, so the grid reads as a wall of
blue bars and the titles — the only thing that differentiates the cards — are
visually subordinate to a button that says the same thing twenty times.

**F31 — "Add to workspace" is the only thing you can do.** *Shared.* No preview,
no README, no input requirements, no runtime or size estimate, no indication of
whether it is already in your workspace. The user is asked to clone a repo
sight-unseen based on a slug.

### Cross-cutting

**F32 — The same concept has four names.** *Shared.* The left nav says "My
analyses" (`sidebar.tsx:36`); the page it opens is headed "Workflows"
(`workflows/page.tsx:44`); its left column is headed "PROJECTS"; the nav item
for ready-made ones says "Examples" (`sidebar.tsx:40`) and the page it opens is
headed "Marketplace" (`marketplace/page.tsx:37`), which the prototype renamed
"Catalog". This is the vocabulary that `docs/ux-terminology-test.md` was written
to validate and which shipped unvalidated (#269).

**F33 — Eight status words across two unrelated badge systems.** *Shared.*
Project badges say `generated` / `draft`; the DAG legend says `unsubmitted` /
`pending` / `running` / `succeeded` / `failed` / `held`. Three of those
(`generated`, `draft`, `unsubmitted`) all mean "has not run yet", and they are
rendered in different shapes, in different places, with different colors, on the
same screen.

**F34 — One accent color does five jobs.** *Shared.* The same blue marks the
active nav item, the stepper's current step, every catalog CTA, hyperlinks, and
file status. Nothing is held back for "this is the action to take", so nothing
reads as the action to take.

**F35 — Empty states describe absence rather than offering an action.**
*Shared.* "Scanning…", "0 runs" ×3, "Select a run to see live execution data",
"No job data available." Each is a dead end.

**F36 — Icon-only navigation with nine unlabeled icons.** *Prototype-only.* The
rail carries grid, branch, bag, stack, book, chart, speech-bubble, terminal and
gear with no text at any width. `main`'s sidebar is labeled and collapsible
(`sidebar.tsx:149`), which is right. A permanently-collapsed rail is a memory
test, and it is the *primary* IA.

**F37 — The numbered stepper is a false promise.** *Prototype-only, and it
should not be adopted.* `1 Ask AI → 2 Run Jobs → 3 Notebooks → 4 Resources`
uses the visual grammar of a wizard: ordered, sequential, completable. None of
that is true. Resources is an infrastructure inventory, Notebooks is a separate
application, and nothing ever marks step 1 done. It also duplicates the left
rail — the same four destinations, in a different order, under different names
("Run Jobs" vs the rail's branch icon) — so the app has two navigation systems
that disagree, and the labeled one is in the least-scanned position on screen
while the unlabeled one is the real IA.

---

## 2. What the meeting asked for

From the project meeting recap (2026-08-28), mapped onto the findings above.

| Ask | Findings | Status |
|---|---|---|
| Chat should be context-aware of the specific workflow | F7, F8 | Partly built (`context-chip.tsx`); needs to be prominent and to actually scope retrieval |
| Workflow lifecycle management | F16, F17, F24, F33 | Open |
| Data upload: external URLs, and data-fetch as compute jobs inside the workflow | — | Open; Phase 2 (#255) covers laptop upload only |
| Disk quota per user | — | Open; needs a number from the platform side before UI |
| Configurable / discoverable workflow scan paths | F25 | **Shipped** (3ae5d02) |
| Discover and track workflows via the Pegasus MCP or REST API instead of duplicating metadata | F25, F26 | Open; today `workflow_scanner.py` globs `**/braindump.yml` |
| Unbiased user testing | F32 | Open; `docs/ux-terminology-test.md` still unrun |

---

## 3. Principles for the redesign

1. **The screen shows the user's state, not the product's features.** F4 and F35
   are the same failure at opposite ends.
2. **Never truncate a payload.** Truncating a label is a layout decision;
   truncating something the user is about to send or select (F1, F12) is not.
3. **One accent color, reserved for the next action.** Everything else earns
   attention through position and weight (F30, F34).
4. **Scale is a design case, not an edge case.** 170 nodes, 20 catalog entries
   with dirty tags, and a 15-field generated form are the *normal* inputs
   (F14, F19, F27).
5. **Group by the user's decision, not by our pipeline.** F20 and F10 are the
   same mistake in two places.
6. **Say which, not "default".** Any resolved identity — model, site, scan
   root — is named on screen (F9).
7. **Do not reintroduce what #241 removed.** F2, F16, F21 are all regressions
   against shipped phases. Whatever chrome we adopt, the flow underneath stays.

---

## 4. The redesign

### R1 — DAG: cluster the fan-out, then fit *(fixes F14, F15; highest value)*

The problem is not rendering speed, it is that Pegasus DAGs are dominated by
scatter fan-outs of identical transformations and we draw every sibling.

- Collapse consecutive siblings that share a transformation and a rank into one
  **group node**: `mutation_overlap ×167`, with a status bar across it showing
  the mix (e.g. 140 succeeded / 20 running / 7 failed). Expand on click into a
  side list, not into the canvas.
- With grouping, a 170-node 1000Genomes DAG becomes roughly 6–10 nodes and fits
  at a readable zoom without touching `minZoom`.
- Keep a real cap as a backstop: above ~300 nodes after grouping, render the
  rank summary and offer "show all" explicitly rather than silently overflowing.
- Change `rankdir` to `"LR"` for wide graphs, or keep TB and let the group node
  absorb the width — decide with a measurement, not a preference.
- Replace the "large graph — scroll / drag" banner with nothing. If the graph
  fits, no banner is needed; if it does not, the banner is an apology.

**Verify with:** the exact 1000Genomes workflow in the capture, 170 nodes,
and a deliberately pathological one at 2,000.

### R2 — Run form: three sections, required first, real labels *(F18–F22)*

- Sections become **Your data** / **Analysis settings** / **Where it runs**,
  assigned from the existing `_infer_widget()` hints plus the plumbing list, not
  from which binary reads the flag.
- Required parameters render first inside their section and the form does not
  scroll them off screen — a sticky "1 required field needs a value" strip when
  any is empty and out of view.
- `_label_for()` gains a fallback for short-only flags (F18): when the derived
  label is ≤2 characters, fall back to `act.dest`, then to a truncated help
  clause, and never render a single letter.
- Help text collapses to one line with a "more" affordance past ~120
  characters (F22).
- One control for execution site, period (F21) — keep the #257 reconciliation
  and add a test that fails if two site inputs render on one form.

### R3 — Catalog: clean the taxonomy at the source, then rebuild the card *(F27–F31)*

Backend (`routers/marketplace.py`):
- Split `topic` on commas, trim, lowercase, and fold hyphens to spaces before it
  reaches the client, so `Machine-Learning, Nlp` becomes the two tags
  `machine learning` and `nlp`. Emit `topics: list[str]`, keeping `topic` for
  compatibility.
- Derive a display name from the slug when `name` is absent: strip a trailing
  `-workflow`, title-case the rest (F29).
- Fetch the repo description from the GitHub API when the registry omits one,
  and cache it. Where that fails, render the card without a description rather
  than inventing one (F28).

Frontend:
- Card becomes: name, one-line description, tag row, provenance, and a
  **secondary** "Add to workspace" plus a primary "Preview" that opens the
  README, the input requirements and the DAG shape in a drawer (F30, F31).
- Fold tags below a count threshold into "Other"; cap the visible chip row and
  put the rest behind "More filters" (F27).
- Mark entries already in the workspace and offer "Open" instead of "Add".

### R4 — Chat: make the context the loudest thing in the composer *(F7, F8, F10, F13)*

This is the meeting's top ask, so it gets the most visible change.

- The composer's first row is a **context bar**, not a placeholder: a chip for
  the workflow or run currently in view, one for selected data, and an explicit
  "Nothing selected — answering from general Pegasus knowledge" when empty. The
  chip is removable and addable, so the scope is a thing you set rather than a
  thing you guess.
- Placeholder changes from "Ask about Pegasus workflows…" to a task framing
  ("Describe what you want to build, run, or fix"), which is what it does.
- Split the one menu (F10) into two labeled controls: **Expertise** (the
  persona, persistent, defaults to the workflow's domain) and a **`/` command
  palette** invoked from the input itself, which is where an action belongs and
  where it is searchable as the skill list grows.
- Label the model control and resolve "default" to the actual model id (F9).
- Do not truncate descriptions in either control; the popover grows into the
  available space (F12).

### R5 — Home is the chat *(F1–F6)* — **revised in rev. 2**

Rev. 1 wanted a state dashboard with chat underneath. That splits the product in
two and makes the assistant the second thing on its own landing page. The state
belongs *inside* the conversation.

- The page is an opening turn from the assistant. Its first line says what
  needs attention, and each run that does arrives as a card with the action on
  it — "Ask what went wrong" hands the failure straight to the chat with the
  run named in the prompt.
- Runs are ordered by urgency, not recency: failed, then running, then the most
  recent finish, capped at three. This is an opening turn, not a report.
- The openers keep the prototype's card idea, which beats our old
  slash-command cards, but each shows the **full prompt** (F1) in task language
  rather than Pegasus language (F2). "I have a folder of input files and I want
  to run the same analysis on every one of them", not "Scatter-gather pipeline".
- Openers are filtered by workspace state, so a debugging opener never appears
  to someone with nothing to debug (F3).
- The composer is a **handoff**, not a second chat implementation: submitting
  calls `openChatWith()` and the real panel takes over with the text in it. One
  conversation, one place it lives.
- Terminal is not in the opener row; it stays behind the rail's Advanced
  disclosure where Phase 7 put it (F6).

### R6 — One vocabulary, one status ladder *(F32, F33)*

- Pick one word per concept and apply it to nav, page heading, column heading
  and empty state at the same time. Candidate: **Analyses** for the user's own,
  **Examples** for the catalog. The nav already says this; the pages do not.
- Collapse the status vocabulary to one ladder shared by projects and runs:
  `Draft → Ready → Running → Finished / Failed / Needs attention`. Map
  `generated`, `unsubmitted`, `pending`, `held` onto it and keep the Pegasus
  term as a tooltip for anyone who wants it.
- **This is the change that must not ship before
  `docs/ux-terminology-test.md` runs.** The last vocabulary change went live
  unvalidated (#269) and we still do not know whether the current words work.
  Running the protocol is a prerequisite for R6, not a follow-up.

### R7 — Adopt the rail; still reject the stepper *(F36, F37)* — **revised in rev. 2**

Rev. 1 said keep the labelled sidebar. Withdrawn — the dashboard read the rail
buys is worth having, and the one real objection is cheap to answer.

- **The rail is adopted.** 68px, stroke icons, with a 9px label under each. The
  label is the whole amendment: it costs 22px against an icons-only rail and
  removes the guess-and-hover that a bare rail forces on a first-time user.
  Collapsed drops the labels to 46px, so the toggle still buys the canvas back
  on the graph screen where every pixel counts.
- **Emoji are gone** (`sidebar.tsx`, `dashboard-view.tsx`). They render as a
  different glyph per platform and cannot take a colour from state.
- **The wordmark does not fit a rail.** The mark stays, the release string moves
  to the collapse toggle's tooltip.

Still not worth taking:
- **The numbered stepper** (F37). It uses wizard grammar — ordered, numbered,
  completable — for four destinations that are none of those, and it duplicates
  the rail under different names, so the app would have two navigation systems
  that disagree. If we want to guide a first run, guide it in place, on the
  workflow, where the steps exist and can actually complete.
- **A top bar** — deferred rather than rejected. The mocks show one carrying
  global search, and there is a real gap there (Studio has no ⌘K at all), but
  shipping a search box that searches nothing is worse than not shipping it.
  It waits for the search.

### R9 — The cornflower palette *(F34, and a measured contrast defect)* — **new in rev. 2**

Sampled from the captures: ground `#F3F6FF`, surfaces `#FCFDFF`, accent
`#6C9CDE`, plus six ~4%-saturation pastel tints for iconography. Cooler and
softer than the sky-on-slate it replaces.

One measured problem comes with it, and one comes free:

- **White label text on `#6C9CDE` is 2.82:1** — under the 4.5:1 AA text
  threshold *and* under the 3:1 non-text floor. It is why the prototype's
  primary buttons and its send arrow read as disabled. Filled actions move two
  steps darker to `#3F6FB8` (5.03:1); `#6C9CDE` stays as `pegasus-500` for
  resting fills and borders that are never the sole indicator.
- **Studio's current `#0284c7` is 4.1:1** — already below AA before any of this.
  The swap fixes a pre-existing failure rather than introducing one.

The `pegasus-*` scale keeps its name and only changes value, so the ~250
existing utility usages reskin without touching every component. The 500→600
step is deliberately a cliff rather than an even interval: everything filled
lives at 600+, everything decorative at 500-. Three progress-bar fills moved
500→600, because there colour *is* the sole indicator.

The dark theme is **derived, not sampled** — the captures are light-mode only.
Ground `#0E1425`, accent `#8FB4EE` (8.67:1). It should be judged on its own
rather than assumed to match anything.

### R8 — Data ingress: URLs and fetch-as-a-job *(meeting ask, no finding)*

Not visible in the captures, but named twice in the meeting and currently
unbuilt.

- Add a URL source next to the drag-and-drop in `upload-drop-zone.tsx`: paste an
  HTTP/S3/Globus URL, Studio registers it in the replica catalog rather than
  copying it to the pod.
- For large or slow sources, generate a **fetch job** as the first node of the
  workflow so the transfer happens on the execution site with Pegasus's retry
  and staging, not in the browser. This is also what
  `plugins/pegasus-ai/references/PEGASUS.md` already prescribes for API-fetching
  jobs, so the guidance and the product would finally agree.
- Show remaining quota in the upload UI. **Blocked** until the platform side
  gives a per-user number.

---

## 5. Sequencing

### Shipped in #314 — the foundation

| Change | Findings | Where |
|---|---|---|
| Cornflower palette; filled actions darkened to clear AA | F34, R9 | `globals.css`, `tailwind.config.ts`, 3 progress fills |
| Icon rail with micro-labels, emoji removed | F36 | `sidebar.tsx` |
| Home becomes the assistant's opening turn | F1–F6 | `dashboard-view.tsx` |
| DAG fan-out clustering, honest overflow notice, `minZoom` 0.1→0.04 | F14, F15 | `dag-viewer.tsx` |
| Marketplace tag splitting/folding, slug→name, `topics[]` | F27–F29 | `marketplace.py`, `marketplace/page.tsx` |
| Short-flag label fallback | F18 | `introspect_argparse.py` |

### Still to do

| # | Change | Findings | Depends on |
|---|---|---|---|
| 1 | **Operable assistant replies** — tools returning typed objects, one renderer per type | — | the thesis rests on this |
| 2 | Parameters slide-over; required-first; section regrouping | F19, F20, F22 | — |
| 3 | Chat context bar; split persona from `/` commands; name the model | F7–F10, F13 | needs `context-chip.tsx` from `main` |
| 4 | Catalog card rebuild with Preview | F30, F31 | shipped taxonomy work |
| 5 | Global search (⌘K), then the top bar that carries it | — | — |
| 6 | Data ingress: URL sources and fetch-as-a-job | — | quota number |
| 7 | Vocabulary and status ladder | F32, F33 | **terminology test must run first** |

Item 1 is the one that decides whether any of this is what it claims to be.
Item 7 is gated on research, not on code — which is why #314 left "Resources"
alone in the rail rather than renaming it to "Compute" as the mocks show.

---

## 6. Open questions

1. **Whose prototype is this and is it going somewhere?** The captures show
   sustained design work by someone outside this repo. If that work is
   continuing, sections R5 and R7 should be a conversation with its author
   rather than a unilateral rewrite. If it was a one-off exploration, take the
   icon set and the example-gallery idea and move on.
2. **Do we adopt the Pegasus MCP/REST API for run discovery** (meeting ask) or
   keep globbing `braindump.yml`? This decides whether F25 and F26 get fixed
   properly or patched. It is an architecture call, not a UI one, and it should
   be made before item 1 hardens more UI around the current scanner's data
   shape.
3. **What is the per-user disk quota?** Blocks the visible half of R8.
4. **Who runs the terminology test, and on whom?** The meeting noted the team
   needs users outside it for unbiased feedback. Nine of these findings are
   wording problems; without the test we are guessing, and the last time we
   guessed we shipped it default-on.

---

## Appendix — verification notes

Claims marked "Reproduces in `main`" were checked against the working tree at
`3ae5d02` (branch `feat/workflow-scan-settings`):

- **F14** — `studio-web/components/workflows/dag-viewer.tsx:48,49,59,262,263`.
  `NODE_W=190`, `nodesep=30`, `rankdir="TB"`, `fitView`, `minZoom={0.1}`.
  167 siblings × 220px = 36,740px; 1,200px pane ⇒ required zoom 0.033 < 0.1.
  No node cap in the component or in `studio-api/routers/workflows.py`.
- **F18** — `studio-api/introspect_argparse.py:118-128`. Help longer than 48
  chars falls through to `sorted(act.option_strings, key=len)[-1].lstrip("-")`,
  which is `s` for a generator declaring only `-s`.
- **F27/F28/F29** — `studio-api/routers/marketplace.py:119,134-137` (raw
  `topic`, `description` defaulting to `""`, `_id_from_repo` as the name
  fallback) and `studio-web/app/(studio)/marketplace/page.tsx:27-32` (chip set
  from a bare `Set` of `w.topic`).
- **F9** — `studio-web/components/chat/chat-input.tsx:319`.
- **F25** — `studio-web/app/(studio)/workflows/page.tsx:44-49`.
- **F32** — `sidebar.tsx:36,40`; `workflows/page.tsx:44`;
  `marketplace/page.tsx:37`.

Findings marked *Prototype-only* were checked for absence: `git log --all
-S"Ask AI" -- studio-web/` and `-S"Run Jobs"` both return no commits, and no
top-bar or stepper component exists in `studio-web/components/layout/`.
