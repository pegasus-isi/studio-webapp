"""Telemetry capture and consent (#367 step 6).

The invariant these tests defend is one-directional: prompts must not leave the
pod unless the user said they may. Every path that could get that wrong —
missing consent, an unreadable consent row, a deployment with no collector, a
username that could forge a resource attribute — is asserted to resolve to "no
content", not merely to "no crash".
"""

from __future__ import annotations

import json
import os
import re


def _write_config(tmp_path, monkeypatch, **llm_config):
    """Generate opencode.json and return the parsed result."""
    from knowledge.opencode import OpenCodeAdapter

    home = tmp_path / "home"
    ws = home / "work"
    ws.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)

    base = {
        "provider": "gateway",
        "model": "default",
        "api_key": "",
        "base_url": "http://gateway.llm:8080/v1",
    }
    base.update(llm_config)
    adapter = OpenCodeAdapter()
    adapter.update_llm_config(str(ws), base)
    cfg = json.loads((home / ".config" / "opencode" / "opencode.json").read_text())
    return cfg, adapter


def _attrs(plugin) -> dict[str, str]:
    raw = plugin[1]["resourceAttributes"]
    return dict(part.split("=", 1) for part in raw.split(","))


# --- the generated config -------------------------------------------------


def test_no_collector_means_no_plugin_at_all(tmp_path, monkeypatch):
    """Studio also runs on a FABRIC VM and under plain `docker run`.

    There the plugin would be fetched from npm on every engine start only to
    retry an endpoint that is never coming up, so it must be absent entirely
    rather than present-and-disabled.
    """
    monkeypatch.delenv("STUDIO_OTLP_ENDPOINT", raising=False)

    cfg, _ = _write_config(tmp_path, monkeypatch)

    assert "plugin" not in cfg


def test_metrics_flow_without_consent_but_traces_do_not(tmp_path, monkeypatch):
    """The default posture: counters yes, anything the user typed no."""
    monkeypatch.setenv("STUDIO_OTLP_ENDPOINT", "http://collector.monitoring:4318")

    cfg, _ = _write_config(tmp_path, monkeypatch, telemetry_content_capture=False)

    plugin = cfg["plugin"][0]
    options = plugin[1]
    assert options["enabled"] is True
    assert options["endpoint"] == "http://collector.monitoring:4318"
    # The prompt rides in a span's input.value regardless of the log flag, so
    # disabling traces is the only thing that actually withholds it.
    assert options["disabledTraces"] == ["all"]
    assert options["capturePromptInLogs"] is False
    # No log backend is deployed; log events would be egress to a drop.
    assert options["logsEnabled"] is False
    assert _attrs(plugin)["studio.content_capture"] == "false"


