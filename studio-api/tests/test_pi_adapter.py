"""Tests for the pi coding agent knowledge adapter."""

from __future__ import annotations

import json


def _update(tmp_path, monkeypatch, llm_config):
    from knowledge.pi import PiAdapter

    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    ws = home / "work"
    ws.mkdir(exist_ok=True)
    PiAdapter().update_llm_config(str(ws), llm_config)
    return home


def test_models_json_lists_every_openai_compatible_provider(tmp_path, monkeypatch):
    home = _update(
        tmp_path,
        monkeypatch,
        {
            "provider": "gateway",
            "model": "default",
            "api_key": "",
            "base_url": "http://gateway.llm:8080/v1",
            "all_providers": [
                {
                    "provider_id": "gateway",
                    "name": "LLM Gateway",
                    "api_key": "",
                    "base_url": "http://gateway.llm:8080/v1",
                    "default_model": "default",
                },
                {
                    "provider_id": "custom-renci",
                    "name": "RENCI Gateway",
                    "api_key": "sk-renci",
                    "base_url": "https://ai.example.org/v1",
                    "default_model": "gpt-5.6-sol",
                    "models": ["gpt-5.6-luna", "gpt-5.6-sol", "kimi-k2.6"],
                },
                # A pi built-in provider — must NOT get a models.json entry.
                {
                    "provider_id": "anthropic",
                    "name": "Anthropic",
                    "api_key": "sk-ant",
                    "base_url": "",
                    "default_model": "claude-sonnet-4-5-20250929",
                },
            ],
        },
    )

    cfg = json.loads((home / ".pi" / "agent" / "models.json").read_text())
    assert set(cfg["providers"]) == {"gateway", "custom-renci"}

    renci = cfg["providers"]["custom-renci"]
    assert renci["baseUrl"] == "https://ai.example.org/v1"
    assert renci["api"] == "openai-completions"
    assert renci["apiKey"] == "sk-renci"
    assert [m["id"] for m in renci["models"]] == [
        "gpt-5.6-luna", "gpt-5.6-sol", "kimi-k2.6",
    ]

    # Keyless gateway: apiKey omitted entirely, default model declared.
    gw = cfg["providers"]["gateway"]
    assert "apiKey" not in gw
    assert [m["id"] for m in gw["models"]] == ["default"]


def test_settings_json_sets_default_and_merges(tmp_path, monkeypatch):
    """defaultProvider/defaultModel are set without clobbering user settings,
    and the shared skills checkout is appended exactly once."""
    home = tmp_path / "home"
    agent_dir = home / ".pi" / "agent"
    agent_dir.mkdir(parents=True)
    (agent_dir / "settings.json").write_text(
        json.dumps({"theme": "light", "skills": ["/home/user/my-skills"]})
    )

    checkout = tmp_path / "checkout"
    (checkout / "skills" / "pegasus-scaffold").mkdir(parents=True)
    (checkout / "skills" / "pegasus-scaffold" / "SKILL.md").write_text(
        "---\nname: pegasus-scaffold\n---\n"
    )
    monkeypatch.setenv("KNOWLEDGE_ROOT", str(checkout))

    config = {
        "provider": "custom-renci",
        "model": "gpt-5.6-sol",
        "api_key": "sk-renci",
        "base_url": "https://ai.example.org/v1",
    }
    _update(tmp_path, monkeypatch, config)

    settings = json.loads((agent_dir / "settings.json").read_text())
    assert settings["defaultProvider"] == "custom-renci"
    assert settings["defaultModel"] == "gpt-5.6-sol"
    assert settings["theme"] == "light"  # user's choice untouched
    assert settings["skills"][0] == "/home/user/my-skills"
    assert settings["skills"][1] == str(checkout / "skills")

    # Idempotent: a second propagation must not duplicate the skills path.
    _update(tmp_path, monkeypatch, config)
    settings = json.loads((agent_dir / "settings.json").read_text())
    assert settings["skills"].count(str(checkout / "skills")) == 1


def test_active_provider_synthesized_without_saved_row(tmp_path, monkeypatch):
    home = _update(
        tmp_path,
        monkeypatch,
        {
            "provider": "custom-live",
            "model": "live-model",
            "api_key": "k-live",
            "base_url": "https://live.example/v1",
        },
    )
    cfg = json.loads((home / ".pi" / "agent" / "models.json").read_text())
    live = cfg["providers"]["custom-live"]
    assert live["apiKey"] == "k-live"
    assert [m["id"] for m in live["models"]] == ["live-model"]

    settings = json.loads(
        (home / ".pi" / "agent" / "settings.json").read_text()
    )
    assert settings["defaultProvider"] == "custom-live"


def test_uninstall_removes_generated_config_only(tmp_path, monkeypatch):
    home = _update(
        tmp_path,
        monkeypatch,
        {
            "provider": "gateway",
            "model": "default",
            "base_url": "http://gateway.llm:8080/v1",
        },
    )
    agent_dir = home / ".pi" / "agent"
    # Simulate a user setting saved alongside ours.
    settings = json.loads((agent_dir / "settings.json").read_text())
    settings["theme"] = "light"
    (agent_dir / "settings.json").write_text(json.dumps(settings))

    from knowledge.pi import PiAdapter

    PiAdapter().uninstall(str(home / "work"))

    assert not (agent_dir / "models.json").exists()
    remaining = json.loads((agent_dir / "settings.json").read_text())
    assert "defaultProvider" not in remaining
    assert "defaultModel" not in remaining
    assert remaining["theme"] == "light"


def test_pi_is_registered(tmp_path):
    from knowledge.adapters import get_adapter
    from knowledge.pi import PiAdapter
    from services.installer import _INLINE_REGISTRY

    assert isinstance(get_adapter("pi"), PiAdapter)
    entry = next(t for t in _INLINE_REGISTRY if t["id"] == "pi")
    assert entry["binary"] == "pi"
    assert entry["knowledge_adapter"] == "pi"
    assert "@earendil-works/pi-coding-agent" in entry["install_command"]
