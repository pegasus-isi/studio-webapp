"""LLM provider registry and model discovery.

Ported from opencode-extension/config_builder.py — converted urllib to async httpx.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import structlog

log = structlog.get_logger()

# Provider presets — mirrors config_builder.PROVIDERS
PROVIDERS: dict[str, dict[str, Any]] = {
    "anthropic": {
        "name": "Anthropic",
        "npm": "@ai-sdk/anthropic",
        "base_url": None,
        "default_model": "claude-sonnet-4-5-20250929",
        "api_key_env": "ANTHROPIC_API_KEY",
    },
    "openai": {
        "name": "OpenAI",
        "npm": "@ai-sdk/openai",
        "base_url": "https://api.openai.com/v1",
        "default_model": "gpt-4o",
        "api_key_env": "OPENAI_API_KEY",
    },
    "fabric": {
        "name": "FABRIC AI",
        "npm": "@ai-sdk/openai-compatible",
        "base_url": "https://ai.fabric-testbed.net/v1",
        "default_model": "qwen3-coder-30b",
        "api_key_env": "FABRIC_AI_API_KEY",
    },
    "nrp": {
        "name": "NRP (Nautilus)",
        "npm": "@ai-sdk/openai-compatible",
        "base_url": "https://ellm.nrp-nautilus.io/v1",
        "default_model": "qwen3-coder-30b",
        "api_key_env": "NRP_API_KEY",
    },
    "google": {
        "name": "Google (Gemini)",
        "npm": "@ai-sdk/google",
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
        "default_model": "gemini-2.5-pro",
        "api_key_env": "GOOGLE_API_KEY",
    },
    "custom": {
        "name": "Custom Endpoint",
        "npm": "@ai-sdk/openai-compatible",
        "base_url": None,
        "default_model": "",
        "api_key_env": "CUSTOM_API_KEY",
    },
    "ollama": {
        "name": "Ollama",
        "npm": "@ai-sdk/openai-compatible",
        "base_url": None,
        "default_model": "qwen2.5-coder:7b",
        "api_key_env": None,
    },
}

PROVIDER_DEFAULTS: dict[str, dict[str, str]] = {
    "anthropic": {
        "base_url": "https://api.anthropic.com/v1",
        "model": "claude-sonnet-4-5-20250929",
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o",
    },
    "fabric": {
        "base_url": "https://ai.fabric-testbed.net/v1",
        "model": "qwen3-coder-30b",
    },
    "nrp": {
        "base_url": "https://ellm.nrp-nautilus.io/v1",
        "model": "qwen3-coder-30b",
    },
    "google": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
        "model": "gemini-2.5-pro",
    },
    "custom": {},
    "ollama": {
        "base_url": "http://localhost:11434/v1",
        "model": "qwen2.5-coder:7b",
    },
}

# Providers that need the model proxy
PROXY_PROVIDERS = {"fabric", "nrp", "custom", "ollama"}


def _resolve_base_url(pid: str, env_vars: dict[str, str] | None = None) -> str:
    """Resolve the actual base URL for a provider."""
    env_vars = env_vars or {}
    preset = PROVIDERS[pid]
    if pid == "custom":
        return env_vars.get("CUSTOM_BASE_URL", env_vars.get("OPENAI_API_BASE", ""))
    if pid == "ollama":
        host = env_vars.get("OLLAMA_HOST", "http://localhost:11434")
        return f"{host}/v1"
    return preset["base_url"] or ""


def _resolve_api_key(pid: str, env_vars: dict[str, str] | None = None) -> str:
    """Resolve the API key for a provider from env vars."""
    env_vars = env_vars or {}
    preset = PROVIDERS[pid]
    if pid == "ollama":
        return "ollama"
    key_env = preset["api_key_env"]
    if key_env:
        return env_vars.get(key_env, "")
    return ""


async def fetch_models(base_url: str, api_key: str) -> tuple[list[str], str | None]:
    """Fetch available model IDs from an OpenAI-compatible /models endpoint.

    Returns (model_ids, error_message).
    """
    if not base_url:
        return [], "No base URL configured."

    base = base_url.rstrip("/")
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"{base}/models", headers=headers)
            resp.raise_for_status()
            data = resp.json()
            model_ids = sorted(
                m.get("id", "") for m in data.get("data", []) if m.get("id")
            )
            if model_ids:
                return model_ids, None
            return [], "Endpoint returned no models."
    except httpx.HTTPStatusError as e:
        return [], f"HTTP {e.response.status_code}: {e.response.reason_phrase}"
    except Exception as e:
        return [], f"Failed to fetch models: {e}"
