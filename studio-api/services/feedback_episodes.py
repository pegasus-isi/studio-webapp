"""Feedback episodes: "Did the AI help?" around real workflow failures.

Every failed run becomes an **episode**: the run fails, the user asks the AI
about it (or doesn't), re-runs the same workflow, and that re-run finishes.
The episode records each step, and the user is asked one question only at the
turning points, where the answer means something:

* **help** card — an episode where the AI was asked closes (the re-run reached
  a terminal state), or nobody re-ran within ``STUDIO_FEEDBACK_HELP_TIMEOUT_SECONDS``
  of asking. "Your re-run of X succeeded. Did the AI's suggestion fix it?"
* **nudge** — a run failed and nobody asked the AI within
  ``STUDIO_FEEDBACK_NUDGE_SECONDS``. "X failed. Want the AI to look?"
* **help_after_nudge** — a help card for an episode whose nudge the user
  answered with "Ask the AI". Kept distinct so the nudge's effect survives the
  help card overwriting ``answer``.

Give-up tags (the row under a stopped or errored chat turn) are recorded here
too, but are not prompts in the budget sense: the user just acted, and the row
is one click under their own message.

## Why the rules live on the server

The fatigue rules (one prompt per browser session, help cards only for the
first three episodes, two dismissals snooze everything for a week, the
``feedback`` mute) are the whole difference between a survey people answer and
one they learn to swat. Enforced in the browser they would reset on every
reload and differ between tabs; here they are one ledger
(``app_settings.feedback_prompt_state``) that every tab asks.

## Matching a re-run to its episode

A workflow's identity across runs is its Pegasus workflow name
(``pegasus_wf_name`` in braindump.yml, the scanner's ``name``): the run ids are
per-run wf_uuids, and the submit dirs are per-run ``runNNNN``. A re-run is

* a run of the same workflow in a *different* submit dir, planned after the
  failure was seen (so a sibling that was already running is not mistaken for
  a fix); or
* the *same* submit dir leaving the failed state (``pegasus-run`` on a rescue
  DAG resubmits in place).

Both are matched twice: eagerly when Studio submits (``observe_submit``), and
from state on every watch-loop scan (``process_scan``) for runs submitted from
a terminal. State rather than transitions wherever possible, so a pod restart
mid-episode loses nothing but the moment of failure.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from db import Database

log = structlog.get_logger()

ANSWERS = frozenset({
    "fixed",
    "partly",
    "no_help",
    "did_not_try",
    "fixed_myself",
    "ask_ai",
    "dismissed",
})
GIVEUP_REASONS = frozenset({"stop", "error"})
GIVEUP_TAGS = frozenset({"wrong_direction", "too_slow", "error", "other"})

PROMPT_HELP = "help"
PROMPT_NUDGE = "nudge"
PROMPT_HELP_AFTER_NUDGE = "help_after_nudge"
_HELP_TYPES = (PROMPT_HELP, PROMPT_HELP_AFTER_NUDGE)

#: The notification kind a user mutes to never see these prompts.
MUTE_KIND = "feedback"
_MUTED_SETTING_KEY = "muted_notification_kinds"  # notification_engine's key
_STATE_KEY = "feedback_prompt_state"

MAX_HELP_CARDS = 3
DISMISSALS_TO_SNOOZE = 2
SNOOZE = timedelta(days=7)
#: A prompt about something this old is noise: the user has moved on and would
#: have to reconstruct what the question is even about.
STALE_AFTER = timedelta(hours=24)
#: How far back the prompt queries look at all. Bounds the scan; older
#: episodes stay in the table for the collection script.
_LOOKBACK = timedelta(days=7)
#: A run planned this long before the failure was *seen* still counts as a
#: re-run: the watch loop notices a failure up to one poll after it happens.
_RERUN_SLACK_SECONDS = 30
#: A run first sighted already failed opens an episode only if it was planned
#: this recently. Otherwise adding a scan root full of old failures would
#: nudge about every one of them.
_FRESH_FIRST_SIGHTING = timedelta(hours=1)
_MAX_SESSIONS_TRACKED = 100
_COMMENT_MAX = 2000

_TERMINAL = {"succeeded", "failed", "cancelled"}
#: Statuses that say nothing about whether a run moved — a status read that
#: failed must not look like a rescue re-run of a failed dir.
_UNINFORMATIVE = {"", "unknown", None}
#: Planned but never submitted (the Plan action, a crashed plan): not a
#: re-run yet. Linking one would park the episode waiting for a result that
#: may never come, and its help card with it.
_NOT_SUBMITTED = {"planning", "planned"}

_DEFAULT_NUDGE_SECONDS = 600
_DEFAULT_HELP_TIMEOUT_SECONDS = 900

# Serialises read-modify-write of the prompt-state ledger. Two tabs polling at
# once, or an answer landing mid-poll, would otherwise lose a dismissal count.
_state_lock = asyncio.Lock()


# --- configuration -----------------------------------------------------------


def _env_seconds(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(float(raw))
    except ValueError:
        # A typo in a dry-run override must not take the feature down; say so
        # in the log and fall back to the real timing.
        log.warning("feedback_bad_env", name=name, value=raw)
        return default
    return max(0, value)


def nudge_seconds() -> int:
    return _env_seconds("STUDIO_FEEDBACK_NUDGE_SECONDS", _DEFAULT_NUDGE_SECONDS)


def help_timeout_seconds() -> int:
    return _env_seconds(
        "STUDIO_FEEDBACK_HELP_TIMEOUT_SECONDS", _DEFAULT_HELP_TIMEOUT_SECONDS
    )


def prompts_enabled_by_env() -> bool:
    """``STUDIO_FEEDBACK_PROMPTS=0`` stops every prompt; recording continues."""
    raw = os.environ.get("STUDIO_FEEDBACK_PROMPTS", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


# --- time helpers (``_now`` is patched by tests) -----------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.isoformat()


def _parse(ts: Any) -> datetime | None:
    if not ts:
        return None
    try:
        moment = datetime.fromisoformat(str(ts))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _planned_at(run_dir: str) -> datetime | None:
    """When a run was planned: braindump.yml's mtime, written by pegasus-plan.

    The braindump's own ``timestamp`` field would do, but its format has varied
    across Pegasus versions and the file's mtime has not.
    """
    for path in (os.path.join(run_dir, "braindump.yml"), run_dir):
        try:
            return datetime.fromtimestamp(os.path.getmtime(path), timezone.utc)
        except OSError:
            continue
    return None


# --- prompt-state ledger ------------------------------------------------------


def _default_state() -> dict[str, Any]:
    return {
        "consecutive_dismissals": 0,
        "snoozed_until": None,
        "sessions_shown": {},
        "help_cards_shown": 0,
    }


async def _load_state(db: Database) -> dict[str, Any]:
    raw = await db.get_setting(_STATE_KEY, None)
    state = _default_state()
    if isinstance(raw, dict):
        state.update({k: raw[k] for k in state if k in raw})
    if not isinstance(state["sessions_shown"], dict):
        state["sessions_shown"] = {}
    for key in ("consecutive_dismissals", "help_cards_shown"):
        try:
            state[key] = int(state[key] or 0)
        except (TypeError, ValueError):
            state[key] = 0
    return state


async def _save_state(db: Database, state: dict[str, Any]) -> None:
    shown = state.get("sessions_shown") or {}
    if len(shown) > _MAX_SESSIONS_TRACKED:
        # A session id is minted per tab load, so this map only grows. Keep the
        # newest; a session old enough to fall off is a tab long since closed.
        newest = sorted(
            shown.items(), key=lambda kv: str((kv[1] or {}).get("at") or "")
        )[-_MAX_SESSIONS_TRACKED:]
        state["sessions_shown"] = dict(newest)
    await db.set_setting(_STATE_KEY, state)


async def prompt_block_reason(
    db: Database, state: dict[str, Any] | None = None, now: datetime | None = None
) -> str | None:
    """Why no prompt may be shown at all right now, or None if one may."""
    if not prompts_enabled_by_env():
        return "disabled"
    muted = await db.get_setting(_MUTED_SETTING_KEY, [])
    if isinstance(muted, list) and MUTE_KIND in {str(k) for k in muted}:
        return "muted"
    state = state if state is not None else await _load_state(db)
    snoozed = _parse(state.get("snoozed_until"))
    if snoozed and snoozed > (now or _now()):
        return "snoozed"
    return None


# --- episode lifecycle --------------------------------------------------------


async def _failure_category(run_dir: str) -> str | None:
    """The run_diagnosis category of the first failed job, best effort.

    Bounded: this runs inside the watch loop, and a stampede DB on a slow NFS
    mount must not stall every other notification behind it.
    """
    try:
        from services import run_diagnosis
        from services.workflow_monitor import get_workflow_jobs

        jobs = await asyncio.wait_for(get_workflow_jobs(run_dir), timeout=10)
        return run_diagnosis.categorize_jobs(jobs)
    except Exception as e:  # noqa: BLE001 — a category is nice to have
        log.debug("feedback_category_failed", run_dir=run_dir, error=str(e))
        return None


async def open_episode(
    db: Database,
    *,
    run_id: str,
    run_dir: str | None,
    workflow: str | None,
    source: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or _now()
    category = await _failure_category(run_dir) if run_dir else None
    episode = await db.add_feedback_episode(
        workflow=workflow,
        run_id=run_id,
        run_dir=run_dir,
        failure_category=category,
        failed_at=_iso(now),
        ai_turns=0,
    )
    log.info(
        "feedback_episode",
        step="open",
        episode_id=episode["id"],
        run_id=run_id,
        workflow=workflow,
        failure_category=category,
        source=source,
    )
    return episode


async def _link_rerun(
    db: Database,
    episode: dict[str, Any],
    *,
    rerun_id: str,
    rerun_dir: str,
    now: datetime,
    source: str,
) -> None:
    await db.update_feedback_episode(
        episode["id"],
        rerun_id=rerun_id,
        rerun_dir=rerun_dir,
        rerun_submitted_at=_iso(now),
    )
    episode.update(rerun_id=rerun_id, rerun_dir=rerun_dir, rerun_submitted_at=_iso(now))
    log.info(
        "feedback_episode",
        step="rerun",
        episode_id=episode["id"],
        rerun_id=rerun_id,
        in_place=rerun_dir == episode.get("run_dir"),
        source=source,
    )


async def _close(
    db: Database, episode: dict[str, Any], status: str, now: datetime
) -> None:
    await db.update_feedback_episode(
        episode["id"], closed_at=_iso(now), rerun_status=status
    )
    episode.update(closed_at=_iso(now), rerun_status=status)
    log.info(
        "feedback_episode",
        step="close",
        episode_id=episode["id"],
        rerun_status=status,
        asked_ai=bool(episode.get("asked_ai_at")),
        ai_turns=episode.get("ai_turns") or 0,
    )


def _is_rerun_candidate(
    episode: dict[str, Any], obs: dict[str, Any], failed_at: datetime | None
) -> bool:
    if not episode.get("workflow") or obs.get("workflow") != episode["workflow"]:
        return False
    if obs.get("run_dir") == episode.get("run_dir"):
        return False
    if obs.get("status") in _UNINFORMATIVE or obs.get("status") in _NOT_SUBMITTED:
        return False
    planned = _planned_at(str(obs.get("run_dir") or ""))
    if planned is None or failed_at is None:
        return False
    return planned >= failed_at - timedelta(seconds=_RERUN_SLACK_SECONDS)


async def process_scan(
    db: Database, observations: list[dict[str, Any]], *, seed_only: bool
) -> None:
    """Advance every episode from one watch-loop scan.

    ``observations`` holds one dict per run: ``run_id``, ``run_dir``,
    ``workflow``, ``status`` and ``prev_status`` (None at first sighting).
    Order matters: link before close (a rescue that already finished links and
    closes in one pass), and close before open (a re-run that failed again
    closes its episode and then opens its own).
    """
    now = _now()
    by_dir = {o["run_dir"]: o for o in observations if o.get("run_dir")}
    open_eps = await db.list_feedback_episodes("closed_at IS NULL")

    # 1. Link re-runs.
    for ep in open_eps:
        if ep.get("rerun_dir"):
            continue
        own = by_dir.get(ep.get("run_dir") or "")
        if own and own.get("status") not in _UNINFORMATIVE and own["status"] != "failed":
            # The failed dir is moving again: resubmitted in place.
            await _link_rerun(
                db, ep, rerun_id=own["run_id"], rerun_dir=own["run_dir"],
                now=now, source="scan",
            )
            continue
        failed_at = _parse(ep.get("failed_at"))
        candidates = [
            o for o in observations if _is_rerun_candidate(ep, o, failed_at)
        ]
        if candidates:
            first = min(
                candidates,
                key=lambda o: _planned_at(o["run_dir"]) or now,
            )
            await _link_rerun(
                db, ep, rerun_id=first["run_id"], rerun_dir=first["run_dir"],
                now=now, source="scan",
            )

    # 2. Close episodes whose re-run finished.
    for ep in open_eps:
        rerun_dir = ep.get("rerun_dir")
        if not rerun_dir or ep.get("closed_at"):
            continue
        obs = by_dir.get(rerun_dir)
        if obs and obs.get("status") in _TERMINAL:
            await _close(db, ep, obs["status"], now)

    # 3. Open episodes for new failures. Only for a failure seen happening —
    #    a transition, or a fresh run first seen already failed. The seed scan
    #    at startup sees every old failure in the workspace for the first time
    #    and must not turn them into nudges.
    if seed_only:
        return
    still_open_dirs = {
        ep.get("run_dir") for ep in open_eps if not ep.get("closed_at")
    }
    for obs in observations:
        if obs.get("status") != "failed":
            continue
        prev = obs.get("prev_status")
        if prev == "failed":
            continue
        if prev is None:
            planned = _planned_at(obs["run_dir"])
            if planned is None or now - planned > _FRESH_FIRST_SIGHTING:
                continue
        if obs["run_dir"] in still_open_dirs:
            continue
        await open_episode(
            db,
            run_id=obs["run_id"],
            run_dir=obs["run_dir"],
            workflow=obs.get("workflow"),
            source="scan",
            now=now,
        )
        still_open_dirs.add(obs["run_dir"])


async def observe_submit(db: Database, run_dir: str) -> None:
    """Link a run Studio just submitted to the episode it re-runs, if any.

    The scan would find it within one poll anyway; doing it here makes
    ``rerun_submitted_at`` the real submit time and covers a run that fails
    before the next poll ever sees it running.
    """
    from services.workflow_scanner import _parse_braindump

    try:
        bd = _parse_braindump(os.path.join(run_dir, "braindump.yml"))
    except Exception:  # noqa: BLE001 — no braindump, no identity to match on
        return
    run_id = str(bd.get("wf_uuid") or os.path.basename(run_dir))
    workflow = bd.get("pegasus_wf_name")
    now = _now()
    for ep in await db.list_feedback_episodes(
        "closed_at IS NULL AND rerun_dir IS NULL"
    ):
        same_dir = ep.get("run_dir") == run_dir
        same_workflow = bool(workflow) and ep.get("workflow") == workflow
        if same_dir or same_workflow:
            await _link_rerun(
                db, ep, rerun_id=run_id, rerun_dir=run_dir, now=now,
                source="submit",
            )


# --- asking the AI ------------------------------------------------------------


async def mark_asked(
    db: Database,
    key: str,
    *,
    conversation_id: str | None,
    turn: bool,
) -> dict[str, Any] | None:
    """Record that the AI was asked about an episode (by episode or run id).

    ``turn`` is True for a chat turn actually sent while scoped to the run, and
    False for "Ask what went wrong", which only opens the chat with a draft:
    the click is the intent to ask, the turns are how much asking it took.

    An unknown run that is known to have failed opens its episode on the spot.
    The watch loop opens episodes only for failures it sees happen, so a run
    that failed while the pod was down has none — and the user asking about it
    is exactly the episode worth recording.
    """
    episode = await db.find_feedback_episode(key)
    if episode is None:
        run = await db.get_workflow_run(key)
        if not run or run.get("status") != "failed":
            return None
        episode = await open_episode(
            db,
            run_id=run["run_id"],
            run_dir=run.get("run_dir"),
            workflow=run.get("name"),
            source="asked",
        )

    updates: dict[str, Any] = {}
    if conversation_id and not episode.get("conversation_id"):
        updates["conversation_id"] = conversation_id[:200]
    if turn:
        updates["ai_turns"] = int(episode.get("ai_turns") or 0) + 1
    first_ask = not episode.get("asked_ai_at") and not episode.get("closed_at")
    # Asking after the re-run already finished is about something settled; it
    # still counts as a turn, but must not make a "did it fix it?" card due
    # for a fix the user found without it.
    if first_ask:
        updates["asked_ai_at"] = _iso(_now())
    if updates:
        await db.update_feedback_episode(episode["id"], **updates)
        episode.update(updates)
    if first_ask:
        log.info(
            "feedback_episode",
            step="asked",
            episode_id=episode["id"],
            via="turn" if turn else "button",
        )
    return episode


# --- prompts ------------------------------------------------------------------


def due_prompt_type(episode: dict[str, Any], now: datetime) -> str | None:
    """Which prompt this episode is due for right now, ignoring the budget."""
    answer = episode.get("answer")
    if answer and answer != "ask_ai":
        return None
    asked = _parse(episode.get("asked_ai_at"))
    if asked:
        closed = _parse(episode.get("closed_at"))
        if closed:
            ready = closed
        elif not episode.get("rerun_dir"):
            ready = asked + timedelta(seconds=help_timeout_seconds())
        else:
            return None  # re-run in flight: its result is the better question
        if now < ready or now - ready > STALE_AFTER:
            return None
        if answer == "ask_ai" or episode.get("prompt_type") == PROMPT_NUDGE:
            return PROMPT_HELP_AFTER_NUDGE
        return PROMPT_HELP
    if answer:
        return None
    if episode.get("closed_at") or episode.get("rerun_dir"):
        return None  # they already re-ran without asking; nothing to nudge
    failed = _parse(episode.get("failed_at"))
    if failed is None:
        return None
    ready = failed + timedelta(seconds=nudge_seconds())
    if now < ready or now - ready > STALE_AFTER:
        return None
    return PROMPT_NUDGE


def _allowed_by_budget(
    episode: dict[str, Any], prompt_type: str, state: dict[str, Any]
) -> bool:
    if prompt_type not in _HELP_TYPES:
        return True
    if episode.get("prompt_type") in _HELP_TYPES:
        return True  # already counted against the budget
    return int(state.get("help_cards_shown") or 0) < MAX_HELP_CARDS


def _message(episode: dict[str, Any], prompt_type: str) -> str:
    name = episode.get("workflow") or "Your workflow"
    if prompt_type == PROMPT_NUDGE:
        return f"{name} failed. Want the AI to look?"
    status = episode.get("rerun_status")
    if episode.get("closed_at") and status:
        outcome = {"succeeded": "succeeded", "failed": "failed again"}.get(
            status, "finished"
        )
        return f"Your re-run of {name} {outcome}. Did the AI's suggestion fix it?"
    return f"You asked the AI about {name} failing. Did its suggestion help?"


def _prompt_payload(episode: dict[str, Any], prompt_type: str) -> dict[str, Any]:
    return {
        "episode_id": episode["id"],
        "prompt_type": prompt_type,
        "kind": "nudge" if prompt_type == PROMPT_NUDGE else "help",
        "workflow": episode.get("workflow"),
        "run_id": episode.get("run_id"),
        "rerun_status": episode.get("rerun_status"),
        "failure_category": episode.get("failure_category"),
        "message": _message(episode, prompt_type),
    }


async def _candidates(db: Database, now: datetime) -> list[dict[str, Any]]:
    return await db.list_feedback_episodes(
        "(answer IS NULL OR answer = 'ask_ai') AND failed_at >= ?",
        (_iso(now - _LOOKBACK),),
    )


async def due_prompts(db: Database) -> list[tuple[str, str]]:
    """(episode id, prompt type) for every prompt that could be shown now.

    Session-agnostic: used by the watch loop to poke browsers, which then ask
    ``pending`` — where the per-session rule is applied.
    """
    now = _now()
    state = await _load_state(db)
    if await prompt_block_reason(db, state, now):
        return []
    due: list[tuple[str, str]] = []
    for ep in await _candidates(db, now):
        prompt_type = due_prompt_type(ep, now)
        if prompt_type and _allowed_by_budget(ep, prompt_type, state):
            due.append((ep["id"], prompt_type))
    return due


async def pending(db: Database, session: str) -> dict[str, Any] | None:
    """The one prompt this browser session may show now, after every rule.

    Showing is recorded here — the caller is about to render it — so this is
    where the per-session slot and the help-card budget are spent.

    One prompt per session means one *episode* per session: the same episode
    is returned again until answered (a second tab poll, a dropped SSE poke),
    and a nudge answered with "Ask the AI" may come back as that episode's help
    card. The user chose to continue that exchange; refusing the follow-up for
    a budget they spent by engaging would lose the one answer the nudge was
    for.
    """
    session = (session or "").strip()[:100]
    if not session:
        return None
    async with _state_lock:
        now = _now()
        state = await _load_state(db)
        if await prompt_block_reason(db, state, now):
            return None

        shown = state["sessions_shown"].get(session)
        if shown:
            episode = await db.get_feedback_episode(str(shown.get("episode_id")))
            if episode is None:
                return None
            prompt_type = due_prompt_type(episode, now)
            if not prompt_type or not _allowed_by_budget(episode, prompt_type, state):
                return None
            chosen: tuple[dict[str, Any], str] | None = (episode, prompt_type)
        else:
            chosen = None
            for ep in await _candidates(db, now):
                prompt_type = due_prompt_type(ep, now)
                if prompt_type and _allowed_by_budget(ep, prompt_type, state):
                    chosen = (ep, prompt_type)
                    break
            if chosen is None:
                return None

        episode, prompt_type = chosen
        if episode.get("prompt_type") != prompt_type:
            if prompt_type in _HELP_TYPES and episode.get("prompt_type") not in _HELP_TYPES:
                state["help_cards_shown"] = int(state["help_cards_shown"]) + 1
            await db.update_feedback_episode(
                episode["id"], prompt_type=prompt_type, prompted_at=_iso(now)
            )
            episode.update(prompt_type=prompt_type, prompted_at=_iso(now))
            log.info(
                "feedback_episode",
                step="prompt",
                episode_id=episode["id"],
                prompt_type=prompt_type,
            )
        state["sessions_shown"][session] = {
            "episode_id": episode["id"],
            "at": _iso(now),
        }
        await _save_state(db, state)
        return _prompt_payload(episode, prompt_type)


async def answer(
    db: Database,
    key: str,
    *,
    answer: str,
    comment: str | None = None,
) -> dict[str, Any] | None:
    """Record an answer. Returns the updated episode, or None if unknown.

    Re-answering is allowed (the optional comment arrives as a second call
    after the one-click answer), but the fatigue counters move only when an
    unanswered episode gets its first real answer, so a retry or a follow-up
    comment cannot count one dismissal twice.
    """
    if answer not in ANSWERS:
        raise ValueError(f"unknown answer: {answer}")
    async with _state_lock:
        episode = await db.find_feedback_episode(key)
        if episode is None:
            return None
        now = _now()
        previous = episode.get("answer")
        first_answer = previous in (None, "ask_ai") and answer != previous

        updates: dict[str, Any] = {"answer": answer, "answered_at": _iso(now)}
        if comment is not None and comment.strip():
            updates["comment"] = comment.strip()[:_COMMENT_MAX]
        if answer == "ask_ai" and not episode.get("asked_ai_at"):
            # The client also calls /asked, but this answer alone must be
            # enough to make the follow-up help card due.
            updates["asked_ai_at"] = _iso(now)
        await db.update_feedback_episode(episode["id"], **updates)
        episode.update(updates)

        if first_answer:
            state = await _load_state(db)
            if answer == "dismissed":
                state["consecutive_dismissals"] = int(state["consecutive_dismissals"]) + 1
                if state["consecutive_dismissals"] >= DISMISSALS_TO_SNOOZE:
                    state["snoozed_until"] = _iso(now + SNOOZE)
                    state["consecutive_dismissals"] = 0
                    log.info("feedback_prompts_snoozed", until=state["snoozed_until"])
            else:
                state["consecutive_dismissals"] = 0
            await _save_state(db, state)

        log.info(
            "feedback_episode",
            step="answer",
            episode_id=episode["id"],
            answer=answer,
            prompt_type=episode.get("prompt_type"),
            # Never the text itself: logs leave the pod, the comment is the
            # user's own words.
            has_comment=bool(episode.get("comment")),
        )
        return episode


async def record_giveup(
    db: Database,
    *,
    conversation_id: str | None,
    episode_key: str | None,
    reason: str,
    tag: str,
) -> dict[str, Any]:
    """A give-up tag: on its episode when the chat was scoped to one, else
    standalone in ``feedback_giveups``."""
    if reason not in GIVEUP_REASONS:
        raise ValueError(f"unknown reason: {reason}")
    if tag not in GIVEUP_TAGS:
        raise ValueError(f"unknown tag: {tag}")
    conversation_id = conversation_id[:200] if conversation_id else None
    episode = await db.find_feedback_episode(episode_key) if episode_key else None
    if episode is not None:
        try:
            tags = json.loads(episode.get("tags") or "[]")
            if not isinstance(tags, list):
                tags = []
        except ValueError:
            tags = []
        tags.append(tag)
        updates: dict[str, Any] = {"tags": json.dumps(tags)}
        if conversation_id and not episode.get("conversation_id"):
            updates["conversation_id"] = conversation_id
        await db.update_feedback_episode(episode["id"], **updates)
        log.info(
            "feedback_episode",
            step="giveup",
            episode_id=episode["id"],
            reason=reason,
            tag=tag,
        )
        return {"episode_id": episode["id"], "giveup_id": None}
    giveup_id = await db.add_feedback_giveup(
        conversation_id=conversation_id, episode_id=None, reason=reason, tag=tag
    )
    log.info("feedback_giveup", reason=reason, tag=tag, giveup_id=giveup_id)
    return {"episode_id": None, "giveup_id": giveup_id}
