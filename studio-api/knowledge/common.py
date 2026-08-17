"""Shared utilities for knowledge propagation across AI tool adapters.

Provides helpers to build skill/agent appendices that can be appended to
any tool's context file (CLAUDE.md, AGENTS.md, context.md, etc.).
"""

from __future__ import annotations

import json
import os
import re
import shutil
from typing import Any

import structlog

log = structlog.get_logger()

from knowledge import layout


def build_skills_appendix() -> str:
    """Build a markdown section describing all available skills.

    Returns a string like:

        ## Available Pegasus Skills

        ### /scaffold — Scaffold
        Generate a complete Pegasus workflow project from a pipeline description
        ...
    """
    if not layout.skill_names():
        return ""

    sections: list[str] = []
    sections.append("\n## Available Pegasus Skills\n")
    sections.append(
        "The following slash commands are available. When the user invokes one, "
        "follow the instructions in the corresponding skill document.\n"
    )

    for name, meta in sorted(
        ((n, layout.skill_meta(n)) for n in layout.skill_names()),
        key=lambda item: (int(item[1].get("order", 999)), item[0]),
    ):
        sections.append(
            f"- **`{meta['slash_command']}`** — {meta['name']}: "
            f"{meta['description']}"
        )

    return "\n".join(sections) + "\n"


def build_agents_appendix() -> str:
    """Build a markdown section listing available agent personas.

    Returns a string like:

        ## Available Agent Personas

        - **workflow-architect**: Expert Pegasus WMS workflow designer...
        - **pipeline-debugger**: LLM-powered Pegasus workflow debugging...
    """
    agents_dir = layout.agents_dir()
    if not os.path.isdir(agents_dir):
        return ""

    sections: list[str] = []
    sections.append("\n## Available Agent Personas\n")
    sections.append(
        "You can adopt any of the following specialist personas when the user "
        "requests domain-specific help.\n"
    )

    for fname in sorted(os.listdir(agents_dir)):
        if not fname.endswith(".md"):
            continue
        agent_id = fname[:-3]
        fpath = os.path.join(agents_dir, fname)

        name = agent_id.replace("-", " ").title()
        description = ""

        try:
            with open(fpath) as f:
                content = f.read()
            fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
            if fm_match:
                for line in fm_match.group(1).split("\n"):
                    if line.startswith("name:"):
                        name = line.split(":", 1)[1].strip()
                    elif line.startswith("description:"):
                        description = line.split(":", 1)[1].strip()
        except Exception:
            pass

        sections.append(f"- **{agent_id}**: {description or name}")

    return "\n".join(sections) + "\n"


def build_knowledge_appendix() -> str:
    """Build a combined skills + agents appendix for context files."""
    return build_skills_appendix() + build_agents_appendix()


def home_path(*parts: str) -> str:
    """Resolve a path under the user's HOME.

    In Studio the PVC mounts at ``~``, so HOME is the persistent, always-present
    root. Tool config placed here is picked up regardless of the cwd a terminal
    starts in (the workspace ``~/work`` is only visible when the tool is launched
    from there). Falls back to ``/tmp`` if HOME is unset.
    """
    return os.path.join(os.environ.get("HOME", "/tmp"), *parts)


_BLOCK_BEGIN = "<!-- BEGIN PEGASUS-AI KNOWLEDGE (managed by Studio) -->"
_BLOCK_END = "<!-- END PEGASUS-AI KNOWLEDGE (managed by Studio) -->"
_BLOCK_RE = re.compile(
    re.escape(_BLOCK_BEGIN) + r".*?" + re.escape(_BLOCK_END) + r"\n?",
    re.DOTALL,
)


def upsert_managed_block(path: str, content: str) -> None:
    """Insert or replace a Studio-managed block in a markdown file.

    The rest of the file is preserved untouched — the target may be the user's
    own global memory (e.g. ``~/.claude/CLAUDE.md``). Idempotent: re-running
    replaces the previous managed block rather than stacking copies.
    """
    existing = ""
    if os.path.isfile(path):
        try:
            with open(path) as f:
                existing = f.read()
        except OSError:
            existing = ""
    stripped = _BLOCK_RE.sub("", existing).rstrip()
    block = f"{_BLOCK_BEGIN}\n{content.rstrip()}\n{_BLOCK_END}\n"
    new = (stripped + "\n\n" + block) if stripped else block
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        f.write(new)


