"""services/schedd.py: start just-submitted jobs without waiting (#494).

The schedd starts DAGMan (scheduler universe) and Studio's remote-planning
jobs (local universe) from its SCHEDD_INTERVAL timer, 300 s by default, and
absorbs a reschedule that arrives within SCHEDD_MIN_INTERVAL of its last cycle.
"""

from __future__ import annotations

import asyncio

import services.schedd as sd


def _fake_exec(calls, *, returncode=0, raises=None):
    class Proc:
        def __init__(self):
            self.returncode = returncode

        async def communicate(self):
            return b"", b"denied" if returncode else b""

    async def fake(*cmd, **kw):
        calls.append(cmd[0])
        if raises:
            raise raises
        return Proc()

    return fake


def test_nudge_reschedules_now_and_again_after_the_window(monkeypatch):
    """Once now, once past SCHEDD_MIN_INTERVAL: the first alone was absorbed."""
    calls: list[str] = []
    monkeypatch.setattr(sd.asyncio, "create_subprocess_exec", _fake_exec(calls))

    async def main():
        sd.nudge(second_after=0.01)
        await asyncio.sleep(0.1)

    asyncio.run(main())
    assert calls == ["condor_reschedule", "condor_reschedule"]


def test_a_failed_or_missing_reschedule_is_never_an_error(monkeypatch):
    """Best effort: the schedd's timer still starts the job."""
    for fake in (
        _fake_exec([], returncode=1),
        _fake_exec([], raises=FileNotFoundError("condor_reschedule")),
    ):
        monkeypatch.setattr(sd.asyncio, "create_subprocess_exec", fake)
        assert asyncio.run(sd.reschedule()) is False


def test_nudge_outside_an_event_loop_does_nothing():
    sd.nudge()  # must not raise


def test_a_successful_submit_nudges_the_schedd(tmp_path, monkeypatch):
    """pegasus-run returned 0, so DAGMan is queued and should start now."""
    import services.workflow_submitter as ws

    run_dir = tmp_path / "submit" / "run0001"
    run_dir.mkdir(parents=True)
    (run_dir / "braindump.yml").write_text("wf_uuid: x\n")
    nudged: list[bool] = []
    monkeypatch.setattr(ws.schedd, "nudge", lambda *a, **k: nudged.append(True))
    monkeypatch.setattr(ws, "_is_submitted", lambda d: False)

    class Proc:
        returncode = 0

        async def communicate(self):
            return b"Your workflow has been started", b""

    async def fake_exec(*cmd, **kw):
        assert cmd[0] == "pegasus-run"
        return Proc()

    monkeypatch.setattr(ws.asyncio, "create_subprocess_exec", fake_exec)
    result = asyncio.run(ws.run_planned_workflow(str(tmp_path), run_dir=str(run_dir)))
    assert result["status"] == "submitted"
    assert nudged == [True]
