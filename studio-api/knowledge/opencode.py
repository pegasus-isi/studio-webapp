"""OpenCodeAdapter — knowledge adapter for OpenCode.

Ported from config_builder.py:build_opencode_config.
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
    remove_file_if,
    remove_generated_context_file,
    rmdir_if_empty,
    unlink_if_links_into,
)
from llm.providers import PROVIDERS, _resolve_api_key, _resolve_base_url, provider_type

log = structlog.get_logger()



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


def _looks_like_our_config(text: str) -> bool:
    """An opencode.json written by update_llm_config(): it always carries the
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

        # Build provider config
        options: dict[str, str] = {}
        if ptype == "anthropic":
            if api_key:
                options["apiKey"] = api_key
        elif ptype == "openai":
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

        config = {
            "$schema": "https://opencode.ai/config.json",
            "provider": {
                provider: {
                    "npm": preset.get("npm", "@ai-sdk/openai-compatible"),
                    "name": preset.get("name", provider),
                    "options": options,
                    "models": {model_id: {"name": model_id}},
                },
            },
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
            # The command-approval gate for the opencode engine (NEW-02).
            # opencode emits `permission.asked` only for rules set to "ask",
            # and its 1.18.8 default ruleset is `"*": "allow"` with asks only
            # for doom_loop, external directories and *.env reads — so without
            # this key the engine's forwarder (services/opencode_engine.py)
            # never fires and the Settings promise ("pauses before running any
            # shell command") is false on the engine serving every session.
            #
            # Only `bash` is set. This config wins over every built-in and
            # marketplace agent ruleset (opencode merges user config last and
            # takes the last match), so it reaches subagents too. Deliberately
            # NOT `"*": "ask"` — gating read/glob/grep/list makes the assistant
            # unusable — and not `edit: "ask"`: the workspace is the user's own
            # and a prompt per scaffolded file is the kind of friction that
            # gets the toggle switched off. Everything else keeps opencode's
            # defaults (edit allow, external_directory ask, doom_loop ask,
            # *.env reads ask).
            #
            # Emitted regardless of the Studio toggle: opencode always asks and
            # the engine answers automatically when approval is off. That keeps
            # the toggle live without restarting serve, which reads this file
            # only at startup. To tune later, `bash` also accepts a pattern
            # map: {"*": "ask", "pegasus-status *": "allow"}.
            "permission": {
                "bash": "ask",
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
        # ``workspace`` is $HOME. Only what we generated goes, file by file.
        remove_generated_context_file(os.path.join(workspace, "AGENTS.md"))
        # Workspaces seeded before the workspace copy was dropped still have
        # one; at $HOME a file by that name is the user's unless it is ours.
        remove_file_if(os.path.join(workspace, "opencode.json"), _looks_like_our_config)
        # <workspace>/.opencode is not handled here: it was only ever written
        # under ~/work, which installer.uninstall() prunes by exact name.

        gdir = _global_config_dir()
        remove_generated_context_file(os.path.join(gdir, "AGENTS.md"))
        remove_file_if(os.path.join(gdir, "opencode.json"), _looks_like_our_config)

        # Only the links we made. A hand-written agent beside them stays —
        # the same rule _link_agents_into_global() applies on every refresh.
        agent_dir = os.path.join(gdir, "agent")
        if os.path.isdir(agent_dir):
            for name in os.listdir(agent_dir):
                unlink_if_links_into(os.path.join(agent_dir, name))
        rmdir_if_empty(agent_dir)
        rmdir_if_empty(gdir)
        log.info("opencode_uninstalled", workspace=workspace)
