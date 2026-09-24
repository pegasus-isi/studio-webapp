"""Tests for the per-user UI preferences endpoint (#241, Phase 1b).

The property that matters most is that the default is *deliberate*.
`simple_run` is now on (#241) — the unified Run flow is the product and the
four Pegasus verbs are the opt-out — and the frontend fallback has to agree
with it, or every page renders one layout and then swaps to the other once the
preference fetch lands.
"""

from __future__ import annotations


def test_simple_run_is_on_by_default(client):
    # Flipped in #241 after Phases 1-5 landed. Kept as an explicit assertion
    # rather than dropped: the default is a product decision, and it should
    # take an edit to this line to change it.
    res = client.get("/api/settings/ui")
    assert res.status_code == 200
    assert res.json() == {
        "simple_run": True,
        "advanced_open": False,
        "welcome_dismissed": False,
        "nav_advanced_open": False,
        "skip_welcome": False,
        # OpenCode became the default in #403, once the trial in a real pod
        # showed it working. Asserted so flipping it is an edit to this line,
        # not a side effect.
        "chat_engine": "opencode",
    }


def test_preferences_round_trip(client):
    payload = {
        "simple_run": True,
        "advanced_open": True,
        "welcome_dismissed": True,
        "nav_advanced_open": True,
        "skip_welcome": True,
        # The non-default engine, so the round trip proves a stored value is
        # served back rather than coincidentally matching the default.
        "chat_engine": "builtin",
    }
    saved = client.put("/api/settings/ui", json=payload)
    assert saved.status_code == 200
    assert saved.json() == payload

    assert client.get("/api/settings/ui").json() == payload


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
    assert res.json() == {
        "simple_run": True,
        "advanced_open": False,
        "welcome_dismissed": False,
        "nav_advanced_open": False,
        "skip_welcome": False,
        "chat_engine": "opencode",
    }


def test_corrupt_stored_settings_fall_back_to_defaults(client, monkeypatch):
    # Whatever is in the row, the endpoint must serve usable defaults rather
    # than failing: an unreadable preference must not make Settings unopenable.
    import main

    async def stored(key, default=None):
        return "not-a-dict" if key == "ui_preferences" else default

    monkeypatch.setattr(main.db, "get_setting", stored)

    res = client.get("/api/settings/ui")
    assert res.status_code == 200
    assert res.json() == {
        "simple_run": True,
        "advanced_open": False,
        "welcome_dismissed": False,
        "nav_advanced_open": False,
        "skip_welcome": False,
        "chat_engine": "opencode",
    }


def test_partial_payload_falls_back_to_defaults(client):
    res = client.put("/api/settings/ui", json={"advanced_open": True})
    assert res.status_code == 200
    body = res.json()
    assert body["advanced_open"] is True
    # Omitted key takes the model default rather than erroring.
    assert body["simple_run"] is True


def test_a_retired_preference_value_does_not_break_the_endpoint(client, monkeypatch):
    """A stale *value* must degrade like a stale key, not 500.

    `chat_engine` is a Literal (#367), and the intended end state is one
    engine — so a value stored today can be retired tomorrow. Rejecting the
    whole payload would lose every other preference with it and make Settings
    unopenable, which is the opposite of what the field's comment promises.
    """
    import main

    async def stored(key, default=None):
        if key == "ui_preferences":
            return {"chat_engine": "retired-engine", "skip_welcome": True}
        return default

    monkeypatch.setattr(main.db, "get_setting", stored)

    res = client.get("/api/settings/ui")
    assert res.status_code == 200
    body = res.json()
    # The unusable value falls back to the default...
    assert body["chat_engine"] == "opencode"
    # ...and the preferences beside it survive.
    assert body["skip_welcome"] is True


def test_command_approval_reaches_the_engine_config(client, monkeypatch):
    """Saving the shell-approval setting must propagate it.

    Under the OpenCode engine this setting *is* a `permission` rule in the
    generated config, so persisting it without propagating left Settings
    claiming commands were confirmed while the engine kept running them — for
    the rest of the pod's life, since a restart only re-reads the file that was
    never rewritten.
    """
    calls = []

    from llm import propagator

    class FakePropagator:
        async def propagate(self, db):
            calls.append("propagated")

    monkeypatch.setattr(propagator, "LLMPropagator", FakePropagator)

    res = client.put("/api/chat/command-approval", json={"require_approval": False})
    assert res.status_code == 200
    assert res.json()["require_approval"] is False
    assert calls == ["propagated"]
