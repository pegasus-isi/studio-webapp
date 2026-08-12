# studio-webapp TODO Plan

> **Historical snapshot (July 13, 2026).** Kept for provenance. Much of the
> "Absent today" list has since shipped (dashboard, Resources/OSPool panels,
> runtime estimates), and PR references here predate PR #55. For current status
> see `docs/CODE_REVIEW_2026-08.md`.


Derived July 13, 2026 from the Studio Web App deck
(`outputs/studio-web-app-deck/PegasusAI-Studio-Web-App-Presentation.pptx`,
slides 11–17), `studio-next-todo.md`, `docs/CODE_REVIEW_2026-07.md`, and a
codebase gap survey. Ordered by the deck's own priority call (slide 16):
OOD/UMass path → landing/dashboard shape → provisioning/resource visibility →
deployable handoff artifacts, with agentic work after the foundations.

Each item is tagged: **[blocker]** deployment blocker · **[demo]** demo
commitment · **[spike]** research spike.

## Current state (verified against the repo)

- PRs #1–#13 all merged; no open PRs. Working tree is on
  `fix/chat-transient-retry` (already merged) — switch back to `main`.
- Open review findings: `run_command` shell posture (⏸ needs decision), S7
  plaintext API keys (⏳), S10 Dockerfile supply chain (⏸).
- Absent today: dashboard/landing page (`/` redirects to Workflows),
  provisioning nav/page, pool monitoring (only a stub at
  `studio-api/services/notification_engine.py:313` `_check_resources`),
  runtime prediction, guided tours, pegasus-statistics, pegasus-em,
  Annex/FABRIC/JetStream provisioning, autonomous agent loop.
- Present and reusable: notification center with SSE + categories (including
  an unused `resource` category), analyzer API, stampede DB reader, chat tool
  framework, agent personas, 11-module test suite.

---

## Phase 0 — Carryover hygiene (do first, small)

1. **Decide `run_command` posture** [blocker for agentic work]
   (`studio-api/routers/chat.py:202-221`). Currently arbitrary shell behind
   the chat endpoint; the deck (slide 8) claims "bounded by design".
   Decision: allowlist / policy-gate / per-call approval. Design the gate as
   a reusable **policy layer** — the same mechanism becomes the slide-13
   guardrail (policy-gated submit/provision/delete) later. Do not build it
   twice.
2. **Finish S7** — stop storing LLM API keys plaintext in SQLite
   (`studio-api/db.py:39-70`); at minimum keyring/file-permission hardening
   and stop echoing keys from `validate_provider`.
3. **S10 Dockerfile supply chain** [blocker] — `deb [trusted=yes]`
   (`Dockerfile:134`) etc. Rises in priority because the tarball handoff
   (Phase 1) means external sites will build/run this image.
4. Close stale item: `test_user_storage_relocation.py` does not exist in the
   repo — dropped from the backlog.

## Phase 1 — Deployment paths (P0, UMass-driven)

5. **Open OnDemand deployment path** [blocker]
   - Package Studio as an OOD interactive app (app manifest + form +
     submit template pointing at the container).
   - **Condor-optional mode**: today HTCondor is assumed present. Add a
     runtime flag (e.g. `STUDIO_CONDOR=off|auto`) — health endpoint reports
     it, Workflows page degrades gracefully (generate/plan against a
     non-condor site, hide submit/monitor affordances), notification engine
     skips condor polling.
   - Capture Berent's feedback as issues against this path.
6. **Downloadable tarball + deployment instructions** [blocker]
   - `make dist` (or script) producing `docker save` tarball + versioned
     docs: JupyterHub, OOD, and native-on-existing-Condor-cluster recipes.
   - Native deployment doc covers the non-container case assumptions
     (ports, workspace dir, supervisord vs. systemd).
7. **FABRIC / JetStream deployment paths** [spike] — validate the container
   under each; document image/flavor, ingress, and persistent-volume
   choices. (Deployment here is distinct from provisioning in Phase 3.)

## Phase 2 — Dashboard + resource visibility (demo shape)

> **STATUS: implemented 2026-07-13** on branch
> `feat/dashboard-resource-visibility` (items 8–12; item 11 is v1 only —
> the PegasusOracle v2 backend remains open).

