"""CLI script to seed LLM config from ~/.pegasus-ai/.env into the database."""

from __future__ import annotations

import asyncio
import os

from db import Database


def _load_env_file() -> dict[str, str]:
    """Read LLM configuration from ~/.pegasus-ai/.env, falling back to os.environ."""
    env_path = os.path.join(
        os.environ.get("HOME", "/tmp"), ".pegasus-ai", ".env"
    )
    env_vars: dict[str, str] = {}
    try:
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, _, value = line.partition("=")
                    env_vars[key.strip()] = value.strip()
    except FileNotFoundError:
        pass

    _ENV_KEYS = [
        "LLM_PROVIDER", "LLM_MODEL",
        "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
        "FABRIC_AI_API_KEY", "NRP_API_KEY",
        "CUSTOM_BASE_URL", "CUSTOM_API_KEY",
        "OLLAMA_HOST",
    ]
    for key in _ENV_KEYS:
        if key not in env_vars and os.environ.get(key):
            env_vars[key] = os.environ[key]

    return env_vars


_PROVIDER_KEY_MAP = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "fabric": "FABRIC_AI_API_KEY",
    "nrp": "NRP_API_KEY",
    "custom": "CUSTOM_API_KEY",
}

_PROVIDER_DEFAULTS = {
    "anthropic": {"model": "claude-sonnet-4-5-20250929"},
    "openai": {"model": "gpt-4o"},
    "fabric": {"model": "qwen3-coder-30b", "base_url": "https://ai.fabric-testbed.net/v1"},
    "nrp": {"model": "qwen3-coder-30b", "base_url": "https://ellm.nrp-nautilus.io/v1"},
    "custom": {},
    "ollama": {"model": "qwen2.5-coder:7b"},
}


async def main() -> None:
    env_vars = _load_env_file()
    provider = env_vars.get("LLM_PROVIDER", "")
    if not provider:
        print("No LLM_PROVIDER found — skipping seed.")
        return

    db = Database()
    await db.connect()

    # Check if config already exists
    existing = await db.get_llm_config()
    if existing:
        print("LLM config already exists — skipping seed.")
        await db.close()
        return

    # Resolve config from env
    defaults = _PROVIDER_DEFAULTS.get(provider, {})
    model = env_vars.get("LLM_MODEL", defaults.get("model"))
    api_key = None
    key_env = _PROVIDER_KEY_MAP.get(provider)
    if key_env:
        api_key = env_vars.get(key_env)

    base_url = defaults.get("base_url")
    if provider == "custom":
        base_url = env_vars.get("CUSTOM_BASE_URL", env_vars.get("OPENAI_API_BASE"))
    elif provider == "ollama":
        host = env_vars.get("OLLAMA_HOST", "http://localhost:11434")
        base_url = f"{host}/v1"

    await db.set_llm_config(
        provider=provider,
        model=model,
        api_key=api_key,
        base_url=base_url,
    )
    print(f"Seeded LLM config: provider={provider}, model={model}")

    await db.close()


if __name__ == "__main__":
    asyncio.run(main())
