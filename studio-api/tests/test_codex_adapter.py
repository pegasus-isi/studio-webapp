"""Tests for the Codex CLI knowledge adapter config generation."""

from __future__ import annotations


def _write_config(tmp_path, monkeypatch, llm_config):
    from knowledge.codex_cli import CodexCLIAdapter

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    ws = home / "work"
    ws.mkdir()
    CodexCLIAdapter().update_llm_config(str(ws), llm_config)
    return (home / ".codex" / "config.toml").read_text()


def test_profiles_written_for_every_saved_provider(tmp_path, monkeypatch):
    """Every saved provider gets a [profiles.<id>] with its own model.

    A [model_providers.<id>] section alone leaves the endpoint invisible in
    Codex — the model picker only lists the active model_provider — so a
    saved custom endpoint could never be selected (#205). Profiles make it
    reachable via `codex --profile <id>`.
    """
    toml = _write_config(
        tmp_path,
        monkeypatch,
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

    # Provider sections for both, as before.
    assert "[model_providers.custom-renci]" in toml
    assert "[model_providers.gateway]" in toml
    # And a profile per provider, carrying that provider's own model.
    assert "[profiles.custom-renci]" in toml
    assert "[profiles.gateway]" in toml
    renci_profile = toml.split("[profiles.custom-renci]", 1)[1]
    renci_profile = renci_profile.split("[", 1)[0]
    assert 'model_provider = "custom-renci"' in renci_profile
    assert 'model = "gpt-5.6-sol"' in renci_profile
    # The active selection is unchanged.
    header = toml.split("[", 1)[0]
    assert 'model = "default"' in header
    assert 'model_provider = "gateway"' in header


def test_synthesized_active_provider_gets_a_profile(tmp_path, monkeypatch):
    """The active provider may have no saved row (legacy llm_config only);
    its synthesized entry must carry the active model into its profile."""
    toml = _write_config(
        tmp_path,
        monkeypatch,
        {
            "provider": "custom-live",
            "model": "live-model",
            "api_key": "k",
            "base_url": "https://live.example/v1",
        },
    )

    assert "[model_providers.custom-live]" in toml
    profile = toml.split("[profiles.custom-live]", 1)[1].split("[", 1)[0]
    assert 'model_provider = "custom-live"' in profile
    assert 'model = "live-model"' in profile


def test_profile_model_falls_back_to_active_model(tmp_path, monkeypatch):
    """A draft saved without a default model still gets a usable profile."""
    toml = _write_config(
        tmp_path,
        monkeypatch,
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
                    "provider_id": "custom-draft",
                    "name": "Draft",
                    "base_url": "https://draft.example/v1",
                    "default_model": "",
                },
            ],
        },
    )

    profile = toml.split("[profiles.custom-draft]", 1)[1].split("[", 1)[0]
    assert 'model = "default"' in profile


def test_profile_carries_reported_limits_for_its_model(tmp_path, monkeypatch):
    """A profile is one model; when the endpoint reported that model's
    limits they are written on the profile, and nothing is written for a
    model the endpoint said nothing about."""
    toml = _write_config(
        tmp_path,
        monkeypatch,
        {
            "provider": "custom-gw",
            "model": "big",
            "api_key": "k",
            "base_url": "https://gw.example/v1",
            "all_providers": [
                {
                    "provider_id": "custom-gw",
                    "name": "Gateway",
                    "api_key": "k",
                    "base_url": "https://gw.example/v1",
                    "default_model": "big",
                    "models": ["big", "unsized"],
                    "model_limits": {"big": {"context": 262144, "output": 32768}},
                },
                {
                    "provider_id": "custom-other",
                    "name": "Other",
                    "api_key": "k2",
                    "base_url": "https://other.example/v1",
                    "default_model": "unsized",
                    "models": ["unsized"],
                },
            ],
        },
    )
    gw = toml.split("[profiles.custom-gw]", 1)[1].split("\n\n", 1)[0]
    assert "model_context_window = 262144" in gw
    assert "model_max_output_tokens = 32768" in gw
    other = toml.split("[profiles.custom-other]", 1)[1].split("\n\n", 1)[0]
    assert "model_context_window" not in other
    assert "model_max_output_tokens" not in other
