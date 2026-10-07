"""CrushAdapter — knowledge adapter for Crush (charmbracelet/crush).

Writes AGENTS.md and ~/.config/crush/crush.json. Crush's config carries
multiple named providers (matching Studio's provider_configs one-to-one),
a large/small model selection, MCP servers, and — via options — extra
context files and skill directories, so the shared marketplace checkout is
declared directly rather than copied. Schema: https://charm.land/crush.json
"""

from __future__ import annotations

import json
import os
from typing import Any

import structlog

from knowledge import layout
from knowledge.adapters import KnowledgeAdapter
from knowledge.common import (
    build_knowledge_appendix,
    ensure_context_file,
    model_limits_by_bare_id,
    record_generated_file,
    remove_if_unmodified,
    remove_generated_context_file,
    rmdir_if_empty,
)
from llm.providers import PROVIDERS, provider_type

log = structlog.get_logger()

# Preset types written to crush.json as openai-compat providers. Anthropic and
# Google stay off the map: they are Crush built-ins (Catwalk) with full model
# catalogs that read ANTHROPIC_API_KEY / GEMINI_API_KEY from the environment —
# a hand-written entry would shadow that catalog with a one-model list.
_OPENAI_COMPATIBLE = {"gateway", "custom", "ollama", "openai"}

# The crush.json model entry requires sizing/cost fields we cannot know for
# arbitrary gateway endpoints. Conservative defaults: a context window small
# enough to be safe everywhere, zero cost (the common case here is an internal
# gateway), no reasoning/attachment claims.
def _crush_limits(lim: dict[str, int] | None) -> dict[str, int]:
    """Map a ``{"context", "output"}`` limit onto Crush's field names."""
    out: dict[str, int] = {}
    if lim and "context" in lim:
        out["context_window"] = lim["context"]
    if lim and "output" in lim:
        out["default_max_tokens"] = lim["output"]
    return out


_MODEL_DEFAULTS = {
    "context_window": 128_000,
    "default_max_tokens": 8_192,
    "cost_per_1m_in": 0,
    "cost_per_1m_out": 0,
    "cost_per_1m_in_cached": 0,
    "cost_per_1m_out_cached": 0,
    "can_reason": False,
    "supports_attachments": False,
}


def _looks_like_our_config(text: str) -> bool:
    """A crush.json written by update_llm_config(): it always carries the
    Studio MCP servers. A user's own config would not name gitmcp.io/pegasus."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return False
    mcp = data.get("mcp") if isinstance(data, dict) else None
    if not isinstance(mcp, dict):
        return False
    docs = mcp.get("pegasus-docs")
    return isinstance(docs, dict) and docs.get("url") == "https://gitmcp.io/pegasus-isi/pegasus"


def _global_config_dir() -> str:
    """Crush's global config dir (XDG), so config applies from any cwd."""
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
        os.environ.get("HOME", "/tmp"), ".config"
    )
    return os.path.join(base, "crush")