def test_consent_enables_traces_and_marks_the_data(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_OTLP_ENDPOINT", "http://collector.monitoring:4318")

    cfg, _ = _write_config(tmp_path, monkeypatch, telemetry_content_capture=True)

    plugin = cfg["plugin"][0]
    assert plugin[1]["disabledTraces"] == []
    # The collector redacts content from any resource not marked true, so the
    # marking is what makes a stale pod-side config recoverable.
    assert _attrs(plugin)["studio.content_capture"] == "true"


def test_missing_consent_key_is_read_as_no_consent(tmp_path, monkeypatch):
    """The propagator drops the key when the lookup raises. Absent must not
    read as permitted."""
    monkeypatch.setenv("STUDIO_OTLP_ENDPOINT", "http://collector.monitoring:4318")

    cfg, _ = _write_config(tmp_path, monkeypatch)  # key never set

    assert cfg["plugin"][0][1]["disabledTraces"] == ["all"]


def test_the_user_is_named_from_the_hub_identity(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_OTLP_ENDPOINT", "http://collector.monitoring:4318")
    monkeypatch.setenv("JUPYTERHUB_USER", "kthare10")
    monkeypatch.setenv("STUDIO_DEPLOYMENT_ENV", "jetstream2")

    cfg, _ = _write_config(tmp_path, monkeypatch)

    attrs = _attrs(cfg["plugin"][0])
    assert attrs["enduser.id"] == "kthare10"
    assert attrs["service.namespace"] == "studio"
    assert attrs["deployment.environment"] == "jetstream2"


def test_a_username_cannot_forge_another_attribute(tmp_path, monkeypatch):
    """Resource attributes are a flat `k=v,k=v` string.

    A username carrying a comma and an equals sign would not corrupt its own
    attribute — it would append or redefine the ones after it, which is how a
    session claims to be another user or marks itself consenting.
    """
    monkeypatch.setenv("STUDIO_OTLP_ENDPOINT", "http://collector.monitoring:4318")
    monkeypatch.setenv("JUPYTERHUB_USER", "eve,studio.content_capture=true")

    cfg, _ = _write_config(tmp_path, monkeypatch)

    plugin = cfg["plugin"][0]
    assert _attrs(plugin)["studio.content_capture"] == "false"
    assert _attrs(plugin)["enduser.id"] == "eve_studio.content_capture_true"


def test_the_plugin_version_is_pinned_exactly(tmp_path, monkeypatch):
    """A floating spec resolves to whatever npm serves that day, and a plugin
    that throws on import fails silently (docs/OPENCODE-CHAT-SPIKE.md) — so a
    breaking release would remove telemetry with nothing to say so."""
    from knowledge.opencode import _OTEL_PLUGIN

    assert re.fullmatch(r"@[\w.-]+/[\w.-]+@\d+\.\d+\.\d+", _OTEL_PLUGIN), _OTEL_PLUGIN


def test_protocol_defaults_to_http_and_is_overridable(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_OTLP_ENDPOINT", "http://collector.monitoring:4318")
    monkeypatch.delenv("STUDIO_OTLP_PROTOCOL", raising=False)

    cfg, _ = _write_config(tmp_path, monkeypatch)
    assert cfg["plugin"][0][1]["protocol"] == "http/protobuf"

    monkeypatch.setenv("STUDIO_OTLP_PROTOCOL", "grpc")
    cfg, _ = _write_config(tmp_path, monkeypatch)
    assert cfg["plugin"][0][1]["protocol"] == "grpc"


def test_flipping_consent_changes_the_config_so_the_engine_restarts(
    tmp_path, monkeypatch
):
    """`config_changed` is what schedules the restart. OpenCode reads its
    plugin options once at startup, so a consent change that left the file
    byte-identical would be a setting the engine never hears about."""
    monkeypatch.setenv("STUDIO_OTLP_ENDPOINT", "http://collector.monitoring:4318")

    _, first = _write_config(tmp_path, monkeypatch, telemetry_content_capture=False)
    assert first.config_changed is True

    _, again = _write_config(tmp_path, monkeypatch, telemetry_content_capture=False)
    assert again.config_changed is False  # nothing changed, no restart

    _, flipped = _write_config(tmp_path, monkeypatch, telemetry_content_capture=True)
    assert flipped.config_changed is True


# --- the consent row ------------------------------------------------------


def test_consent_defaults_to_off_and_roundtrips(client):
    body = client.get("/api/settings/telemetry").json()
    assert body["content_capture"] is False

    client.put("/api/settings/telemetry", json={"content_capture": True})
    assert client.get("/api/settings/telemetry").json()["content_capture"] is True

    client.put("/api/settings/telemetry", json={"content_capture": False})
    assert client.get("/api/settings/telemetry").json()["content_capture"] is False


def test_an_unreadable_consent_row_reads_as_no_consent():
    """"We could not find out whether they consented" must not send prompts."""
    import asyncio

    from services import telemetry

    class Broken:
        async def get_setting(self, *a, **k):
            raise RuntimeError("db is gone")

    assert asyncio.run(telemetry.content_capture(Broken())) is False


def test_saving_consent_propagates_it_to_the_engine(client, monkeypatch):
    """Storing it is half the job: the engine reads plugin options at startup
    and no endpoint reloads them, so without propagation Settings would say
    content capture was off while the running engine kept exporting prompts."""
    calls: list[str] = []

    import llm.propagator as propagator

    class Recording(propagator.LLMPropagator):
        async def propagate(self, db):  # type: ignore[override]
            calls.append("propagated")

    monkeypatch.setattr(propagator, "LLMPropagator", Recording)

    client.put("/api/settings/telemetry", json={"content_capture": True})

    assert calls == ["propagated"]


def test_consent_is_still_stored_when_propagation_fails(client, monkeypatch):
    """A failed propagation leaves the engine stale, not the choice unrecorded.
    Failing the request would suggest the opposite and invite a re-toggle."""
    import llm.propagator as propagator

    class Exploding(propagator.LLMPropagator):
        async def propagate(self, db):  # type: ignore[override]
            raise RuntimeError("supervisord is not running")

    monkeypatch.setattr(propagator, "LLMPropagator", Exploding)

    resp = client.put("/api/settings/telemetry", json={"content_capture": True})

    assert resp.status_code == 200
    assert client.get("/api/settings/telemetry").json()["content_capture"] is True


def test_the_ui_can_tell_that_no_collector_is_configured(client, monkeypatch):
    """Offering a consent choice that has no effect is a lie about where the
    data goes, so the control needs to know there is nowhere to send it."""
    monkeypatch.delenv("STUDIO_OTLP_ENDPOINT", raising=False)
    assert client.get("/api/settings/telemetry").json()["collector_configured"] is False

    monkeypatch.setenv("STUDIO_OTLP_ENDPOINT", "http://collector.monitoring:4318")
    assert client.get("/api/settings/telemetry").json()["collector_configured"] is True


def test_the_choice_is_recorded_either_way(client):
    """Off is also the default, so without a separate record "declined" and
    "never asked" read the same and the tour could not tell whom to ask
    (#454)."""
    assert client.get("/api/settings/telemetry").json()["decided"] is False

    saved = client.put("/api/settings/telemetry", json={"content_capture": False})
    assert saved.json()["decided"] is True
    body = client.get("/api/settings/telemetry").json()
    assert body["decided"] is True and body["content_capture"] is False

    # Read-only: a client cannot un-decide by sending decided=false.
    client.put(
        "/api/settings/telemetry", json={"content_capture": True, "decided": False}
    )
    assert client.get("/api/settings/telemetry").json()["decided"] is True


def test_an_existing_opt_in_counts_as_decided():
    """Users who opted in before the decision was recorded are not re-asked."""
    import asyncio

    from services import telemetry

    class Db:
        def __init__(self, values):
            self.values = values

        async def get_setting(self, key, default=None):
            return self.values.get(key, default)

    assert asyncio.run(telemetry.decided(Db({"telemetry_content_capture": "1"}))) is True
    assert asyncio.run(telemetry.decided(Db({"telemetry_content_capture": "0"}))) is False
    assert asyncio.run(telemetry.decided(Db({}))) is False
