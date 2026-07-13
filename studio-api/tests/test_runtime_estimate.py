"""Tests for the historical-mean remaining-time estimator."""

from __future__ import annotations

import services.runtime_estimate as rte


def _events(job_states):
    """jobs_init + job_state events for [(id, tx, status, duration), ...]."""
    events = [{
        "event_type": "jobs_init",
        "total_jobs": len(job_states),
        "jobs": [
            {"job_id": jid, "exec_job_id": jid, "transformation": tx}
            for jid, tx, _, _ in job_states
        ],
    }]
    for jid, tx, status, duration in job_states:
        ev = {
            "event_type": "job_state",
            "job_id": jid,
            "exec_job_id": jid,
            "transformation": tx,
        }
        if status == "succeeded":
            ev.update(state="POST_SCRIPT_SUCCESS", exitcode=0, duration=duration)
        elif status == "running":
            ev.update(state="EXECUTE")
        elif status == "pending":
            continue  # no job_state event yet
        events.append(ev)
    return events


def test_estimate_uses_per_transformation_means(monkeypatch):
    events = _events([
        ("j1", "align", "succeeded", 100.0),
        ("j2", "align", "succeeded", 200.0),
        ("j3", "align", "pending", None),   # mean(align) = 150
        ("j4", "align", "pending", None),
    ])
    monkeypatch.setattr(rte, "read_events", lambda rd: events)

    est = rte.estimate_remaining("/fake/run")
    assert est["remaining_seconds"] == 300  # 2 × 150, serial (nothing running)
    assert est["remaining_jobs"] == 2
    assert est["parallelism"] == 1
    assert est["confidence"] == "high"
    assert est["coverage"] == 1.0


def test_estimate_divides_by_running_parallelism(monkeypatch):
    events = _events([
        ("j1", "align", "succeeded", 100.0),
        ("j2", "align", "running", None),
        ("j3", "align", "running", None),
    ])
    monkeypatch.setattr(rte, "read_events", lambda rd: events)

    est = rte.estimate_remaining("/fake/run")
    # 2 remaining × 100s, two running → 100s wall-clock.
    assert est["remaining_seconds"] == 100
    assert est["parallelism"] == 2


def test_estimate_falls_back_to_global_mean(monkeypatch):
    events = _events([
        ("j1", "align", "succeeded", 100.0),
        ("j2", "plot", "pending", None),  # no history for plot
    ])
    monkeypatch.setattr(rte, "read_events", lambda rd: events)

    est = rte.estimate_remaining("/fake/run")
    assert est["remaining_seconds"] == 100  # global mean
    assert est["coverage"] == 0.0
    assert est["confidence"] == "low"


def test_estimate_none_before_any_completion(monkeypatch):
    events = _events([
        ("j1", "align", "running", None),
        ("j2", "align", "pending", None),
    ])
    monkeypatch.setattr(rte, "read_events", lambda rd: events)
    assert rte.estimate_remaining("/fake/run") is None


def test_estimate_zero_when_finished(monkeypatch):
    events = _events([("j1", "align", "succeeded", 50.0)])
    monkeypatch.setattr(rte, "read_events", lambda rd: events)

    est = rte.estimate_remaining("/fake/run")
    assert est["remaining_seconds"] == 0
    assert est["remaining_jobs"] == 0


def test_estimate_none_without_events(monkeypatch):
    monkeypatch.setattr(rte, "read_events", lambda rd: [])
    assert rte.estimate_remaining("/fake/run") is None