class CrushAdapter(KnowledgeAdapter):
    """Translates canonical knowledge into Crush's native format."""

    def install(self, workspace: str, llm_config: dict[str, Any]) -> None:
        # AGENTS.md into the workspace and the global config dir, declared in
        # crush.json's context_paths so it loads whether crush starts in
        # ~/work or anywhere else.
        content = layout.context_document()
        if content:
            appendix = build_knowledge_appendix()
            body = content + ("\n" + appendix if appendix else "")
            for dst_dir in (workspace, _global_config_dir()):
                try:
                    os.makedirs(dst_dir, exist_ok=True)
                    with open(os.path.join(dst_dir, "AGENTS.md"), "w") as f:
                        f.write(body)
                except OSError as e:
                    log.warning("crush_agents_write_failed", dir=dst_dir, error=str(e))

        self.update_llm_config(workspace, llm_config)
        log.info("crush_installed", workspace=workspace)

    def update_llm_config(self, workspace: str, llm_config: dict[str, Any]) -> None:
        """Rewrite ~/.config/crush/crush.json with the current provider config."""
        # Runs on every start, so a deleted AGENTS.md comes back here — the
        # same healing the other adapters do.
        ensure_context_file(os.path.join(workspace, "AGENTS.md"))
        global_dir = _global_config_dir()
        ensure_context_file(os.path.join(global_dir, "AGENTS.md"))

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
                continue  # a provider entry without a URL cannot work
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
            limits = model_limits_by_bare_id(prov)
            entry: dict[str, Any] = {
                "name": prov.get("name") or p_preset.get("name", pid),
                "type": "openai-compat",
                "base_url": p_base,
                # A reported context window / output cap (propagator,
                # ``model_limits``) overrides the placeholder defaults, which
                # are only there because Crush requires the fields; the real
                # window is what its summarizer compacts against.
                "models": [
                    {"id": m, "name": m, **_MODEL_DEFAULTS, **_crush_limits(limits.get(m))}
                    for m in model_ids
                ],
            }
            if p_key:
                entry["api_key"] = p_key
            providers_cfg[pid] = entry

        # The active selection. Crush picks its working models from the
        # "large"/"small" entries; both point at the active choice so agent
        # and summarizer turns land on the same endpoint.
        selection = {"model": model_id, "provider": provider}

        config: dict[str, Any] = {
            "$schema": "https://charm.land/crush.json",
            "providers": providers_cfg,
            "models": {"large": selection, "small": dict(selection)},
            "mcp": {
                "pegasus-docs": {
                    "type": "http",
                    "url": "https://gitmcp.io/pegasus-isi/pegasus",
                },
                "kiso-docs": {
                    "type": "http",
                    "url": "https://gitmcp.io/pegasus-isi/kiso",
                },
            },
            "options": {
                # Absolute, so the context loads regardless of the directory
                # crush happens to start in.
                "context_paths": [os.path.join(global_dir, "AGENTS.md")],
            },
        }

        # Point the skill loader straight at the checkout — one source, same
        # rationale as the opencode adapter.
        skills_dir = layout.skills_dir()
        if os.path.isdir(skills_dir):
            config["options"]["skills_paths"] = [skills_dir]

        config_path = os.path.join(global_dir, "crush.json")
        try:
            os.makedirs(global_dir, exist_ok=True)
            with open(config_path, "w") as f:
                json.dump(config, f, indent=2)
                f.write("\n")
            record_generated_file(config_path)
            log.info(
                "crush_config_written",
                path=config_path,
                provider=provider,
                model=model_id,
                num_providers=len(providers_cfg),
            )
        except OSError as e:
            log.warning("crush_config_write_failed", path=config_path, error=str(e))

        # Crush's built-in providers (Anthropic, Gemini, ...) read keys from
        # the environment; export the active provider's key so terminals
        # inherit it (same as codex).
        if api_key:
            env_var = preset.get("api_key_env")
            if env_var:
                os.environ[env_var] = api_key
            if ptype == "google":
                # Crush's Gemini provider reads GEMINI_API_KEY, not GOOGLE_API_KEY.
                os.environ.setdefault("GEMINI_API_KEY", api_key)

    def uninstall(self, workspace: str) -> None:
        # ``workspace`` is $HOME and ~/.config/crush is the tool's own config
        # dir, which the user may have populated by hand (a crush.json of
        # their own, themes). Only what update_llm_config() wrote goes: the
        # context file by its generator fingerprint, crush.json by the Studio
        # MCP servers it always carries, and the directory only if that
        # emptied it — the same rule the other adapters follow (NEW-01).
        remove_generated_context_file(os.path.join(workspace, "AGENTS.md"))
        global_dir = _global_config_dir()
        remove_generated_context_file(os.path.join(global_dir, "AGENTS.md"))
        remove_if_unmodified(os.path.join(global_dir, "crush.json"), _looks_like_our_config)
        rmdir_if_empty(global_dir)
        log.info("crush_uninstalled", workspace=workspace)
