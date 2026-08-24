"""PiAdapter — knowledge adapter for the pi coding agent (https://pi.dev).

Writes AGENTS.md, ~/.pi/agent/models.json (custom providers) and
~/.pi/agent/settings.json (default model + skills paths). pi reads AGENTS.md
from ~/.pi/agent/, the cwd and its ancestors; skills are SKILL.md directories
discoverable via the settings ``skills`` array, so the shared checkout is
declared directly rather than copied. MCP is not built into pi — nothing to
configure there.
"""

from __future__ import annotations

import json
import os
from typing import Any

import structlog

from knowledge import layout
from knowledge.adapters import KnowledgeAdapter
from knowledge.common import build_knowledge_appendix, ensure_context_file
from llm.providers import PROVIDERS, provider_type

log = structlog.get_logger()

# Preset types written to models.json as pi custom providers. Anthropic and
# Google are pi built-ins that read ANTHROPIC_API_KEY / GEMINI_API_KEY from the
# environment and speak their native APIs — a models.json entry would have to
# guess base URLs pi already knows, so those are configured via env instead.
_OPENAI_COMPATIBLE = {"gateway", "custom", "ollama", "openai"}


def _pi_agent_dir() -> str:
    return os.path.join(os.environ.get("HOME", "/tmp"), ".pi", "agent")


def _read_json(path: str) -> dict[str, Any]:
    """Best-effort read of a JSON object; anything unreadable is {}."""
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_json(path: str, data: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


class PiAdapter(KnowledgeAdapter):
    """Translates canonical knowledge into pi's native format."""

    def install(self, workspace: str, llm_config: dict[str, Any]) -> None:
        # AGENTS.md into the workspace and pi's global agent dir, so the
        # instructions load whether pi starts in ~/work or anywhere else.
        content = layout.context_document()
        if content:
            appendix = build_knowledge_appendix()
            body = content + ("\n" + appendix if appendix else "")
            for dst_dir in (workspace, _pi_agent_dir()):
                try:
                    os.makedirs(dst_dir, exist_ok=True)
                    with open(os.path.join(dst_dir, "AGENTS.md"), "w") as f:
                        f.write(body)
                except OSError as e:
                    log.warning("pi_agents_write_failed", dir=dst_dir, error=str(e))

        self.update_llm_config(workspace, llm_config)
        log.info("pi_installed", workspace=workspace)

    def update_llm_config(self, workspace: str, llm_config: dict[str, Any]) -> None:
        """Rewrite models.json and merge settings.json with the current config."""
        # Runs on every start, so a deleted AGENTS.md comes back here — the
        # same healing the other adapters do.
        ensure_context_file(os.path.join(workspace, "AGENTS.md"))
        ensure_context_file(os.path.join(_pi_agent_dir(), "AGENTS.md"))

        provider = llm_config.get("provider", "gateway")
        ptype = provider_type(provider)
        model = llm_config.get("model")
        api_key = llm_config.get("api_key", "")
        base_url = llm_config.get("base_url", "")
        preset = PROVIDERS.get(ptype, PROVIDERS["custom"])
        if not model:
            model = preset.get("default_model") or ""
        model_id = str(model).rsplit("/", 1)[-1]

        # Same shape as the opencode adapter: every saved provider is written,
        # with the active one synthesized when it has no saved row and its live
        # llm_config winning over a stale row.
        saved = list(llm_config.get("all_providers") or [])
        if not any(p.get("provider_id") == provider for p in saved):
            saved.insert(0, {
                "provider_id": provider,
                "name": preset.get("name", provider),
                "api_key": api_key,
                "base_url": base_url,
                "default_model": model_id,
            })

        providers_cfg: dict[str, Any] = {}
        for prov in saved:
            pid = prov.get("provider_id") or provider
            p_type = provider_type(pid)
            if p_type not in _OPENAI_COMPATIBLE:
                continue
            p_preset = PROVIDERS.get(p_type, PROVIDERS["custom"])
            p_key = prov.get("api_key") or ""
            p_base = prov.get("base_url") or p_preset.get("base_url") or ""
            if pid == provider:
                p_key, p_base = api_key or p_key, base_url or p_base
            if not p_base:
                continue  # a models.json entry without a URL cannot work
            p_model = str(
                prov.get("default_model")
                or (model_id if pid == provider else "")
                or p_preset.get("default_model")
                or ""
            ).rsplit("/", 1)[-1]
            # Full fetched model list when the propagator attached one (#207);
            # a hand-typed default missing from the list is still declared.
            model_ids = [
                str(m).rsplit("/", 1)[-1] for m in prov.get("models") or []
            ]
            if p_model and p_model not in model_ids:
                model_ids.insert(0, p_model)
            entry: dict[str, Any] = {
                "baseUrl": p_base,
                "api": "openai-completions",
                "models": [{"id": m, "name": m} for m in model_ids],
            }
            if p_key:
                entry["apiKey"] = p_key
            elif p_type == "ollama":
                entry["apiKey"] = "ollama"  # pi's SDK wants *some* key value
            providers_cfg[pid] = entry

        agent_dir = _pi_agent_dir()
        _write_json(
            os.path.join(agent_dir, "models.json"), {"providers": providers_cfg}
        )

        # settings.json also holds user choices (theme, compaction, ...), so it
        # is merged, never replaced.
        settings_path = os.path.join(agent_dir, "settings.json")
        settings = _read_json(settings_path)
        settings["defaultProvider"] = provider
        if model_id:
            settings["defaultModel"] = model_id
        skills_dir = layout.skills_dir()
        if os.path.isdir(skills_dir):
            skills = [s for s in settings.get("skills") or [] if s]
            if skills_dir not in skills:
                skills.append(skills_dir)
            settings["skills"] = skills
        _write_json(settings_path, settings)

        log.info(
            "pi_config_written",
            provider=provider,
            model=model_id,
            num_providers=len(providers_cfg),
        )

        # Built-in pi providers read keys from the environment; export the
        # active provider's key so terminals inherit it (same as codex).
        if api_key:
            env_var = preset.get("api_key_env")
            if env_var:
                os.environ[env_var] = api_key
            if ptype == "google":
                # pi's Gemini provider reads GEMINI_API_KEY, not GOOGLE_API_KEY.
                os.environ.setdefault("GEMINI_API_KEY", api_key)

    def uninstall(self, workspace: str) -> None:
        agent_dir = _pi_agent_dir()
        for name in ("models.json", "AGENTS.md"):
            path = os.path.join(agent_dir, name)
            try:
                if os.path.isfile(path):
                    os.remove(path)
            except OSError:
                pass

        # Drop only what this adapter set in settings.json; the rest (theme,
        # compaction, user-added skill paths) belongs to the user.
        settings_path = os.path.join(agent_dir, "settings.json")
        settings = _read_json(settings_path)
        if settings:
            settings.pop("defaultProvider", None)
            settings.pop("defaultModel", None)
            skills_dir = layout.skills_dir()
            skills = [s for s in settings.get("skills") or [] if s != skills_dir]
            if skills:
                settings["skills"] = skills
            else:
                settings.pop("skills", None)
            try:
                _write_json(settings_path, settings)
            except OSError:
                pass
        log.info("pi_uninstalled", workspace=workspace)
