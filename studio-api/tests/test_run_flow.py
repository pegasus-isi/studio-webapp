"""Tests for the unified run-flow orchestrator (services/run_flow.py).

Phase 1a of the non-specialist redesign (#241). The properties worth pinning
are the ones a user would experience as the feature being broken rather than as
an error: a late subscriber must see the whole flow (that is what makes the
page reattachable), a failure must always carry an actionable message, stages
after a failure must not sit spinning forever, and an up-to-date software
environment must not be rebuilt.
"""

from __future__ import annotations

import asyncio
import os

import pytest

from services import run_flow as rf


# ── failure classification ────────────────────────────────────────────────


def test_classify_names_the_missing_python_package():
    cause, action = rf.classify(
        "generate", "ModuleNotFoundError: No module named 'pandas'"
    )
    assert "pandas" in cause
    assert "pip install pandas" in action


def test_classify_explains_the_glibc_trap_without_blaming_the_definition():
    # The log for this failure points at libfakeroot and mentions nothing about
    # the base image, so an unaided user edits the wrong thing.
    cause, action = rf.classify(
        "build",
        "/lib/x86_64-linux-gnu/libc.so.6: version `GLIBC_2.38' not found\n"
        "FATAL: while running %post section: exit status 1",
    )
    assert "older than this system" in cause
    assert "debian:trixie" in action
    assert "Nothing in your own setup steps" in action


def test_classify_is_scoped_per_stage():
    # "already running" is a submit concern. The same text under generate must
    # not borrow submit's advice — this cross-talk is exactly what the
    # frontend's single-rule-set outputHint() gets wrong on build logs.
    submit_cause, _ = rf.classify("submit", "workflow already running")
    generate_cause, _ = rf.classify("generate", "workflow already running")
    assert "already running" in submit_cause
    assert submit_cause != generate_cause


def test_classify_always_returns_an_actionable_pair():
    for stage in rf.STAGE_ORDER:
        cause, action = rf.classify(stage, "something nobody wrote a rule for")
        assert cause and action, f"{stage} produced an empty failure card"


def test_classify_survives_output_that_matches_nothing_at_all():
    cause, action = rf.classify("generate", "")
    assert cause and action


# ── image staleness ───────────────────────────────────────────────────────


def test_missing_image_is_stale(tmp_path):
    definition = tmp_path / "app.def"
    definition.write_text("Bootstrap: docker")
    assert rf.image_is_stale(str(definition), str(tmp_path / "app.sif")) is True


def test_empty_image_is_stale(tmp_path):
    # A build that failed part-way can leave a zero-byte .sif; treating it as
    # current would hand the workflow an unusable image.
    definition = tmp_path / "app.def"
    definition.write_text("Bootstrap: docker")
    sif = tmp_path / "app.sif"
    sif.write_bytes(b"")
    assert rf.image_is_stale(str(definition), str(sif)) is True


def test_image_newer_than_definition_is_fresh(tmp_path):
    definition = tmp_path / "app.def"
    definition.write_text("Bootstrap: docker")
    sif = tmp_path / "app.sif"
    sif.write_bytes(b"hsqs-ish")
    os.utime(sif, (os.path.getmtime(definition) + 10,) * 2)
    assert rf.image_is_stale(str(definition), str(sif)) is False


def test_image_older_than_definition_is_stale(tmp_path):
    definition = tmp_path / "app.def"
    definition.write_text("Bootstrap: docker")
    sif = tmp_path / "app.sif"
    sif.write_bytes(b"hsqs-ish")
    os.utime(sif, (os.path.getmtime(definition) - 10,) * 2)
    assert rf.image_is_stale(str(definition), str(sif)) is True


# ── flow mechanics ────────────────────────────────────────────────────────


def _flow(stages=("generate", "plan", "submit")) -> rf.RunFlow:
    return rf.RunFlow(
        flow_id="test",
        project_id="proj",
        stages=[rf.Stage(key=k, label=rf.STAGE_LABELS[k]) for k in stages],
    )


def test_failure_marks_later_stages_skipped_not_pending():
    # A stage left "pending" after the flow died renders as a step still to
    # come, so the UI would show a dead flow as making progress.
    flow = _flow()
    flow._begin("generate")
    flow._fail("generate", "boom")

    statuses = {s.key: s.status for s in flow.stages}
    assert statuses == {
        "generate": "failed", "plan": "skipped", "submit": "skipped"
    }
    assert flow.state == "failed"


