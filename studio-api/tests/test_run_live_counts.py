"""Run header counts: running vs queued, and the live stream's source (#641)."""

from __future__ import annotations

import asyncio
import json

from services import event_reader, workflow_monitor


def _events(states: dict[str, str | None], exitcodes: dict[str, int] | None = None):
    exitcodes = exitcodes or {}
    evs = [{"event_type": "jobs_init", "total_jobs": len(states),
            "jobs": [{"job_id": j} for j in states]}]
    for j, st in states.items():
        if st is None:
            continue
        ev = {"event_type": "job_state", "job_id": j, "state": st}
        if j in exitcodes:
            ev["exitcode"] = exitcodes[j]
        evs.append(ev)
    return evs


def test_queued_steps_are_not_counted_as_running():
    detail = event_reader.workflow_detail(_events({
        "a": "JOB_SUCCESS",       # done
        "b": "EXECUTE",           # on a worker
        "c": "SUBMIT",            # in the HTCondor queue
        "d": "JOB_HELD",          # held
        "e": None,                # not yet released by DAGMan
    }, {"a": 0}))
    assert detail["completed_jobs"] == 1
    assert detail["running_jobs"] == 1
    assert detail["queued_jobs"] == 3


def test_running_plus_queued_is_every_unfinished_step():
    detail = event_reader.workflow_detail(_events({
        "a": "EXECUTE", "b": "EXECUTE", "c": "JOB_FAILURE", "d": "SUBMIT",
    }, {"c": 1}))
    unfinished = detail["total_jobs"] - detail["completed_jobs"] - detail["failed_jobs"]
    assert detail["running_jobs"] + detail["queued_jobs"] == unfinished == 3
    assert detail["running_jobs"] == 2


def test_stream_sends_counts_even_when_workflow_events_jsonl_exists(tmp_path, monkeypatch):
    # The old path tailed this file from its end and forwarded raw per-job
    # events with no counts, so the header's "running" read 0.
    run_dir = tmp_path / "run0001"
    run_dir.mkdir()
    (run_dir / "workflow-events.jsonl").write_text(
        "\n".join(json.dumps(e) for e in _events({"a": "EXECUTE", "b": "SUBMIT"})) + "\n"
    )

    async def fake_status(_run_dir):
        return "running"

    monkeypatch.setattr(workflow_monitor, "dagman_exit_status", lambda _d: None)
    monkeypatch.setattr(workflow_monitor, "get_run_status_async", fake_status)

    async def first_event():
        agen = workflow_monitor.tail_workflow_events(str(run_dir))
        try:
            return await agen.__anext__()
        finally:
            await agen.aclose()

    first = asyncio.run(first_event())
    assert first["type"] == "workflow_state"
    assert first["total_jobs"] == 2
    assert first["running_jobs"] == 1
    assert first["queued_jobs"] == 1
