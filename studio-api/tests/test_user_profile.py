"""The four-question profile (early-user feedback program).

What these defend: every answer is optional, unknown options are refused with
a readable message rather than stored, `answered_at` is the server's clock and
not the client's, and the free-text research field never reaches the log.
"""

from __future__ import annotations

import json

import pytest


def test_unanswered_profile_is_all_none(client):
    res = client.get("/api/settings/profile")
    assert res.status_code == 200
    assert res.json() == {
        "research_field": None,
        "role": None,
        "pegasus_experience": None,
        "coding_comfort": None,
        "answered_at": None,
    }


def test_round_trip_stamps_answered_at(client):
    payload = {
        "research_field": "  hydrology  ",
        "role": "postdoc",
        "pegasus_experience": "tried",
        "coding_comfort": 4,
    }
    saved = client.put("/api/settings/profile", json=payload)
    assert saved.status_code == 200
    body = saved.json()
    assert body["research_field"] == "hydrology"
    assert body["role"] == "postdoc"
    assert body["pegasus_experience"] == "tried"
    assert body["coding_comfort"] == 4
    assert body["answered_at"]

    assert client.get("/api/settings/profile").json() == body


def test_all_blank_still_counts_as_answered(client):
    """Skipping every question is an answer — the tour must not ask again."""
    body = client.put("/api/settings/profile", json={"research_field": "   "}).json()
    assert body["research_field"] is None
    assert body["answered_at"] is not None


def test_client_cannot_backdate_answered_at(client):
    body = client.put(
        "/api/settings/profile", json={"answered_at": "1999-01-01T00:00:00"}
    ).json()
    assert not body["answered_at"].startswith("1999")


@pytest.mark.parametrize(
    "payload",
    [
        {"role": "astronaut"},
        {"pegasus_experience": "expert"},
        {"coding_comfort": 0},
        {"coding_comfort": 6},
        {"research_field": "x" * 201},
    ],
)
def test_invalid_answers_are_refused_with_a_message(client, payload):
    res = client.put("/api/settings/profile", json=payload)
    assert res.status_code == 400
    assert isinstance(res.json()["detail"], str)
    # Nothing was stored.
    assert client.get("/api/settings/profile").json()["answered_at"] is None


def test_stale_stored_field_is_dropped_not_fatal(client):
    """An option renamed between releases loses that answer, not the profile."""
    import asyncio

    import main

    async def seed() -> None:
        await main.db.set_setting(
            "user_profile",
            {"role": "retired_option", "coding_comfort": 3, "answered_at": "t"},
        )

    asyncio.run(seed())
    body = client.get("/api/settings/profile").json()
    assert body["role"] is None
    assert body["coding_comfort"] == 3
    assert body["answered_at"] == "t"


def test_profile_is_its_own_row_not_a_ui_preference(client):
    """A wholesale UI-preferences save must not touch the profile."""
    client.put("/api/settings/profile", json={"role": "faculty"})
    client.put("/api/settings/ui", json={"simple_run": False})
    assert client.get("/api/settings/profile").json()["role"] == "faculty"


def test_log_event_carries_choices_but_no_free_text(client):
    from structlog.testing import capture_logs

    with capture_logs() as logs:
        client.put(
            "/api/settings/profile",
            json={
                "research_field": "secret lab name",
                "role": "faculty",
                "pegasus_experience": "regular",
                "coding_comfort": 5,
            },
        )
    events = [e for e in logs if e.get("event") == "user_profile_set"]
    assert len(events) == 1
    event = events[0]
    assert event["role"] == "faculty"
    assert event["pegasus_experience"] == "regular"
    assert event["coding"] == 5
    assert event["research_field_given"] is True
    assert "secret lab name" not in json.dumps(event, default=str)