def test_failure_snapshot_carries_cause_action_and_technical():
    flow = _flow()
    flow._begin("generate")
    flow._fail("generate", "ModuleNotFoundError: No module named 'numpy'")

    failure = flow.snapshot()["failure"]
    assert "numpy" in failure["cause"]
    assert failure["action"]
    # The raw log is kept, never discarded — it just isn't what leads.
    assert "ModuleNotFoundError" in failure["technical"]


def test_late_subscriber_replays_the_whole_flow():
    """A page reopened mid-run must see everything, not just what follows."""

    async def scenario():
        flow = _flow()
        flow._begin("generate")
        flow._finish("generate", "done", "ready")
        flow._begin("plan")

        # Subscribe only now — after three events have already happened.
        events = []
        async for event in flow.subscribe():
            events.append(event)
            if len(events) == 3:
                break
        return events

    events = asyncio.run(scenario())
    assert [e["type"] for e in events] == ["stage", "stage", "stage"]
    assert events[0]["stage"]["key"] == "generate"
    assert events[2]["stage"]["key"] == "plan"


def test_subscriber_receives_events_emitted_after_it_attaches():
    async def scenario():
        flow = _flow()
        received = []

        async def consume():
            async for event in flow.subscribe():
                received.append(event)
                if event.get("type") == "flow":
                    return

        task = asyncio.create_task(consume())
        await asyncio.sleep(0)  # let the subscriber register
        flow._begin("generate")
        flow._succeed()
        flow._terminal()
        await asyncio.wait_for(task, timeout=2)
        return received

    received = asyncio.run(scenario())
    assert received[-1]["type"] == "flow"
    assert received[-1]["state"] == "succeeded"


def test_subscriber_on_finished_flow_replays_then_stops():
    # Reattaching after the run ended must terminate rather than hang waiting
    # for events that will never come.
    async def scenario():
        flow = _flow()
        flow._begin("generate")
        flow._succeed()
        flow._terminal()
        return [e async for e in flow.subscribe()]

    events = asyncio.run(asyncio.wait_for(scenario(), timeout=2))
    assert events[-1]["type"] == "flow"


def test_events_are_sequenced_without_gaps():
    # `seq` is what lets a subscriber skip backlog it already replayed; a gap
    # or duplicate would silently drop or double an event.
    flow = _flow()
    flow._begin("generate")
    flow._line("generate", "hello")
    flow._finish("generate", "done")
    assert [e["seq"] for e in flow._events] == [0, 1, 2]


# ── manager ───────────────────────────────────────────────────────────────


def test_manager_rejects_a_second_concurrent_flow(monkeypatch, tmp_path):
    """Two flows planning into one directory corrupt the run dir."""

    async def scenario():
        manager = rf.RunFlowManager()
        started = asyncio.Event()

        async def never_finishes(flow, **kwargs):
            flow._begin("plan")
            started.set()
            await asyncio.sleep(30)

        monkeypatch.setattr(rf, "_execute", never_finishes)

        kwargs = dict(
            project_id="proj",
            project_dir=str(tmp_path),
            generator_file=None,
            has_container_def=False,
            generator_args=[],
            site="condorpool",
            output_site="local",
        )
        manager.start(**kwargs)
        await started.wait()

        with pytest.raises(RuntimeError, match="already in progress"):
            manager.start(**kwargs)

        flow = manager.active("proj")
        assert flow is not None
        flow._task.cancel()

    asyncio.run(scenario())


def test_manager_omits_stages_the_project_cannot_run(monkeypatch, tmp_path):
    # A project with no generator and no definition should show two steps, not
    # four with two permanently skipped — a step that can never run is noise.
    async def scenario():
        manager = rf.RunFlowManager()

        async def noop(flow, **kwargs):
            await asyncio.sleep(30)

        monkeypatch.setattr(rf, "_execute", noop)
        flow = manager.start(
            project_id="proj",
            project_dir=str(tmp_path),
            generator_file=None,
            has_container_def=False,
            generator_args=[],
            site="condorpool",
            output_site="local",
        )
        assert [s.key for s in flow.stages] == ["plan", "submit"]
        flow._task.cancel()

    asyncio.run(scenario())


def test_manager_returns_none_for_an_unknown_project():
    assert rf.RunFlowManager().get("nope") is None


# ── end-to-end execution ──────────────────────────────────────────────────
#
# These drive the real `_execute` with real subprocesses, so they cover the
# wiring the unit tests above stub out. `pegasus-plan` is absent on a dev
# machine and in CI, which makes the plan stage a genuine failure path to
# assert on rather than something to mock.


def _project(tmp_path, generator_body: str) -> str:
    project = tmp_path / "proj"
    project.mkdir()
    (project / "workflow_generator.py").write_text(generator_body)
    return str(project)


