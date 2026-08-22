"""Tests for the OpenCode knowledge adapter config generation."""

from __future__ import annotations

import json
import os


def test_writes_global_config_only(tmp_path, monkeypatch):
    from knowledge.opencode import OpenCodeAdapter

    home = tmp_path / "home"
    workspace = tmp_path / "home" / "work"
    workspace.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)

    OpenCodeAdapter().update_llm_config(
        str(workspace),
        {
            "provider": "custom-mqu1g76g",
            "model": "default",
            "api_key": "k",
            "base_url": "http://gateway.llm:8080/v1",
        },
    )

    global_cfg = home / ".config" / "opencode" / "opencode.json"
    ws_cfg = workspace / "opencode.json"
    assert global_cfg.is_file()  # picked up from any cwd
    # No workspace copy: it held byte-identical content, was a second thing to
    # keep in sync, and was the only non-hidden file Studio placed in the
    # directory users keep workflows in.
    assert not ws_cfg.exists()

    cfg = json.loads(global_cfg.read_text())
    # model is "<provider>/<modelId>" with a clean model id.
    assert cfg["model"] == "custom-mqu1g76g/default"
    assert "custom-mqu1g76g" in cfg["provider"]
    assert list(cfg["provider"]["custom-mqu1g76g"]["models"]) == ["default"]


def test_all_saved_providers_are_written(tmp_path, monkeypatch):
    """A saved-but-not-active endpoint must appear in the provider map.

    It used to be dropped — the config only ever held the active provider —
    so custom endpoints never showed up in opencode's picker (#205).
    """
    from knowledge.opencode import OpenCodeAdapter

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    ws = tmp_path / "work"
    ws.mkdir()

    OpenCodeAdapter().update_llm_config(
        str(ws),
        {
            "provider": "gateway",
            "model": "default",
            "api_key": "",
            "base_url": "http://gateway.llm:8080/v1",
            "all_providers": [
                {
                    "provider_id": "custom-renci",
                    "name": "RENCI Gateway",
                    "api_key": "sk-renci",
                    "base_url": "https://ai.example.org/v1",
                    "default_model": "gpt-5.6-sol",
                },
                {
                    "provider_id": "gateway",
                    "name": "LLM Gateway",
                    "api_key": "",
                    "base_url": "http://gateway.llm:8080/v1",
                    "default_model": "default",
                },
            ],
        },
    )

    cfg = json.loads(
        (tmp_path / ".config" / "opencode" / "opencode.json").read_text()
    )
    assert set(cfg["provider"]) == {"gateway", "custom-renci"}
    renci = cfg["provider"]["custom-renci"]
    assert renci["name"] == "RENCI Gateway"
    assert renci["options"] == {
        "baseURL": "https://ai.example.org/v1",
        "apiKey": "sk-renci",
    }
    assert list(renci["models"]) == ["gpt-5.6-sol"]
    # The active selection is unchanged by extra saved providers.
    assert cfg["model"] == "gateway/default"


def test_fetched_model_list_is_declared_in_full(tmp_path, monkeypatch):
    """When the propagator attached a live /models list, every model is
    declared — opencode's picker lists only what the config declares, so
    default_model alone meant one selectable model per endpoint (#207)."""
    from knowledge.opencode import OpenCodeAdapter

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    ws = tmp_path / "work"
    ws.mkdir()

    OpenCodeAdapter().update_llm_config(
        str(ws),
        {
            "provider": "gateway",
            "model": "default",
            "base_url": "http://gateway.llm:8080/v1",
            "all_providers": [
                {
                    "provider_id": "gateway",
                    "name": "LLM Gateway",
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
            ],
        },
    )

    cfg = json.loads(
        (tmp_path / ".config" / "opencode" / "opencode.json").read_text()
    )
    renci = cfg["provider"]["custom-renci"]
    assert set(renci["models"]) == {"gpt-5.6-luna", "gpt-5.6-sol", "kimi-k2.6"}
    # A default_model missing from the fetched list is still declared.
    OpenCodeAdapter().update_llm_config(
        str(ws),
        {
            "provider": "custom-renci",
            "model": "hand-picked",
            "api_key": "sk-renci",
            "base_url": "https://ai.example.org/v1",
            "all_providers": [
                {
                    "provider_id": "custom-renci",
                    "name": "RENCI Gateway",
                    "api_key": "sk-renci",
                    "base_url": "https://ai.example.org/v1",
                    "default_model": "hand-picked",
                    "models": ["gpt-5.6-sol"],
                },
            ],
        },
    )
    cfg = json.loads(
        (tmp_path / ".config" / "opencode" / "opencode.json").read_text()
    )
    assert list(cfg["provider"]["custom-renci"]["models"]) == [
        "hand-picked",
        "gpt-5.6-sol",
    ]


def test_active_provider_synthesized_without_saved_row(tmp_path, monkeypatch):
    """A legacy config with no provider_configs row still yields a working
    entry for the active provider alongside the saved ones."""
    from knowledge.opencode import OpenCodeAdapter

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    ws = tmp_path / "work"
    ws.mkdir()

    OpenCodeAdapter().update_llm_config(
        str(ws),
        {
            "provider": "custom-live",
            "model": "live-model",
            "api_key": "k-live",
            "base_url": "https://live.example/v1",
            "all_providers": [
                {
                    "provider_id": "custom-other",
                    "name": "Other",
                    "api_key": "k-other",
                    "base_url": "https://other.example/v1",
                    "default_model": "other-model",
                },
            ],
        },
    )

    cfg = json.loads(
        (tmp_path / ".config" / "opencode" / "opencode.json").read_text()
    )
    assert set(cfg["provider"]) == {"custom-live", "custom-other"}
    assert cfg["provider"]["custom-live"]["options"]["apiKey"] == "k-live"
    assert list(cfg["provider"]["custom-live"]["models"]) == ["live-model"]
    assert cfg["model"] == "custom-live/live-model"


def test_model_id_prefix_is_stripped(tmp_path, monkeypatch):
    """A stored model that already carries a provider prefix isn't doubled."""
    from knowledge.opencode import OpenCodeAdapter

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    ws = tmp_path / "work"
    ws.mkdir()

    OpenCodeAdapter().update_llm_config(
        str(ws),
        {"provider": "custom-x", "model": "custom-x/default", "base_url": "u"},
    )
    cfg = json.loads((tmp_path / ".config" / "opencode" / "opencode.json").read_text())
    assert cfg["model"] == "custom-x/default"  # not custom-x/custom-x/default


def test_xdg_config_home_respected(tmp_path, monkeypatch):
    from knowledge.opencode import OpenCodeAdapter

    xdg = tmp_path / "xdg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    ws = tmp_path / "work"
    ws.mkdir()

    OpenCodeAdapter().update_llm_config(
        str(ws), {"provider": "gateway", "model": "Default", "base_url": "u"}
    )
    assert (xdg / "opencode" / "opencode.json").is_file()


def test_uninstall_still_removes_a_legacy_workspace_config(tmp_path, monkeypatch):
    """Workspaces seeded before the workspace copy was dropped still have one."""
    from knowledge.opencode import OpenCodeAdapter

    home = tmp_path / "home"
    workspace = home / "work"
    workspace.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)

    legacy = workspace / "opencode.json"
    legacy.write_text("{}")

    OpenCodeAdapter().uninstall(str(workspace))
    assert not legacy.exists()
