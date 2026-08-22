"""OpenCodeAdapter — knowledge adapter for OpenCode.

Ported from config_builder.py:build_opencode_config.
"""

from __future__ import annotations

import json
import os
import shutil
from typing import Any

import structlog

from knowledge import layout
from knowledge.adapters import KnowledgeAdapter
from knowledge.common import (
    build_knowledge_appendix,
    ensure_context_file,
)
from llm.providers import PROVIDERS, provider_type

log = structlog.get_logger()



def _provider_options(ptype: str, api_key: str, base_url: str) -> dict[str, str]:
    """Build one provider's ``options`` block.

    Native SDK providers (anthropic, openai) take only an apiKey — a baseURL
    would point the SDK away from the vendor API. Ollama is keyless but its
    SDK still requires *some* apiKey value. Everything else is treated as
    OpenAI-compatible: baseURL required, key optional (self-hosted endpoints
    are often keyless).
    """
    options: dict[str, str] = {}
    if ptype in ("anthropic", "openai"):
        if api_key:
            options["apiKey"] = api_key
    elif ptype == "ollama":
        options["baseURL"] = base_url or "http://localhost:11434/v1"
        options["apiKey"] = "ollama"
    else:
        if base_url:
            options["baseURL"] = base_url
        if api_key:
            options["apiKey"] = api_key
    return options


def _global_config_dir() -> str:
    """OpenCode's global config dir (XDG). Config here applies from ANY cwd —
    the workspace-only config was picked up only when opencode started in
    ~/work, so terminals launched elsewhere (PVC mounts at ~) fell back to the
    default 'Zen' provider."""
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
        os.environ.get("HOME", "/tmp"), ".config"
    )
    return os.path.join(base, "opencode")


def _link_agents_into_global() -> int:
    """Symlink each agent file into opencode's global agent directory.

    ``~/.config/opencode/agent/<name>.md`` is the location opencode documents
    for agents that should be available from any cwd. Individual files are
    linked into a real directory rather than symlinking the directory itself:
    the loader globs for ``<name>.md``, and a link per file is read through
    ``open()`` like any other file.

    Stale links (an agent removed upstream) are dropped, so the directory always
    reflects the checkout.
    """
    src = layout.agents_dir()
    dst = os.path.join(_global_config_dir(), "agent")
    if not os.path.isdir(src):
        return 0

    try:
        os.makedirs(dst, exist_ok=True)
    except OSError as e:
        log.warning("opencode_agent_dir_failed", dir=dst, error=str(e))
        return 0

    wanted = {n for n in os.listdir(src) if n.endswith(".md")}

    # Drop links we previously made that no longer have a source. Only links are
    # removed: an agent the user wrote by hand is theirs, not ours to delete.
    for name in os.listdir(dst):
        path = os.path.join(dst, name)
        if os.path.islink(path) and name not in wanted:
            try:
                os.unlink(path)
            except OSError:
                pass

    linked = 0
    for name in sorted(wanted):
        target, path = os.path.join(src, name), os.path.join(dst, name)
        try:
            if os.path.islink(path) or os.path.exists(path):
                if os.path.realpath(path) == os.path.realpath(target):
                    linked += 1
                    continue
                os.unlink(path)
            os.symlink(target, path)
            linked += 1
        except OSError as e:
            log.warning("opencode_agent_link_failed", agent=name, error=str(e))
    return linked