def _drive(project_dir: str, **overrides) -> rf.RunFlow:
    """Run a flow to completion and return it."""

    async def scenario():
        manager = rf.RunFlowManager()
        kwargs = {
            "project_id": "proj",
            "project_dir": project_dir,
            "generator_file": os.path.join(project_dir, "workflow_generator.py"),
            "has_container_def": False,
            "generator_args": [],
            "site": "condorpool",
            "output_site": "local",
            **overrides,
        }
        flow = manager.start(**kwargs)
        await asyncio.wait_for(flow._task, timeout=60)
        return flow

    return asyncio.run(scenario())


def test_generate_failure_stops_the_flow_with_a_usable_message(tmp_path):
    project_dir = _project(tmp_path, "import definitely_not_a_real_module\n")
    flow = _drive(project_dir)

    assert flow.state == "failed"
    snap = flow.snapshot()
    assert snap["failure"]["stage"] == "generate"
    assert "definitely_not_a_real_module" in snap["failure"]["cause"]
    # Plan and submit must not have been attempted.
    assert [s["status"] for s in snap["stages"]] == ["failed", "skipped", "skipped"]


def test_generate_success_advances_to_plan(tmp_path):
    # A generator that writes a real abstract workflow, so _find_workflow_file
    # locates it and the flow genuinely reaches the plan stage.
    project_dir = _project(
        tmp_path,
        "with open('workflow.yml', 'w') as fh:\n"
        "    fh.write('pegasus: \"5.0\"\\njobs: []\\n')\n",
    )
    flow = _drive(project_dir)

    snap = flow.snapshot()
    stages = {s["key"]: s["status"] for s in snap["stages"]}
    assert stages["generate"] == "done"
    # The flow must have moved past generate and reached plan. Whether plan
    # then succeeds depends on whether Pegasus is installed, which differs
    # between a dev machine and CI — so assert only that it was attempted, and
    # that any failure was reported in the user's terms rather than as a
    # traceback.
    assert stages["plan"] != "pending"
    if snap["failure"]:
        assert snap["failure"]["cause"]
        assert snap["failure"]["action"]


def test_flow_always_reaches_a_terminal_event(tmp_path):
    # Whatever happens, the last event must be the terminal one — a client
    # waiting on the stream would otherwise hang forever.
    project_dir = _project(tmp_path, "raise SystemExit(3)\n")
    flow = _drive(project_dir)

    assert flow._events[-1]["type"] == "flow"
    assert flow._events[-1]["state"] == "failed"
    assert flow.state in ("failed", "succeeded")


def test_build_stage_is_skipped_when_the_project_has_no_definition(tmp_path):
    project_dir = _project(tmp_path, "pass\n")
    flow = _drive(project_dir, has_container_def=True)

    # has_container_def was claimed, but find_definition() finds no .def, so
    # the stage resolves to skipped rather than failing the run.
    build = next(s for s in flow.snapshot()["stages"] if s["key"] == "build")
    assert build["status"] == "skipped"
    assert "No software environment" in build["detail"]


# ── endpoints ─────────────────────────────────────────────────────────────


def test_run_flow_snapshot_is_inactive_before_any_run(client):
    res = client.get("/api/workflows/projects/nothing-here/run-flow")
    assert res.status_code == 200
    assert res.json()["active"] is False


def test_start_run_flow_404s_on_unknown_project(client):
    res = client.post(
        "/api/workflows/projects/nope/run-flow", json={"args": []}
    )
    assert res.status_code == 404


def test_run_flow_events_404s_when_nothing_is_running(client):
    res = client.get("/api/workflows/projects/nope/run-flow/events")
    assert res.status_code == 404


def test_plan_uses_the_shared_submit_base(tmp_path, monkeypatch):
    """The GUI plans into the same base the scaffold skill teaches (`--dir
    submit`), so both launchers share one run-number sequence instead of two
    trees with colliding run0002s."""
    from services.workflow_submitter import SUBMIT_BASE

    seen: list[list[str]] = []

    async def fake_plan_capture(flow, argv, *, project_dir, mode=None):
        seen.append(list(argv))
        return 1, "stubbed planner"

    monkeypatch.setattr(rf, "_plan_capture", fake_plan_capture)
    project_dir = _project(
        tmp_path,
        "with open('workflow.yml', 'w') as fh:\n"
        "    fh.write('pegasus: \"5.0\"\\njobs: []\\n')\n",
    )
    _drive(project_dir)

    assert seen, "the planner was never invoked"
    argv = seen[0]
    assert argv[:3] == ["pegasus-plan", "--dir", SUBMIT_BASE]
    assert SUBMIT_BASE == "submit"
