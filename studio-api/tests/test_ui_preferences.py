"""Tests for the per-user UI preferences endpoint (#241, Phase 1b).

The property that matters most is the default: `simple_run` must be **off**
until validation. A merge here deploys to live user pods, so a preferences
regression that defaulted it on would flip every user into an unvalidated UI.
"""

from __future__ import annotations


def test_defaults_are_off_before_anything_is_saved(client):
    res = client.get("/api/settings/ui")
    assert res.status_code == 200
    assert res.json() == {"simple_run": False, "advanced_open": False}


def test_preferences_round_trip(client):
    saved = client.put(
        "/api/settings/ui",
        json={"simple_run": True, "advanced_open": True},
    )
    assert saved.status_code == 200
    assert saved.json() == {"simple_run": True, "advanced_open": True}

    assert client.get("/api/settings/ui").json() == {
        "simple_run": True,
        "advanced_open": True,
    }


def test_unknown_keys_in_stored_settings_are_ignored(client, monkeypatch):
    # A preference removed in a later release leaves its key behind in the
    # user's DB. Loading it must not 500 the settings page — which is what
    # UIPreferences(**stored) would do without the model_fields filter.
    import main

    async def stored(key, default=None):
        if key == "ui_preferences":
            return {"simple_run": True, "retired_option": "x"}
        return default

    monkeypatch.setattr(main.db, "get_setting", stored)

    res = client.get("/api/settings/ui")
    assert res.status_code == 200
    assert res.json() == {"simple_run": True, "advanced_open": False}


def test_corrupt_stored_settings_fall_back_to_defaults(client, monkeypatch):
    # Whatever is in the row, the endpoint must serve usable defaults rather
    # than failing: an unreadable preference must not make Settings unopenable.
    import main

    async def stored(key, default=None):
        return "not-a-dict" if key == "ui_preferences" else default

    monkeypatch.setattr(main.db, "get_setting", stored)

    res = client.get("/api/settings/ui")
    assert res.status_code == 200
    assert res.json() == {"simple_run": False, "advanced_open": False}


def test_partial_payload_falls_back_to_defaults(client):
    res = client.put("/api/settings/ui", json={"simple_run": True})
    assert res.status_code == 200
    body = res.json()
    assert body["simple_run"] is True
    # Omitted key takes the model default rather than erroring.
    assert body["advanced_open"] is False
