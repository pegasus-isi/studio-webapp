"""CodexCLIAdapter — knowledge adapter for OpenAI Codex CLI.

Writes AGENTS.md, ~/.codex/config.toml, .agents/skills/, and .codex/agents/
into the workspace. Codex CLI reads config.toml for model provider settings
and discovers skills/agents from the filesystem.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from typing import Any

import structlog

from knowledge import layout
from knowledge.adapters import KnowledgeAdapter
from knowledge.common import build_knowledge_appendix
from llm.providers import PROVIDERS

log = structlog.get_logger()


# MCP servers to configure in config.toml
MCP_SERVERS = {
    "pegasus-docs": "https://gitmcp.io/pegasus-isi/pegasus",
    "kiso-docs": "https://gitmcp.io/pegasus-isi/kiso",
}


def _build_config_toml(
    active_provider: str,
    active_model: str,
    providers: list[dict[str, Any]],
) -> str:
    """Build a Codex CLI config.toml with all configured providers.

    Args:
        active_provider: The provider_id to use as the default model_provider.
        active_model: The model name to use by default.
        providers: List of provider configs, each with provider_id, name,
                   api_key, base_url, default_model.
    """
    lines = []

    # Active model/provider. model_provider must name the (sanitized) table
    # key written below, so both go through _toml_key.
    lines.append(f'model = "{_toml_escape(active_model)}"')
    lines.append(f'model_provider = "{_toml_key(active_provider)}"')
    lines.append('model_reasoning_effort = "medium"')
    lines.append("")

    # Write a [model_providers.X] section for each provider
    for prov in providers:
        pid = prov.get("provider_id", prov.get("provider", "custom"))
        name = prov.get("name", pid)
        base_url = prov.get("base_url", "")
        api_key = prov.get("api_key", "")

        # Fall back to preset base_url if not set
        if not base_url:
            preset = PROVIDERS.get(pid, {})
            base_url = preset.get("base_url", "") or ""

        lines.append(f"[model_providers.{_toml_key(pid)}]")
        lines.append(f'name = "{_toml_escape(name)}"')
        if base_url:
            lines.append(f'base_url = "{_toml_escape(base_url)}"')
        # Codex >= 0.136 no longer supports wire_api = "chat" — it requires the
        # Responses API. The provider endpoint must implement /responses.
        # See https://github.com/openai/codex/discussions/7782
        lines.append('wire_api = "responses"')
        if api_key:
            lines.append(
                f'experimental_bearer_token = "{_toml_escape(api_key)}"'
            )
        lines.append("")

    # MCP server sections
    for server_name, url in MCP_SERVERS.items():
        lines.append(f"[mcp_servers.{server_name}]")
        lines.append(f'url = "{url}"')
        lines.append("")

    return "\n".join(lines)


def _seed_codex_skills(workspace: str) -> int:
    """Copy canonical skills to .agents/skills/ for Codex's native skill picker.

    Each skill gets a SKILL.md with frontmatter (name + description) that
    Codex uses to populate the skill picker UI.

    Returns count of skills seeded.
    """
    skills_src = layout.skills_dir()
    if not os.path.isdir(skills_src):
        return 0

    count = 0
    for skill_name in sorted(os.listdir(skills_src)):
        skill_dir = os.path.join(skills_src, skill_name)
        canonical = os.path.join(skill_dir, "canonical.md")
        if not os.path.isfile(canonical):
            continue

        dst_dir = os.path.join(workspace, ".agents", "skills", skill_name)
        os.makedirs(dst_dir, exist_ok=True)
        shutil.copy2(canonical, os.path.join(dst_dir, "SKILL.md"))
        count += 1

    return count


def _seed_codex_agents(workspace: str) -> int:
    """Convert agent markdown files to .codex/agents/*.toml for Codex subagents.

    Reads each agent .md file's YAML frontmatter for description, and
    uses the body as developer_instructions.

    Returns count of agents seeded.
    """
    agents_src = layout.agents_dir()
    if not os.path.isdir(agents_src):
        return 0

    codex_agents_dir = os.path.join(workspace, ".codex", "agents")
    os.makedirs(codex_agents_dir, exist_ok=True)

    count = 0
    for fname in sorted(os.listdir(agents_src)):
        if not fname.endswith(".md"):
            continue

        agent_id = fname[:-3]
        fpath = os.path.join(agents_src, fname)

        with open(fpath) as f:
            content = f.read()

        # Parse YAML frontmatter
        description = agent_id.replace("-", " ").title()
        instructions = content

        fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
        if fm_match:
            for line in fm_match.group(1).split("\n"):
                if line.startswith("description:"):
                    description = line.split(":", 1)[1].strip()
            instructions = content[fm_match.end():]

        # Escape triple quotes inside the content so they don't
        # terminate the TOML multi-line basic string prematurely.
        safe_instructions = instructions.replace('"""', '""\\\"')
        toml_lines = [
            f'name = "{_toml_escape(agent_id)}"',
            f'description = "{_toml_escape(description)}"',
            f'developer_instructions = """\n{safe_instructions}"""',
            "",
            "# MCP servers for documentation lookups",
        ]
        for server_name, url in MCP_SERVERS.items():
            toml_lines.append(f"[mcp_servers.{server_name}]")
            toml_lines.append(f'url = "{url}"')
            toml_lines.append("")

        toml_path = os.path.join(codex_agents_dir, f"{agent_id}.toml")
        with open(toml_path, "w") as f:
            f.write("\n".join(toml_lines))
        count += 1

    return count


def _toml_escape(s: str) -> str:
    """Escape a string for a single-line TOML basic string value.

    Newlines matter as much as quotes: an unescaped "\n" ends the value and
    lets the remainder of the string inject arbitrary TOML keys.
    """
    return (
        s.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )


def _toml_key(s: str) -> str:
    """Reduce a provider id to a safe TOML bare key ([model_providers.<id>]
    is a table header — escaping can't help there, only a character allowlist
    can)."""
    return re.sub(r"[^A-Za-z0-9_-]", "-", s) or "provider"


class CodexCLIAdapter(KnowledgeAdapter):
    """Translates canonical knowledge into Codex CLI's native format."""

    def install(self, workspace: str, llm_config: dict[str, Any]) -> None:
        # Copy PEGASUS_AI.md as AGENTS.md and append skills/agents appendix
        dst = os.path.join(workspace, "AGENTS.md")
        content = layout.context_document()
        if content:
            appendix = build_knowledge_appendix()
            with open(dst, "w") as f:
                f.write(content)
                if appendix:
                    f.write("\n" + appendix)
            log.info("codex_agents_md_written", path=dst)

        # Seed native Codex skills (.agents/skills/<name>/SKILL.md)
        skill_count = _seed_codex_skills(workspace)
        log.info("codex_skills_seeded", count=skill_count)

        # Seed native Codex agents (.codex/agents/<name>.toml)
        agent_count = _seed_codex_agents(workspace)
        log.info("codex_agents_seeded", count=agent_count)

        # Write config.toml (LLM config + MCP servers)
        self.update_llm_config(workspace, llm_config)

    def update_llm_config(self, workspace: str, llm_config: dict[str, Any]) -> None:
        """Rewrite ~/.codex/config.toml with all configured providers.

        llm_config may contain an 'all_providers' key with the full list
        from the provider_configs DB table. If not present, falls back to
        writing just the single active provider.
        """
        provider = llm_config.get("provider", "openai")
        model = llm_config.get("model", "")
        api_key = llm_config.get("api_key", "")
        base_url = llm_config.get("base_url", "")

        preset = PROVIDERS.get(provider, PROVIDERS.get("openai", {}))
        if not model:
            model = preset.get("default_model", "gpt-4o")

        # Build provider list from all saved providers (if any).
        all_providers = llm_config.get("all_providers")
        provider_list = list(all_providers) if all_providers else []

        # The active provider MUST have a [model_providers.<active>] section or
        # Codex aborts with "Model provider `<active>` not found". The saved
        # provider_configs list may not include the active provider (e.g. it
        # was never persisted there), so synthesize its section from the
        # active llm_config.
        def _pid(p: dict[str, Any]) -> str:
            return p.get("provider_id", p.get("provider", ""))

        if not any(_pid(p) == provider for p in provider_list):
            provider_list.insert(0, {
                "provider_id": provider,
                "name": preset.get("name", provider),
                "api_key": api_key,
                "base_url": base_url or preset.get("base_url", "") or "",
            })

        # Write to ~/.codex/config.toml (skip if HOME is not set)
        home = os.environ.get("HOME", "")
        if not home:
            return
        codex_dir = os.path.join(home, ".codex")
        os.makedirs(codex_dir, exist_ok=True)

        config_toml = _build_config_toml(
            active_provider=provider,
            active_model=model,
            providers=provider_list,
        )

        config_path = os.path.join(codex_dir, "config.toml")
        with open(config_path, "w") as f:
            f.write(config_toml)

        log.info(
            "codex_config_written",
            path=config_path,
            provider=provider,
            model=model,
            num_providers=len(provider_list),
        )

        # Also set env var for the active provider
        if api_key:
            env_var = preset.get("api_key_env")
            if env_var:
                os.environ[env_var] = api_key

    def uninstall(self, workspace: str) -> None:
        # Remove AGENTS.md from workspace
        agents_path = os.path.join(workspace, "AGENTS.md")
        if os.path.isfile(agents_path):
            os.remove(agents_path)
            log.info("codex_file_removed", path=agents_path)

        # Remove .agents/skills/ from workspace
        agents_skills = os.path.join(workspace, ".agents")
        if os.path.isdir(agents_skills):
            shutil.rmtree(agents_skills)
            log.info("codex_dir_removed", path=agents_skills)

        # Remove .codex/ from workspace (agents live here)
        codex_workspace = os.path.join(workspace, ".codex")
        if os.path.isdir(codex_workspace):
            shutil.rmtree(codex_workspace)
            log.info("codex_dir_removed", path=codex_workspace)

        # Remove ~/.codex/config.toml
        home = os.environ.get("HOME", "")
        if not home:
            return
        config_path = os.path.join(home, ".codex", "config.toml")
        if os.path.isfile(config_path):
            os.remove(config_path)
            log.info("codex_file_removed", path=config_path)

        # Remove old codex.json if it exists
        old_json = os.path.join(workspace, "codex.json")
        if os.path.isfile(old_json):
            os.remove(old_json)
