"""AI-help feedback episodes: lifecycle, prompt timing and the fatigue rules.

No Pegasus or Condor: the scanner and status reads are replaced the same way
tests/test_notifications.py does it, and time is a clock the test moves.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone

import pytest

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kw: float) -> None:
        self.now = self.now + timedelta(**kw)


@pytest.fixture
def fe(monkeypatch):
    """The service with a controllable clock and no stampede reads."""
    from services import feedback_episodes

    clock = Clock()
    monkeypatch.setattr(feedback_episodes, "_now", clock)

    async def category(run_dir):
        return "out_of_memory"

    monkeypatch.setattr(feedback_episodes, "_failure_category", category)
    for name in (
        "STUDIO_FEEDBACK_NUDGE_SECONDS",
        "STUDIO_FEEDBACK_HELP_TIMEOUT_SECONDS",
        "STUDIO_FEEDBACK_PROMPTS",
    ):
        monkeypatch.delenv(name, raising=False)
    feedback_episodes.clock = clock  # type: ignore[attr-defined]
    return feedback_episodes


def make_run(tmp_path, name: str, uuid: str, workflow: str, planned: datetime):
    """A submit dir with a braindump planned at `planned`."""
    run_dir = tmp_path / name
    run_dir.mkdir(parents=True, exist_ok=True)
    bd = run_dir / "braindump.yml"
    bd.write_text(f"wf_uuid: {uuid}\npegasus_wf_name: {workflow}\n")
    ts = planned.timestamp()
    os.utime(bd, (ts, ts))
    return str(run_dir)


def obs(run_dir, run_id, status, prev, workflow="streamflow"):
    return {
        "run_id": run_id,
        "run_dir": run_dir,
        "workflow": workflow,
        "status": status,
        "prev_status": prev,
    }


def run(coro_fn, tmp_path):
    """Run `coro_fn(db)` against a fresh database."""
    from db import Database

    async def go():
        db = Database(str(tmp_path / "fb.db"))
        await db.connect()
        try:
            return await coro_fn(db)
        finally:
            await db.close()

    return asyncio.run(go())


async def fail_run(fe, db, run_dir, run_id="uuid-1", workflow="streamflow"):
    """Observe `run_dir` going running -> failed; return its episode."""
    await fe.process_scan(
        db, [obs(run_dir, run_id, "failed", "running", workflow)], seed_only=False
    )
    episode = await db.find_feedback_episode(run_id)
    assert episode is not None
    return episode


# --- lifecycle -----------------------------------------------------------------


def test_full_episode_fail_ask_rerun_close_card(fe, tmp_path, monkeypatch):
    """fail -> ask -> rerun -> close -> "did it fix it?" card, driven through
    the real notification engine scan with the scanner mocked."""
    import services.notification_engine as ne

    run1 = make_run(tmp_path, "run0001", "uuid-1", "streamflow", T0 - timedelta(hours=1))
    runs = [{"run_dir": run1, "run_id": "uuid-1", "run_name": "run0001", "name": "streamflow"}]
    status = {run1: "running"}

    async def discover(*a, **k):
        return list(runs)

    async def detail(run_dir):
        return {"status": status[run_dir], "failed_jobs": 0, "total_jobs": 3}

    monkeypatch.setattr(ne, "discover_runs_async", discover)
    monkeypatch.setattr(ne, "get_workflow_detail", detail)

    async def scenario(db):
        eng = ne.NotificationEngine()
        eng._db = db
        eng._workspace_root = str(tmp_path)
        q = eng.subscribe()

        await eng._scan(seed_only=True)
        status[run1] = "failed"
        await eng._scan(seed_only=False)

        ep = await db.find_feedback_episode("uuid-1")
        assert ep["workflow"] == "streamflow"
        assert ep["failure_category"] == "out_of_memory"
        assert ep["failed_at"] == T0.isoformat()
        assert ep["closed_at"] is None

        fe.clock.advance(minutes=2)
        await fe.mark_asked(db, "uuid-1", conversation_id="ses_1", turn=False)
        await fe.mark_asked(db, "uuid-1", conversation_id="ses_1", turn=True)

        # The re-run: same workflow, new submit dir, planned after the failure.
        fe.clock.advance(minutes=3)
        run2 = make_run(tmp_path, "run0002", "uuid-2", "streamflow", fe.clock.now)
        runs.append({"run_dir": run2, "run_id": "uuid-2", "run_name": "run0002", "name": "streamflow"})
        status[run2] = "running"
        await eng._scan(seed_only=False)
        ep = await db.find_feedback_episode("uuid-1")
        assert ep["rerun_id"] == "uuid-2"
        assert ep["closed_at"] is None
        # Re-run in flight: no card yet, even past the no-rerun timeout.
        fe.clock.advance(minutes=20)
        assert await fe.pending(db, "tab-a") is None

        status[run2] = "succeeded"
        await eng._scan(seed_only=False)
        ep = await db.find_feedback_episode("uuid-1")
        assert ep["rerun_status"] == "succeeded"
        assert ep["closed_at"] is not None
        assert ep["asked_ai_at"] is not None
        assert ep["ai_turns"] == 1
        assert ep["conversation_id"] == "ses_1"

        prompt = await fe.pending(db, "tab-a")
        assert prompt["prompt_type"] == "help"
        assert prompt["kind"] == "help"
        assert prompt["message"] == (
            "Your re-run of streamflow succeeded. Did the AI's suggestion fix it?"
        )

        # The browsers were poked about it, and the poke never reached the
        # notification history (the bell).
        kinds = []
        while not q.empty():
            kinds.append(q.get_nowait())
        pokes = [n for n in kinds if n["kind"] == "feedback_prompt"]
        assert pokes and pokes[-1]["meta"] == {
            "episode_id": ep["id"], "prompt_type": "help"
        }
        assert all(n["kind"] != "feedback_prompt" for n in await db.list_notifications())

        # A second scan does not poke again for the same prompt.
        await eng._scan(seed_only=False)
        assert all(q.get_nowait()["kind"] != "feedback_prompt" for _ in range(q.qsize()))

        await fe.answer(db, ep["id"], answer="fixed")
        assert await fe.pending(db, "tab-a") is None

    run(scenario, tmp_path)


def test_seed_scan_opens_nothing(fe, tmp_path):
    """Old failures seen at startup are not episodes — or every restart would
    nudge about every failed run in the workspace."""
    run1 = make_run(tmp_path, "run0001", "uuid-1", "streamflow", T0)

    async def scenario(db):
        await fe.process_scan(db, [obs(run1, "uuid-1", "failed", None)], seed_only=True)
        assert await db.list_feedback_episodes() == []

    run(scenario, tmp_path)


def test_first_sighting_failed_opens_only_if_fresh(fe, tmp_path):
    fresh = make_run(tmp_path, "fresh", "uuid-f", "wf", T0 - timedelta(minutes=5))
    old = make_run(tmp_path, "old", "uuid-o", "wf", T0 - timedelta(days=3))

    async def scenario(db):
        await fe.process_scan(
            db,
            [obs(fresh, "uuid-f", "failed", None, "wf"), obs(old, "uuid-o", "failed", None, "wf")],
            seed_only=False,
        )
        eps = await db.list_feedback_episodes()
        assert [e["run_id"] for e in eps] == ["uuid-f"]

    run(scenario, tmp_path)


def test_rescue_rerun_in_place(fe, tmp_path):
    """pegasus-run on the failed dir resubmits in place: same dir, back to
    running, then terminal."""
    run1 = make_run(tmp_path, "run0001", "uuid-1", "wf", T0 - timedelta(hours=1))

    async def scenario(db):
        ep = await fail_run(fe, db, run1, workflow="wf")
        fe.clock.advance(minutes=1)
        await fe.process_scan(db, [obs(run1, "uuid-1", "running", "failed", "wf")], seed_only=False)
        ep = await db.get_feedback_episode(ep["id"])
        assert ep["rerun_id"] == "uuid-1" and ep["rerun_dir"] == run1
        await fe.process_scan(db, [obs(run1, "uuid-1", "succeeded", "running", "wf")], seed_only=False)
        ep = await db.get_feedback_episode(ep["id"])
        assert ep["rerun_status"] == "succeeded" and ep["closed_at"]

    run(scenario, tmp_path)


def test_rerun_that_fails_closes_and_opens_a_new_episode(fe, tmp_path):
    run1 = make_run(tmp_path, "run0001", "uuid-1", "wf", T0 - timedelta(hours=1))

    async def scenario(db):
        ep1 = await fail_run(fe, db, run1, workflow="wf")
        fe.clock.advance(minutes=1)
        run2 = make_run(tmp_path, "run0002", "uuid-2", "wf", fe.clock.now)
        await fe.process_scan(
            db,
            [obs(run1, "uuid-1", "failed", "failed", "wf"), obs(run2, "uuid-2", "running", None, "wf")],
            seed_only=False,
        )
        await fe.process_scan(
            db,
            [obs(run1, "uuid-1", "failed", "failed", "wf"), obs(run2, "uuid-2", "failed", "running", "wf")],
            seed_only=False,
        )
        ep1 = await db.get_feedback_episode(ep1["id"])
        assert ep1["rerun_status"] == "failed" and ep1["closed_at"]
        ep2 = await db.find_feedback_episode("uuid-2")
        assert ep2 is not None and ep2["id"] != ep1["id"] and ep2["closed_at"] is None

    run(scenario, tmp_path)


def test_sibling_planned_before_failure_is_not_a_rerun(fe, tmp_path):
    """A parameter sweep's other run, already going when this one failed, is
    not the user re-running it."""
    run1 = make_run(tmp_path, "run0001", "uuid-1", "wf", T0 - timedelta(hours=1))
    sibling = make_run(tmp_path, "run0002", "uuid-2", "wf", T0 - timedelta(minutes=30))

    async def scenario(db):
        ep = await fail_run(fe, db, run1, workflow="wf")
        await fe.process_scan(db, [obs(sibling, "uuid-2", "running", "running", "wf")], seed_only=False)
        assert (await db.get_feedback_episode(ep["id"]))["rerun_id"] is None

    run(scenario, tmp_path)


def test_planned_but_unsubmitted_run_is_not_a_rerun(fe, tmp_path):
    """Plan without Run must not park the episode waiting on a result that
    may never come; it links once the run is actually going."""
    run1 = make_run(tmp_path, "run0001", "uuid-1", "wf", T0 - timedelta(hours=1))

    async def scenario(db):
        ep = await fail_run(fe, db, run1, workflow="wf")
        fe.clock.advance(minutes=1)
        run2 = make_run(tmp_path, "run0002", "uuid-2", "wf", fe.clock.now)
        await fe.process_scan(db, [obs(run2, "uuid-2", "planned", None, "wf")], seed_only=False)
        assert (await db.get_feedback_episode(ep["id"]))["rerun_id"] is None
        await fe.process_scan(db, [obs(run2, "uuid-2", "running", "planned", "wf")], seed_only=False)
        assert (await db.get_feedback_episode(ep["id"]))["rerun_id"] == "uuid-2"

    run(scenario, tmp_path)


def test_observe_submit_links_rerun(fe, tmp_path):
    run1 = make_run(tmp_path, "run0001", "uuid-1", "wf", T0 - timedelta(hours=1))

    async def scenario(db):
        ep = await fail_run(fe, db, run1, workflow="wf")
        other = make_run(tmp_path, "other", "uuid-x", "different", T0)
        await fe.observe_submit(db, other)
        assert (await db.get_feedback_episode(ep["id"]))["rerun_id"] is None

        fe.clock.advance(minutes=4)
        run2 = make_run(tmp_path, "run0002", "uuid-2", "wf", fe.clock.now)
        await fe.observe_submit(db, run2)
        ep = await db.get_feedback_episode(ep["id"])
        assert ep["rerun_id"] == "uuid-2"
        assert ep["rerun_submitted_at"] == fe.clock.now.isoformat()

    run(scenario, tmp_path)


# --- timing ------------------------------------------------------------------------


def test_nudge_after_ten_minutes_without_asking(fe, tmp_path):
    run1 = make_run(tmp_path, "run0001", "uuid-1", "streamflow", T0 - timedelta(hours=1))

    async def scenario(db):
        await fail_run(fe, db, run1)
        fe.clock.advance(minutes=9, seconds=59)
        assert await fe.pending(db, "s") is None
        fe.clock.advance(seconds=1)
        prompt = await fe.pending(db, "s")
        assert prompt["prompt_type"] == "nudge"
        assert prompt["message"] == "streamflow failed. Want the AI to look?"

    run(scenario, tmp_path)


def test_no_nudge_when_asked_or_rerun(fe, tmp_path):
    run1 = make_run(tmp_path, "run0001", "uuid-1", "a", T0 - timedelta(hours=1))
    run2 = make_run(tmp_path, "run0002", "uuid-2", "b", T0 - timedelta(hours=1))

    async def scenario(db):
        await fail_run(fe, db, run1, "uuid-1", "a")
        await fail_run(fe, db, run2, "uuid-2", "b")
        await fe.mark_asked(db, "uuid-1", conversation_id=None, turn=True)
        fe.clock.advance(minutes=1)
        await fe.observe_submit(db, make_run(tmp_path, "run0003", "uuid-3", "b", fe.clock.now))
        fe.clock.advance(minutes=12)
        # uuid-1 was asked (help card not due for 15 min), uuid-2 was re-run.
        assert await fe.due_prompts(db) == []

    run(scenario, tmp_path)


def test_help_card_after_timeout_without_rerun(fe, tmp_path):
    run1 = make_run(tmp_path, "run0001", "uuid-1", "streamflow", T0 - timedelta(hours=1))

    async def scenario(db):
        await fail_run(fe, db, run1)
        await fe.mark_asked(db, "uuid-1", conversation_id="c", turn=True)
        fe.clock.advance(minutes=14)
        assert await fe.pending(db, "s") is None
        fe.clock.advance(minutes=1)
        prompt = await fe.pending(db, "s")
        assert prompt["prompt_type"] == "help"
        assert prompt["message"] == (
            "You asked the AI about streamflow failing. Did its suggestion help?"
        )

    run(scenario, tmp_path)


def test_env_timings_shorten_the_waits(fe, tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_FEEDBACK_NUDGE_SECONDS", "30")
    monkeypatch.setenv("STUDIO_FEEDBACK_HELP_TIMEOUT_SECONDS", "60")
    run1 = make_run(tmp_path, "run0001", "uuid-1", "a", T0 - timedelta(hours=1))
    run2 = make_run(tmp_path, "run0002", "uuid-2", "b", T0 - timedelta(hours=1))

    async def scenario(db):
        await fail_run(fe, db, run1, "uuid-1", "a")
        await fail_run(fe, db, run2, "uuid-2", "b")
        await fe.mark_asked(db, "uuid-2", conversation_id=None, turn=True)
        fe.clock.advance(seconds=30)
        assert [t for _, t in await fe.due_prompts(db)] == ["nudge"]
        fe.clock.advance(seconds=30)
        assert sorted(t for _, t in await fe.due_prompts(db)) == ["help", "nudge"]

    run(scenario, tmp_path)


def test_bad_env_value_falls_back(fe, monkeypatch):
    monkeypatch.setenv("STUDIO_FEEDBACK_NUDGE_SECONDS", "ten minutes")
    assert fe.nudge_seconds() == 600


def test_stale_prompts_expire(fe, tmp_path):
    run1 = make_run(tmp_path, "run0001", "uuid-1", "a", T0 - timedelta(hours=1))

    async def scenario(db):
        await fail_run(fe, db, run1, "uuid-1", "a")
        fe.clock.advance(days=2)
        assert await fe.pending(db, "s") is None

    run(scenario, tmp_path)


# --- fatigue rules -----------------------------------------------------------------


def test_one_prompt_per_session(fe, tmp_path):
    run1 = make_run(tmp_path, "run0001", "uuid-1", "a", T0 - timedelta(hours=1))
    run2 = make_run(tmp_path, "run0002", "uuid-2", "b", T0 - timedelta(hours=1))

    async def scenario(db):
        await fail_run(fe, db, run1, "uuid-1", "a")
        await fail_run(fe, db, run2, "uuid-2", "b")
        fe.clock.advance(minutes=11)

        first = await fe.pending(db, "tab-a")
        assert first is not None
        # Asking again in the same session returns the same prompt (a poll and
        # an SSE poke can both ask), never a second one.
        again = await fe.pending(db, "tab-a")
        assert again["episode_id"] == first["episode_id"]

        await fe.answer(db, first["episode_id"], answer="fixed_myself")
        assert await fe.pending(db, "tab-a") is None

        other = await fe.pending(db, "tab-b")
        assert other is not None and other["episode_id"] != first["episode_id"]

    run(scenario, tmp_path)


def test_help_cards_only_for_first_three_episodes(fe, tmp_path):
    async def scenario(db):
        ids = []
        for i in range(4):
            d = make_run(tmp_path, f"run{i}", f"uuid-{i}", f"wf{i}", T0 - timedelta(hours=1))
            ep = await fail_run(fe, db, d, f"uuid-{i}", f"wf{i}")
            await fe.mark_asked(db, ep["id"], conversation_id=None, turn=True)
            ids.append(ep["id"])
        fe.clock.advance(minutes=16)

        shown = []
        for i in range(4):
            prompt = await fe.pending(db, f"tab-{i}")
            if prompt:
                shown.append(prompt["episode_id"])
                await fe.answer(db, prompt["episode_id"], answer="partly")
        assert len(shown) == 3
        # The fourth is recorded silently: still unanswered, never prompted.
        silent = [i for i in ids if i not in shown]
        ep = await db.get_feedback_episode(silent[0])
        assert ep["prompt_type"] is None and ep["answer"] is None
        assert ep["asked_ai_at"] is not None

        state = await db.get_setting("feedback_prompt_state")
        assert state["help_cards_shown"] == 3

    run(scenario, tmp_path)


def test_two_consecutive_dismissals_snooze_for_a_week(fe, tmp_path):
    async def scenario(db):
        async def due_nudge(name):
            d = make_run(tmp_path, name, f"uuid-{name}", name, fe.clock.now - timedelta(hours=1))
            await fail_run(fe, db, d, f"uuid-{name}", name)
            fe.clock.advance(minutes=11)

        await due_nudge("a")
        p = await fe.pending(db, "s1")
        await fe.answer(db, p["episode_id"], answer="dismissed")
        # A retried dismissal of the same episode does not count twice.
        await fe.answer(db, p["episode_id"], answer="dismissed")
        state = await db.get_setting("feedback_prompt_state")
        assert state["consecutive_dismissals"] == 1 and state["snoozed_until"] is None

        await due_nudge("b")
        p = await fe.pending(db, "s2")
        await fe.answer(db, p["episode_id"], answer="dismissed")
        state = await db.get_setting("feedback_prompt_state")
        assert state["snoozed_until"] == (fe.clock.now + timedelta(days=7)).isoformat()

        await due_nudge("c")
        assert await fe.pending(db, "s3") is None
        assert await fe.prompt_block_reason(db) == "snoozed"

        fe.clock.advance(days=7)
        await due_nudge("d")
        assert (await fe.pending(db, "s4"))["prompt_type"] == "nudge"

    run(scenario, tmp_path)


def test_a_real_answer_resets_the_dismissal_streak(fe, tmp_path):
    async def scenario(db):
        for name, ans in (("a", "dismissed"), ("b", "fixed_myself"), ("c", "dismissed")):
            d = make_run(tmp_path, name, f"uuid-{name}", name, fe.clock.now - timedelta(hours=1))
            await fail_run(fe, db, d, f"uuid-{name}", name)
            fe.clock.advance(minutes=11)
            p = await fe.pending(db, f"s-{name}")
            await fe.answer(db, p["episode_id"], answer=ans)
        state = await db.get_setting("feedback_prompt_state")
        assert state["consecutive_dismissals"] == 1 and state["snoozed_until"] is None

    run(scenario, tmp_path)


def test_muted_feedback_kind_never_prompts(fe, tmp_path):
    run1 = make_run(tmp_path, "run0001", "uuid-1", "a", T0 - timedelta(hours=1))

    async def scenario(db):
        await db.set_setting("muted_notification_kinds", ["feedback"])
        await fail_run(fe, db, run1, "uuid-1", "a")
        fe.clock.advance(minutes=11)
        assert await fe.pending(db, "s") is None
        assert await fe.due_prompts(db) == []
        # Still recorded.
        assert await db.find_feedback_episode("uuid-1") is not None

    run(scenario, tmp_path)


def test_env_disables_prompts_but_keeps_recording(fe, tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_FEEDBACK_PROMPTS", "0")
    run1 = make_run(tmp_path, "run0001", "uuid-1", "a", T0 - timedelta(hours=1))

    async def scenario(db):
        await fail_run(fe, db, run1, "uuid-1", "a")
        await fe.mark_asked(db, "uuid-1", conversation_id=None, turn=True)
        fe.clock.advance(minutes=20)
        assert await fe.pending(db, "s") is None
        assert await fe.prompt_block_reason(db) == "disabled"
        ep = await db.find_feedback_episode("uuid-1")
        assert ep["asked_ai_at"] and ep["ai_turns"] == 1

    run(scenario, tmp_path)


def test_nudge_ask_then_help_card_in_same_session(fe, tmp_path):
    """Choosing "Ask the AI" on a nudge continues that episode: its help card
    may follow in the same session, and is typed so the nudge stays visible."""
    run1 = make_run(tmp_path, "run0001", "uuid-1", "streamflow", T0 - timedelta(hours=1))

    async def scenario(db):
        await fail_run(fe, db, run1)
        fe.clock.advance(minutes=10)
        p = await fe.pending(db, "tab")
        assert p["prompt_type"] == "nudge"
        await fe.answer(db, p["episode_id"], answer="ask_ai")
        ep = await db.get_feedback_episode(p["episode_id"])
        assert ep["asked_ai_at"] == fe.clock.now.isoformat()

        fe.clock.advance(minutes=15)
        p2 = await fe.pending(db, "tab")
        assert p2["episode_id"] == p["episode_id"]
        assert p2["prompt_type"] == "help_after_nudge"
        await fe.answer(db, p2["episode_id"], answer="partly", comment="  needed the log  ")
        ep = await db.get_feedback_episode(p["episode_id"])
        assert ep["answer"] == "partly"
        assert ep["prompt_type"] == "help_after_nudge"
        assert ep["comment"] == "needed the log"

    run(scenario, tmp_path)


def test_asking_after_close_does_not_make_a_card_due(fe, tmp_path):
    run1 = make_run(tmp_path, "run0001", "uuid-1", "wf", T0 - timedelta(hours=1))

    async def scenario(db):
        ep = await fail_run(fe, db, run1, workflow="wf")
        await fe.process_scan(db, [obs(run1, "uuid-1", "succeeded", "failed", "wf")], seed_only=False)
        await fe.mark_asked(db, ep["id"], conversation_id=None, turn=True)
        ep = await db.get_feedback_episode(ep["id"])
        assert ep["asked_ai_at"] is None and ep["ai_turns"] == 1
        fe.clock.advance(minutes=30)
        assert await fe.due_prompts(db) == []

    run(scenario, tmp_path)


# --- run_diagnosis category --------------------------------------------------------


def test_categorize_jobs():
    from services import run_diagnosis

    assert run_diagnosis.categorize_jobs([]) == "no_failed_job"
    assert (
        run_diagnosis.categorize_jobs(
            [{"status": "failed", "exitcode": 1, "stderr": "ModuleNotFoundError: No module named 'xarray'"}]
        )
        == "missing_python_package"
    )
    assert run_diagnosis.categorize_jobs([{"status": "failed", "exitcode": 137}]) == "out_of_memory"
    assert run_diagnosis.categorize_jobs([{"status": "failed", "stderr": "??"}]) == "unknown"


# --- endpoints ---------------------------------------------------------------------


def _seed(tmp_db, fn):
    """Write through a second connection to the client's DB file, as
    tests/test_chat.py does."""
    from db import Database

    async def go():
        db = Database(db_path=tmp_db)
        await db.connect()
        try:
            return await fn(db)
        finally:
            await db.close()

    return asyncio.run(go())


def test_endpoints_roundtrip(client, tmp_db, monkeypatch):
    from services import feedback_episodes

    async def no_category(run_dir):
        return None

    monkeypatch.setattr(feedback_episodes, "_failure_category", no_category)

    async def seed(db):
        # A failed run the watch loop never opened an episode for.
        await db.upsert_workflow_run(
            run_id="uuid-9", name="streamflow", run_dir="/tmp/nowhere/run0009",
            status="failed",
        )
        await db.upsert_workflow_run(
            run_id="uuid-ok", name="streamflow", run_dir="/tmp/nowhere/run0010",
            status="succeeded",
        )

    _seed(tmp_db, seed)

    # Asking about a succeeded run, or an unknown one, links nothing.
    assert client.post("/api/feedback/episodes/uuid-ok/asked", json={}).status_code == 404
    assert client.post("/api/feedback/episodes/nope/asked", json={}).status_code == 404

    res = client.post(
        "/api/feedback/episodes/uuid-9/asked",
        json={"conversation_id": "ses_abc", "turn": False},
    )
    assert res.status_code == 200
    body = res.json()
    episode_id = body["episode_id"]
    assert body["asked_ai_at"] and body["ai_turns"] == 0

    res = client.post(
        f"/api/feedback/episodes/{episode_id}/asked", json={"turn": True}
    )
    assert res.json()["ai_turns"] == 1

    pending = client.get("/api/feedback/episodes/pending?session=tab-1").json()
    assert pending["prompts_enabled"] is True
    assert pending["prompt"] is None  # the help timeout has not passed
    assert client.get("/api/feedback/episodes/pending").status_code == 422

    # Validation.
    assert client.post(
        f"/api/feedback/episodes/{episode_id}/answer", json={"answer": "great"}
    ).status_code == 422
    assert client.post(
        "/api/feedback/episodes/ep_missing/answer", json={"answer": "fixed"}
    ).status_code == 404

    res = client.post(
        "/api/feedback/episodes/uuid-9/answer",
        json={"answer": "no_help", "comment": "it never read the log", "session": "tab-1"},
    )
    assert res.status_code == 200 and res.json()["answer"] == "no_help"

    # Give-ups: linked by run id, and standalone.
    res = client.post(
        "/api/feedback/episodes/giveup",
        json={"episode_id": "uuid-9", "reason": "stop", "tag": "too_slow", "conversation_id": "ses_abc"},
    )
    assert res.json()["episode_id"] == episode_id
    res = client.post(
        "/api/feedback/episodes/giveup",
        json={"reason": "error", "tag": "other", "conversation_id": "ses_x"},
    )
    assert res.json()["episode_id"] is None and res.json()["giveup_id"]
    assert client.post(
        "/api/feedback/episodes/giveup", json={"reason": "bored", "tag": "other"}
    ).status_code == 422

    async def check(db):
        ep = await db.get_feedback_episode(episode_id)
        rows = []
        async with db.db.execute("SELECT * FROM feedback_giveups") as cur:
            rows = [dict(r) for r in await cur.fetchall()]
        return ep, rows

    ep, rows = _seed(tmp_db, check)
    assert ep["workflow"] == "streamflow"
    assert ep["answer"] == "no_help"
    assert ep["comment"] == "it never read the log"
    assert ep["conversation_id"] == "ses_abc"
    assert ep["tags"] == '["too_slow"]'
    assert len(rows) == 1
    assert rows[0]["reason"] == "error" and rows[0]["conversation_id"] == "ses_x"


def test_pending_reports_disabled(client, monkeypatch):
    monkeypatch.setenv("STUDIO_FEEDBACK_PROMPTS", "0")
    body = client.get("/api/feedback/episodes/pending?session=x").json()
    assert body == {"prompt": None, "prompts_enabled": False}


def test_pending_honours_mute_via_prefs_endpoint(client):
    client.put("/api/notifications/prefs", json={"muted": ["feedback"]})
    body = client.get("/api/feedback/episodes/pending?session=x").json()
    assert body["prompts_enabled"] is False