class OpenCodeAdapter(KnowledgeAdapter):
    """Translates canonical knowledge into OpenCode's native format."""

    def install(self, workspace: str, llm_config: dict[str, Any]) -> None:
        # Copy PEGASUS_AI.md as AGENTS.md — into the workspace and the global
        # config dir, so the config's `instructions: ["AGENTS.md"]` resolves
        # whether opencode starts in ~/work or anywhere else.
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
                    log.warning("opencode_agents_write_failed", dir=dst_dir, error=str(e))

        _link_agents_into_global()

        # Write opencode.json (global — read from any cwd)
        self.update_llm_config(workspace, llm_config)
        log.info("opencode_installed", workspace=workspace)

    def update_llm_config(self, workspace: str, llm_config: dict[str, Any]) -> None:
        """Rewrite opencode.json with the current provider config."""
        # Runs on every start, so it is also where a deleted AGENTS.md comes back
        # and where the agent links are (re)made. install() alone was not
        # enough: startup only calls this, so when the config root moved to $HOME
        # the links were never created there and the assistant ran with no skills
        # at all — improvising instead of following pegasus-scaffold.
        ensure_context_file(os.path.join(workspace, "AGENTS.md"))
        ensure_context_file(os.path.join(_global_config_dir(), "AGENTS.md"))
        _link_agents_into_global()
        provider = llm_config.get("provider", "anthropic")
        ptype = provider_type(provider)  # custom-<id> -> "custom", etc.
        model = llm_config.get("model")
        api_key = llm_config.get("api_key", "")
        base_url = llm_config.get("base_url", "")

        global_dir = _global_config_dir()
        preset = PROVIDERS.get(ptype, PROVIDERS["anthropic"])
        if not model:
            model = preset.get("default_model", "claude-sonnet-4-5-20250929")
        # The opencode `model` field is "<providerID>/<modelID>". Strip any
        # provider prefix that may already be on the stored model so we build a
        # clean id (e.g. "default"), not "custom-x/default" doubled up.
        model_id = str(model).rsplit("/", 1)[-1]

        # Every saved provider goes into the config, not just the active one:
        # opencode's provider/model picker lists exactly what this map declares,
        # so a custom endpoint that was saved but never activated used to be
        # invisible in the TUI (#205). The active provider may not have a saved
        # row (legacy configs written straight to llm_config), so synthesize it.
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
            p_preset = PROVIDERS.get(p_type, PROVIDERS["custom"])
            p_key = prov.get("api_key") or ""
            p_base = prov.get("base_url") or p_preset.get("base_url") or ""
            if pid == provider:
                # The live llm_config wins over the saved row for the active
                # provider, so a key/URL edited via PUT /config isn't shadowed
                # by a stale provider_configs row.
                p_key, p_base = api_key or p_key, base_url or p_base
            p_model = str(
                prov.get("default_model")
                or (model_id if pid == provider else "")
                or p_preset.get("default_model")
                or ""
            ).rsplit("/", 1)[-1]
            # Declare the endpoint's full model list when the propagator
            # fetched one (opencode's picker lists only what is declared, so
            # default_model alone meant one selectable model per endpoint,
            # #207). No list — endpoint down, keyless probe rejected — falls
            # back to default_model; a draft with neither still shows up
            # (empty model list) rather than silently vanishing.
            model_ids = [
                str(m).rsplit("/", 1)[-1] for m in prov.get("models") or []
            ]
            if p_model and p_model not in model_ids:
                model_ids.insert(0, p_model)
            providers_cfg[pid] = {
                "npm": p_preset.get("npm", "@ai-sdk/openai-compatible"),
                "name": prov.get("name") or p_preset.get("name", pid),
                "options": _provider_options(p_type, p_key, p_base),
                "models": {m: {"name": m} for m in model_ids},
            }

        config = {
            "$schema": "https://opencode.ai/config.json",
            "provider": providers_cfg,
            "model": f"{provider}/{model_id}",
            # Absolute, not "AGENTS.md": a bare name is resolved against the
            # project opencode walks up to, which is whatever directory the
            # process happened to start in.
            "instructions": [os.path.join(global_dir, "AGENTS.md")],
            "mcp": {
                "pegasus-docs": {
                    "type": "remote",
                    "url": "https://gitmcp.io/pegasus-isi/pegasus",
                    "enabled": True,
                },
                "kiso-docs": {
                    "type": "remote",
                    "url": "https://gitmcp.io/pegasus-isi/kiso",
                    "enabled": True,
                },
            },
        }

        # Point the skill loader straight at the checkout. Declaring the path is
        # what makes discovery independent of where opencode starts: the loader
        # otherwise finds skills by walking up from the cwd, so what the model
        # can invoke depends on the directory a process happened to launch in.
        # It also keeps one source — the checkout — rather than a linked copy
        # per tool, where two trees offering the same skill names left which one
        # answered down to directory-scan order.
        skills_dir = layout.skills_dir()
        if os.path.isdir(skills_dir):
            config["skills"] = {"paths": [skills_dir]}

        # Global config only. It is read from any cwd, so the workspace copy
        # this used to also write was redundant — byte-identical content in a
        # second place to keep in sync, and the only non-hidden file Studio put
        # in the directory users are told to keep workflows in. uninstall()
        # still removes the legacy copy for workspaces that have one.
        blob = json.dumps(config, indent=2)
        targets = [os.path.join(global_dir, "opencode.json")]
        for path in targets:
            try:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w") as f:
                    f.write(blob)
                log.info("opencode_config_written", path=path)
            except OSError as e:
                log.warning("opencode_config_write_failed", path=path, error=str(e))

    def uninstall(self, workspace: str) -> None:
        paths = [
            os.path.join(workspace, t)
            for t in ("AGENTS.md", "opencode.json", ".opencode")
        ]
        # Everything we write under $HOME: the global config, its context file,
        # and the agent links.
        gdir = _global_config_dir()
        paths += [
            os.path.join(gdir, "opencode.json"),
            os.path.join(gdir, "AGENTS.md"),
            os.path.join(gdir, "agent"),
        ]
        for path in paths:
            try:
                if os.path.isfile(path):
                    os.remove(path)
                elif os.path.isdir(path):
                    shutil.rmtree(path)
            except OSError:
                pass
        log.info("opencode_uninstalled", workspace=workspace)
