"""Limits reported under a namespaced model id must reach the adapters,
which declare models by the bare id."""

from __future__ import annotations

import json

from knowledge.common import model_limits_by_bare_id


def test_namespaced_ids_are_keyed_by_bare_id_too():
    prov = {
        "model_limits": {
            "qwen/qwen3.8-27b": {"context": 262144, "output": 32768},
            "plain": {"context": 1, "output": 2, "junk": 3},
            "bad": {"context": "128k", "output": True},
        }
    }
    got = model_limits_by_bare_id(prov)
    assert got["qwen3.8-27b"] == {"context": 262144, "output": 32768}
    assert got["qwen/qwen3.8-27b"] == {"context": 262144, "output": 32768}
    assert got["plain"] == {"context": 1, "output": 2}
    assert "bad" not in got


def test_directly_reported_bare_id_wins_over_derived():
    prov = {
        "model_limits": {
            "vendor/m": {"context": 10},
            "m": {"context": 20},
        }
    }
    assert model_limits_by_bare_id(prov)["m"] == {"context": 20}


def test_opencode_declares_limits_for_namespaced_listing(tmp_path, monkeypatch):
    from knowledge.opencode import OpenCodeAdapter

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    ws = tmp_path / "work"
    ws.mkdir()
    OpenCodeAdapter().update_llm_config(
        str(ws),
        {
            "provider": "custom-gw",
            "model": "qwen3.8-27b",
            "api_key": "k",
            "base_url": "https://gw.example/v1",
            "all_providers": [
                {
                    "provider_id": "custom-gw",
                    "name": "Gateway",
                    "api_key": "k",
                    "base_url": "https://gw.example/v1",
                    "default_model": "qwen3.8-27b",
                    "models": ["qwen/qwen3.8-27b"],
                    "model_limits": {
                        "qwen/qwen3.8-27b": {"context": 262144, "output": 32768},
                    },
                },
            ],
        },
    )
    cfg = json.loads((tmp_path / ".config" / "opencode" / "opencode.json").read_text())
    assert cfg["provider"]["custom-gw"]["models"]["qwen3.8-27b"]["limit"] == {
        "context": 262144, "output": 32768,
    }


def test_codex_profile_limits_survive_namespaced_listing(tmp_path, monkeypatch):
    from knowledge.codex_cli import CodexCLIAdapter

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    ws = home / "work"
    ws.mkdir()
    CodexCLIAdapter().update_llm_config(
        str(ws),
        {
            "provider": "custom-gw",
            "model": "qwen3.8-27b",
            "api_key": "k",
            "base_url": "https://gw.example/v1",
            "all_providers": [
                {
                    "provider_id": "custom-gw",
                    "name": "Gateway",
                    "api_key": "k",
                    "base_url": "https://gw.example/v1",
                    "default_model": "qwen/qwen3.8-27b",
                    "models": ["qwen/qwen3.8-27b"],
                    "model_limits": {
                        "qwen/qwen3.8-27b": {"context": 262144, "output": 32768},
                    },
                },
            ],
        },
    )
    toml = (home / ".codex" / "config.toml").read_text()
    profile = toml.split("[profiles.custom-gw]", 1)[1].split("\n\n", 1)[0]
    assert "model_context_window = 262144" in profile
    assert "model_max_output_tokens = 32768" in profile