8. **Landing page / dashboard at `/`** [demo]
   (`studio-web/app/(studio)/page.tsx` currently redirects to Workflows).
   Panels: workflows summary (counts by state, recent runs), resources
   available, overview/quick actions, guided-tour entry point (hook for
   Phase 5). Backend: one new aggregate endpoint (`GET /api/dashboard`)
   composing existing workflow + health data.
9. **Provisioning nav item + page (read-only first)** [demo]
   - Add "Provisioning" to `studio-web/components/layout/sidebar.tsx`.
   - New backend service polling `condor_status` for pool inventory:
     provisioned pool / OS pool / base Condor pool; total + idle CPUs and
     GPUs. Cache and expose via REST + SSE.
   - Supply/demand view: current workflow queue demand (from stampede/condor_q)
     vs. pool capacity.
10. **Wire `_check_resources`** [demo] — replace the stub with real
    detection: idle/held jobs with unmatched requirements, queue wait past a
    configured threshold, demand gap vs. capacity. Emit `resource`-category
    notifications with the *reason* and required delta ("needs 8 CPUs / 1
    GPU"). Explainable thresholds only; no automation yet.
11. **Remaining-time estimate** [demo, then spike]
    - v1: historical per-transformation mean runtimes from the stampede DB
      (reader already exists) × remaining job counts — cheap and explainable.
    - v2 [spike]: integrate PegasusOracle (`pegasusai/Jobs-Runtime-Prediction`
      services, ports 8000/8001) as an optional predictor backend.
12. **Analyzer/diagnostics UI panel** — analyzer runs via
    `POST /api/workflows/{run_id}/analyze` but has no dedicated UI surface;
    the deck (slide 6) presents it as a first-class view. Small, high-value.

## Phase 3 — Resource add decision loop (Act + Verify)

13. **Actionable resource notifications** — CTA set from slide 12: add
    resources / view recommendation / snooze / mark handled manually.
14. **Audit trail** — SQLite table recording decision, action, result,
    resolution for every resource event. Shared later by the agent (Phase 4).
15. **Provisioner adapter interface** [spike] — one interface, three
    implementations arriving in order: FABRIC and JetStream adapters now (to
    validate UX/resource model), **HTCondor Annex adapter when Condor 26
    ships (~Aug 2026 — tracked external dependency, do not block on it)**.
16. Manual approval remains mandatory for all provisioning actions until
    false-positive and failure rates are understood (deck slide 12 note).

## Phase 4 — Agentic workflow operator (north star)

17. **Autonomy levels** — Assist (exists: chat), **Approve** (agent prepares
    generate/plan/submit/fix actions, user approves diffs/commands before
    execution), Autopilot (later, policy-bounded). Approve mode is the next
    concrete deliverable.
18. **Operator loop** — Plan → Run → Monitor → Diagnose → Fix → Retry,
    composed from existing pieces: workflows API (generate/plan/submit),
    SSE events + notifications (monitor), analyzer + exit codes + job
    metadata (diagnose), chat file tools (fix), bounded rerun (retry).
19. **Guardrails before Autopilot** — policy-gated actions (reuse Phase 0
    gate), diff review + checkpoint (git snapshot of project dir) + rollback,
    bounded retry budget, escalation on low confidence/repeat failure, audit
    trail (reuse Phase 3 table).

## Phase 5 — Enablement + workflow intelligence

20. **Guided tour framework** [demo] — pick driver.js or react-joyride;
    attach to dashboard, Workflows, Workbench, Settings. Tours: new-workflow
    creation, script→workflow conversion, Variant Calling tutorial.
21. **pegasus-statistics integration** [spike→demo] — "how did Pegasus
    improve the workflow": runtime, reliability, resource use per run;
    ties into the dashboard overview panel.
22. **Ensemble Manager (pegasus-em) integration** [spike].

---

## Sequencing notes

- Phases 0–1 unblock UMass/external users; Phase 2 gives the deck's demo
  flow a home screen and the provisioning story a visible surface.
- Item 1 (`run_command` policy) is deliberately first: it resolves the open
  security finding *and* is the foundation for items 16/17/19.
- The Condor 26 / Annex dependency gates only item 15's third adapter;
  everything else in Phases 2–3 proceeds against the existing pool.
- Deck slide 17's open product question — demo commitment vs. research spike
  vs. deployment blocker — is answered by the tags above; revisit tags when
  UMass feedback lands.
