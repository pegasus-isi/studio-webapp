"""Tests for the Crush knowledge adapter config generation."""

from __future__ import annotations

import json


def _update(tmp_path, monkeypatch, llm_config):
    from knowledge.crush import CrushAdapter

    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    ws = home / "work"
    ws.mkdir(exist_ok=True)
    CrushAdapter().update_llm_config(str(ws), llm_config)
    return home


def _config(home):
    return json.loads(
        (home / ".config" / "crush" / "crush.json").read_text()
    )


def test_every_openai_compatible_provider_is_written(tmp_path, monkeypatch):
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
                # A Crush built-in (Catwalk) provider — must NOT be shadowed
                # by a hand-written one-model entry.
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

    cfg = _config(home)
    assert set(cfg["providers"]) == {"gateway", "custom-renci"}

    renci = cfg["providers"]["custom-renci"]
    assert renci["type"] == "openai-compat"
    assert renci["base_url"] == "https://ai.example.org/v1"
    assert renci["api_key"] == "sk-renci"
    assert [m["id"] for m in renci["models"]] == [
        "gpt-5.6-luna", "gpt-5.6-sol", "kimi-k2.6",
    ]
    # Required-by-schema sizing fields are present on every model entry.
    assert all("context_window" in m for m in renci["models"])

    # Keyless gateway: api_key omitted entirely.
    gw = cfg["providers"]["gateway"]
    assert "api_key" not in gw
    assert [m["id"] for m in gw["models"]] == ["default"]

    # Active selection drives both the large and small model slots.
    assert cfg["models"]["large"] == {"model": "default", "provider": "gateway"}
    assert cfg["models"]["small"] == {"model": "default", "provider": "gateway"}


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
    cfg = _config(home)
    live = cfg["providers"]["custom-live"]
    assert live["api_key"] == "k-live"
    assert [m["id"] for m in live["models"]] == ["live-model"]
    assert cfg["models"]["large"] == {
        "model": "live-model", "provider": "custom-live",
    }


def test_mcp_and_skills_declared(tmp_path, monkeypatch):
    checkout = tmp_path / "checkout"
    (checkout / "skills" / "pegasus-scaffold").mkdir(parents=True)
    (checkout / "skills" / "pegasus-scaffold" / "SKILL.md").write_text(
        "---\nname: pegasus-scaffold\n---\n"
    )
    monkeypatch.setenv("KNOWLEDGE_ROOT", str(checkout))

    home = _update(
        tmp_path,
        monkeypatch,
        {
            "provider": "gateway",
            "model": "default",
            "base_url": "http://gateway.llm:8080/v1",
        },
    )
    cfg = _config(home)
    assert cfg["mcp"]["pegasus-docs"]["type"] == "http"
    assert "gitmcp.io/pegasus-isi/pegasus" in cfg["mcp"]["pegasus-docs"]["url"]
    assert cfg["options"]["skills_paths"] == [str(checkout / "skills")]
    # Context is declared with an absolute path so it loads from any cwd.
    agents = home / ".config" / "crush" / "AGENTS.md"
    assert cfg["options"]["context_paths"] == [str(agents)]


def test_uninstall_removes_generated_config(tmp_path, monkeypatch):
    home = _update(
        tmp_path,
        monkeypatch,
        {
            "provider": "gateway",
            "model": "default",
            "base_url": "http://gateway.llm:8080/v1",
        },
    )
    from knowledge.crush import CrushAdapter

    CrushAdapter().uninstall(str(home / "work"))
    assert not (home / ".config" / "crush" / "crush.json").exists()
    assert not (home / ".config" / "crush" / "AGENTS.md").exists()


def test_crush_is_registered(tmp_path):
    from knowledge.adapters import get_adapter
    from knowledge.crush import CrushAdapter
    from services.installer import _INLINE_REGISTRY

    assert isinstance(get_adapter("crush"), CrushAdapter)
    entry = next(t for t in _INLINE_REGISTRY if t["id"] == "crush")
    assert entry["binary"] == "crush"
    assert entry["knowledge_adapter"] == "crush"
    assert "@charmland/crush" in entry["install_command"]
