"""Tests for the OpenCode knowledge adapter config generation."""

from __future__ import annotations

import json
import os


def test_writes_global_and_workspace_config(tmp_path, monkeypatch):
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
    assert ws_cfg.is_file()

    cfg = json.loads(global_cfg.read_text())
    # model is "<provider>/<modelId>" with a clean model id.
    assert cfg["model"] == "custom-mqu1g76g/default"
    assert "custom-mqu1g76g" in cfg["provider"]
    assert list(cfg["provider"]["custom-mqu1g76g"]["models"]) == ["default"]


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
