"""Onboarding funnel — the baseline for the non-specialist redesign.

Phase 0 of the non-specialist redesign (#241). Phase 8 judges the whole
redesign on **time from first login to first successful run**, which means the
measurement has to exist *before* the UI changes. Instrumented afterwards it
would only ever describe the redesigned app, with nothing to compare against.

Four milestones, each recorded once and never overwritten:

    first_seen      the workspace was opened for the first time
    first_workflow  an analysis exists in the workspace (added or authored)
    first_run       a run exists — they got as far as submitting
    first_success   a run reached "succeeded" — the thing they came for

The intermediate two matter as much as the endpoints: a single
time-to-first-success number says the funnel is slow, but not *where*. If most
users reach `first_workflow` quickly and stall before `first_run`, the run form
is the problem; if they submit promptly and never succeed, the failure path is.
Those point at different phases of the plan.

## Two deliberate design choices

**Structlog as well as SQLite.** The DB is per-user, inside a pod, on that
user's NFS home — fine for showing a single workspace its own progress, useless
for answering "did the redesign help?" across a cohort. Every milestone also
emits a log event, which reaches the cluster's log pipeline where it can be
aggregated. The DB row is what makes the event fire exactly once.

**Pre-existing workspaces are flagged, not counted.** See
`Database.has_prior_activity`. A user upgrading into this code has months of
successful runs already; recording their milestones now would produce a
near-zero time-to-first-success and poison the baseline. Their snapshot carries
`preexisting_workspace: true` and must be excluded from cohort statistics.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import structlog

log = structlog.get_logger()

_SETTING_KEY = "onboarding_funnel"

# Ordered: each milestone implies the ones before it. A run cannot exist
# without an analysis, so observing a run backfills `first_workflow` if the
# poll that would have caught it never ran (e.g. the user went straight to the
# Workflows page and never loaded the dashboard).
MILESTONES: tuple[str, ...] = (
    "first_seen",
    "first_workflow",
    "first_run",
    "first_success",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _key(milestone: str) -> str:
    return f"{milestone}_at"


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


async def _load(db: Any) -> dict[str, Any]:
    data = await db.get_setting(_SETTING_KEY, None)
    return dict(data) if isinstance(data, dict) else {}


async def record(db: Any, *reached: str) -> list[str]:
    """Record every milestone in `reached` that is not already set.

    Returns the milestones newly recorded this call (empty is the normal case
    — this runs on every dashboard/workflow poll). Earlier milestones implied
    by a later one are backfilled with the same timestamp, so a funnel is never
    missing a step it must logically have passed through.

    Unknown milestone names are ignored rather than raising: this is
    instrumentation, and a typo in a call site must not take down the endpoint
    it is measuring.
    """
    wanted = [m for m in reached if m in MILESTONES]
    if not wanted:
        return []

    # Backfill implied predecessors.
    highest = max(MILESTONES.index(m) for m in wanted)
    implied = set(MILESTONES[: highest + 1])

    state = await _load(db)
    missing = [m for m in MILESTONES if m in implied and not state.get(_key(m))]
    if not missing:
        return []

    now = _now()
    first_seen = _parse(state.get(_key("first_seen")))

    # The very first write decides whether this workspace counts toward the
    # baseline at all — check before stamping first_seen, while "has anything
    # ever happened here" still means "before this instrumentation existed".
    if "first_seen" in missing:
        try:
            state["preexisting_workspace"] = await db.has_prior_activity()
        except Exception as e:  # noqa: BLE001 — never fail a poll over metrics
            log.warning("onboarding_prior_activity_check_failed", error=str(e))
            state["preexisting_workspace"] = False

    for milestone in missing:
        state[_key(milestone)] = now

    try:
        await db.set_setting(_SETTING_KEY, state)
    except Exception as e:  # noqa: BLE001
        log.warning("onboarding_persist_failed", error=str(e))
        return []

    base = first_seen or _parse(state.get(_key("first_seen")))
    for milestone in missing:
        elapsed = None
        if base and milestone != "first_seen":
            elapsed = round((_parse(now) - base).total_seconds(), 3)  # type: ignore[operator]
        log.info(
            "onboarding_milestone",
            milestone=milestone,
            at=now,
            seconds_since_first_seen=elapsed,
            preexisting_workspace=bool(state.get("preexisting_workspace")),
        )
    return missing


async def observe(
    db: Any,
    *,
    has_projects: bool = False,
    has_runs: bool = False,
    has_success: bool = False,
) -> None:
    """Translate an observation of workspace state into milestones.

    Convenience wrapper for the poll endpoints, which know *what they see*
    rather than *what is new*. Safe to call on every request.
    """
    reached = ["first_seen"]
    if has_projects:
        reached.append("first_workflow")
    if has_runs:
        reached.append("first_run")
    if has_success:
        reached.append("first_success")
    await record(db, *reached)


async def snapshot(db: Any) -> dict[str, Any]:
    """The funnel as timestamps plus elapsed seconds from first_seen.

    `seconds` holds the headline metric (`first_success`) alongside the
    intermediate steps. A milestone not yet reached is absent from `seconds`
    rather than zero, so "not there yet" is never mistaken for "instant".
    """
    state = await _load(db)
    base = _parse(state.get(_key("first_seen")))

    milestones: dict[str, str | None] = {}
    seconds: dict[str, float] = {}
    for milestone in MILESTONES:
        ts = state.get(_key(milestone))
        milestones[milestone] = ts
        moment = _parse(ts)
        if base and moment and milestone != "first_seen":
            seconds[milestone] = round((moment - base).total_seconds(), 3)

    return {
        "milestones": milestones,
        "seconds_from_first_seen": seconds,
        # True = this workspace predates the instrumentation; its timings are
        # not a valid baseline sample and must be excluded from cohort stats.
        "preexisting_workspace": bool(state.get("preexisting_workspace")),
        "complete": all(milestones[m] for m in MILESTONES),
    }
