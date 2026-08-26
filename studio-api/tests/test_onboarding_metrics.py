"""Tests for the onboarding funnel (services/onboarding_metrics.py).

The funnel is the baseline for the non-specialist redesign (#241), so the
properties worth protecting are the ones that would silently produce a *wrong
number* rather than an error: milestones must be write-once, implied
predecessors must be backfilled, and a pre-existing workspace must be flagged
so its meaningless timings never enter the baseline.
"""

from __future__ import annotations

import asyncio

import pytest

from db import Database
from services import onboarding_metrics as om


@pytest.fixture
def db(tmp_db):
    """A connected throwaway Database, closed after the test."""
    database = Database(tmp_db)
    asyncio.run(database.connect())
    yield database
    asyncio.run(database.close())


def test_record_stamps_milestone_and_reports_it_as_new(db):
    new = asyncio.run(om.record(db, "first_seen"))
    assert new == ["first_seen"]

    snap = asyncio.run(om.snapshot(db))
    assert snap["milestones"]["first_seen"]
    assert snap["milestones"]["first_success"] is None
    assert snap["complete"] is False


def test_milestones_are_write_once(db):
    asyncio.run(om.record(db, "first_seen"))
    first = asyncio.run(om.snapshot(db))["milestones"]["first_seen"]

    # A second call must not re-stamp: the funnel measures the *first* time.
    assert asyncio.run(om.record(db, "first_seen")) == []
    assert asyncio.run(om.snapshot(db))["milestones"]["first_seen"] == first


def test_later_milestone_backfills_implied_predecessors(db):
    # A run that succeeded proves the user also had an analysis and submitted
    # it, even if no poll ever observed those intermediate states.
    new = asyncio.run(om.record(db, "first_success"))
    assert new == list(om.MILESTONES)

    snap = asyncio.run(om.snapshot(db))
    assert all(snap["milestones"][m] for m in om.MILESTONES)
    assert snap["complete"] is True


def test_seconds_are_measured_from_first_seen_and_omit_unreached(db):
    asyncio.run(om.record(db, "first_seen"))
    asyncio.run(om.record(db, "first_run"))

    snap = asyncio.run(om.snapshot(db))
    seconds = snap["seconds_from_first_seen"]

    # first_seen is the origin, so it carries no elapsed value of its own.
    assert "first_seen" not in seconds
    assert seconds["first_run"] >= 0
    # Not reached yet: absent, never 0 — "not there" must not read as "instant".
    assert "first_success" not in seconds


def test_fresh_workspace_is_not_flagged_preexisting(db):
    asyncio.run(om.record(db, "first_seen"))
    assert asyncio.run(om.snapshot(db))["preexisting_workspace"] is False


def test_workspace_with_prior_runs_is_flagged_preexisting(db):
    # Simulate upgrading into this code: runs already exist from before the
    # instrumentation. Their timings must not enter the cohort baseline.
    asyncio.run(db.upsert_workflow_run(
        run_id="old-run",
        name="old",
        run_dir="/tmp/old-run",
        status="succeeded",
    ))

    asyncio.run(om.record(db, "first_seen"))
    assert asyncio.run(om.snapshot(db))["preexisting_workspace"] is True


def test_unknown_milestone_is_ignored_not_raised(db):
    # Instrumentation must never take down the endpoint it measures.
    assert asyncio.run(om.record(db, "not_a_milestone")) == []
    assert asyncio.run(om.snapshot(db))["milestones"]["first_seen"] is None


def test_observe_translates_workspace_state(db):
    asyncio.run(om.observe(db, has_projects=True))
    snap = asyncio.run(om.snapshot(db))
    assert snap["milestones"]["first_workflow"]
    assert snap["milestones"]["first_run"] is None

    asyncio.run(om.observe(db, has_projects=True, has_runs=True, has_success=True))
    assert asyncio.run(om.snapshot(db))["complete"] is True


def test_snapshot_on_untouched_workspace_is_empty_not_error(db):
    snap = asyncio.run(om.snapshot(db))
    assert snap["milestones"] == {m: None for m in om.MILESTONES}
    assert snap["seconds_from_first_seen"] == {}
    assert snap["complete"] is False


def test_onboarding_endpoint_serves_the_snapshot(client):
    res = client.get("/api/dashboard/onboarding")
    assert res.status_code == 200
    body = res.json()
    assert set(body["milestones"]) == set(om.MILESTONES)
    assert "preexisting_workspace" in body
    # Startup stamps first_seen, so the endpoint reflects a live funnel.
    assert body["milestones"]["first_seen"]
