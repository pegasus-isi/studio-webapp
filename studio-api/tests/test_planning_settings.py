"""Where planning runs, as a user-facing setting.

Studio plans on the HTCondor submit host when the schedd is on another machine,
because the planner bakes its own absolute paths into the submit files (#334).
That default is right and stays. What was missing is a way to *ask the other
question* — does planning in this workspace work against my submit host — which
nothing can answer by inspection: it depends on the two machines agreeing about
Pegasus, and the pod image moving to Debian trixie changed that answer.

Before this, the only switch was STUDIO_PLAN_ON_SUBMIT_HOST in the spawner
profile plus a pod restart, which a user cannot do to their own session.

Covered here: the endpoint's validation and persistence, and that it reports
what the *next run* will do rather than only what is stored.
"""

from __future__ import annotations

import pytest


def test_defaults_to_auto(client):
    r = client.get("/api/settings/planning")
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "auto"
    assert body["choices"] == ["auto", "remote", "local"]
    # Always answers the question the user actually has.
    assert body["effective"] in ("remote", "local")
    assert body["source"] in ("setting", "env", "detected", "no-condor", "fallback")


def test_disabling_remote_planning_persists_and_takes_effect(client):
    r = client.put("/api/settings/planning", json={"mode": "local"})
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "local"
    # The stored choice must win over detection, or the switch does nothing.
    assert body["effective"] == "local"
    assert body["source"] == "setting"

    assert client.get("/api/settings/planning").json()["mode"] == "local"


def test_it_can_be_set_back_to_auto(client):
    client.put("/api/settings/planning", json={"mode": "local"})
    body = client.put("/api/settings/planning", json={"mode": "auto"}).json()
    assert body["mode"] == "auto"
    assert body["source"] != "setting"


def test_forcing_remote_is_also_available(client):
    body = client.put("/api/settings/planning", json={"mode": "remote"}).json()
    assert body["mode"] == "remote"
    assert body["effective"] == "remote"


@pytest.mark.parametrize("bad", ["", "sideways", "1", "true"])
def test_a_mode_it_cannot_mean_is_refused(client, bad):
    """Rejected rather than coerced.

    `1`/`true` are accepted spellings of the *env var*, not of this setting;
    silently mapping them would make the stored value and the UI disagree.
    """
    r = client.put("/api/settings/planning", json={"mode": bad})
    assert r.status_code == 400, bad
    assert "auto, remote, local" in r.json()["detail"]


def test_case_and_whitespace_are_normalised(client):
    """Accepted, and stored canonically — so the UI radio matches the DB."""
    body = client.put("/api/settings/planning", json={"mode": "  LOCAL "}).json()
    assert body["mode"] == "local"
    assert client.get("/api/settings/planning").json()["mode"] == "local"


def test_a_refused_write_leaves_the_previous_value(client):
    client.put("/api/settings/planning", json={"mode": "local"})
    client.put("/api/settings/planning", json={"mode": "nonsense"})
    assert client.get("/api/settings/planning").json()["mode"] == "local"