def remove_managed_block(path: str) -> None:
    """Remove the Studio-managed block; delete the file if nothing else remains."""
    if not os.path.isfile(path):
        return
    try:
        with open(path) as f:
            existing = f.read()
    except OSError:
        return
    stripped = _BLOCK_RE.sub("", existing).strip()
    try:
        if stripped:
            with open(path, "w") as f:
                f.write(stripped + "\n")
        else:
            os.remove(path)
    except OSError:
        pass


def merge_json_mapping(path: str, top_key: str, entries: dict[str, Any]) -> None:
    """Read-merge ``entries`` into ``file[top_key]``, preserving all other content.

    Used for user-scope config that co-exists with the user's own state, such as
    MCP servers in ``~/.claude.json``. Creates the file if absent.
    """
    data: dict[str, Any] = {}
    if os.path.isfile(path):
        try:
            with open(path) as f:
                data = json.load(f) or {}
        except (json.JSONDecodeError, OSError):
            data = {}
    section = data.get(top_key)
    if not isinstance(section, dict):
        section = {}
    section.update(entries)
    data[top_key] = section
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def unmerge_json_mapping(path: str, top_key: str, keys: list[str]) -> None:
    """Remove specific ``keys`` from ``file[top_key]``, preserving everything else.

    Drops the ``top_key`` entirely if it becomes empty. No-op if the file or key
    is absent — safe to call on uninstall regardless of prior state.
    """
    if not os.path.isfile(path):
        return
    try:
        with open(path) as f:
            data = json.load(f) or {}
    except (json.JSONDecodeError, OSError):
        return
    section = data.get(top_key)
    if isinstance(section, dict):
        for k in keys:
            section.pop(k, None)
        if section:
            data[top_key] = section
        else:
            data.pop(top_key, None)
    try:
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
    except OSError:
        pass


def update_json(path: str, updates: dict[str, Any]) -> None:
    """Read-merge top-level ``updates`` into a JSON file, preserving other keys.

    Creates the file if absent. Used for flat config files that co-exist with a
    tool's own settings (e.g. a global ``settings.json``).
    """
    data: dict[str, Any] = {}
    if os.path.isfile(path):
        try:
            with open(path) as f:
                data = json.load(f) or {}
        except (json.JSONDecodeError, OSError):
            data = {}
    data.update(updates)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def unmerge_json_top(path: str, keys: list[str]) -> None:
    """Remove top-level ``keys`` from a JSON file; delete it if nothing remains.

    No-op if the file is absent. Safe to call on uninstall regardless of state.
    """
    if not os.path.isfile(path):
        return
    try:
        with open(path) as f:
            data = json.load(f) or {}
    except (json.JSONDecodeError, OSError):
        return
    for k in keys:
        data.pop(k, None)
    try:
        if data:
            with open(path, "w") as f:
                json.dump(data, f, indent=2)
        else:
            os.remove(path)
    except OSError:
        pass


def copy_agents_to_dir(dst_dir: str) -> int:
    """Copy all agent .md files to dst_dir. Returns count copied."""
    agents_src = layout.agents_dir()
    if not os.path.isdir(agents_src):
        return 0

    os.makedirs(dst_dir, exist_ok=True)
    count = 0
    for fname in os.listdir(agents_src):
        if fname.endswith(".md"):
            shutil.copy2(
                os.path.join(agents_src, fname),
                os.path.join(dst_dir, fname),
            )
            count += 1
    return count


def copy_skills_to_dir(dst_dir: str, filename: str = "SKILL.md") -> int:
    """Copy all skill canonical.md files to dst_dir/<skill>/<filename>.

    Returns count copied.
    """
    skills_src = layout.skills_dir()
    if not os.path.isdir(skills_src):
        return 0

    count = 0
    for skill_name in os.listdir(skills_src):
        canonical = os.path.join(skills_src, skill_name, "canonical.md")
        if os.path.isfile(canonical):
            skill_dst = os.path.join(dst_dir, skill_name)
            os.makedirs(skill_dst, exist_ok=True)
            shutil.copy2(canonical, os.path.join(skill_dst, filename))
            count += 1
    return count
