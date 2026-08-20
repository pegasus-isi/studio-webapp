"""LLM provider registry and model discovery.

Ported from opencode-extension/config_builder.py — converted urllib to async httpx.
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx
import structlog

log = structlog.get_logger()

# Baked-in default LLM gateway. Studio ships pointed at an internal,
# OpenAI-compatible gateway so users get a working chat out of the box with
# zero configuration. Both values are overridable via env for other
# deployments.
GATEWAY_BASE_URL = os.environ.get("STUDIO_LLM_GATEWAY_URL", "http://gateway.llm:8080/v1")
# The gateway (bifrost/litellm router) is case-sensitive on the model name:
# "default" routes (→ llama-4-scout), "Default" returns "could not auto resolve a
# provider". Must be lowercase.
GATEWAY_DEFAULT_MODEL = os.environ.get("STUDIO_LLM_DEFAULT_MODEL", "default")

# Provider presets — mirrors config_builder.PROVIDERS
PROVIDERS: dict[str, dict[str, Any]] = {
    "gateway": {
        "name": "LLM Gateway",
        "npm": "@ai-sdk/openai-compatible",
        "base_url": GATEWAY_BASE_URL,
        "default_model": GATEWAY_DEFAULT_MODEL,
        "api_key_env": None,
    },
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
    "gateway": {
        "base_url": GATEWAY_BASE_URL,
        "model": GATEWAY_DEFAULT_MODEL,
    },
    "anthropic": {
        "base_url": "https://api.anthropic.com/v1",
        "model": "claude-sonnet-4-5-20250929",
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o",
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
PROXY_PROVIDERS = {"custom", "ollama"}


def provider_type(provider_id: str) -> str:
    """Normalize a provider_id to a known preset key. Multiple custom endpoints
    are stored as `custom-<id>`; treat those (and any unknown id) as the
    OpenAI-compatible "custom" type so adapters configure them correctly
    instead of falling back to anthropic."""
    if provider_id in PROVIDERS:
        return provider_id
    return "custom"


async def check_api_key_requirement(
    provider_id: str, api_key: str, base_url: str
) -> str | None:
    """Return an error message if saving this provider config would break chat.

    A provider saved without a required API key fails much later — at chat
    time, deep inside opencode, as "Authentication Error, No api key passed
    in" — so catch it at save time instead. Known presets that declare an
    ``api_key_env`` (anthropic, openai, google) hard-require a
    key. Custom endpoints may legitimately be keyless (self-hosted vLLM, an
    internal gateway), so probe ``/models`` without a key and reject only if
    the endpoint demands auth.
    """
    ptype = provider_type(provider_id)
    preset = PROVIDERS[ptype]
    if api_key or not preset.get("api_key_env"):
        return None  # key supplied, or provider needs none (gateway, ollama)
    if ptype != "custom":
        return (
            f"{preset['name']} requires an API key. Add one, or switch to the "
            "built-in LLM Gateway which needs no key."
        )
    if not base_url:
        return "Custom provider requires a base URL."
    _, err = await fetch_models(base_url, "")
    if err and ("401" in err or "403" in err):
        return (
            "This endpoint rejects keyless requests "
            f"({err}) — an API key is required."
        )
    return None


def _resolve_base_url(pid: str, env_vars: dict[str, str] | None = None) -> str:
    """Resolve the actual base URL for a provider."""
    env_vars = env_vars or {}
    ptype = provider_type(pid)
    preset = PROVIDERS[ptype]
    if ptype == "custom":
        return env_vars.get("CUSTOM_BASE_URL", env_vars.get("OPENAI_API_BASE", ""))
    if ptype == "ollama":
        host = env_vars.get("OLLAMA_HOST", "http://localhost:11434")
        return f"{host}/v1"
    return preset["base_url"] or ""


def _resolve_api_key(pid: str, env_vars: dict[str, str] | None = None) -> str:
    """Resolve the API key for a provider from env vars."""
    env_vars = env_vars or {}
    ptype = provider_type(pid)
    preset = PROVIDERS[ptype]
    if ptype == "ollama":
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
