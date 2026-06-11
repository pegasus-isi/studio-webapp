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

KNOWLEDGE_ROOT = os.environ.get(
    "KNOWLEDGE_ROOT", "/opt/pegasus-ai/knowledge"
)


def build_skills_appendix() -> str:
    """Build a markdown section describing all available skills.

    Returns a string like:

        ## Available Pegasus Skills

        ### /scaffold — Scaffold
        Generate a complete Pegasus workflow project from a pipeline description
        ...
    """
    skills_dir = os.path.join(KNOWLEDGE_ROOT, "skills")
    if not os.path.isdir(skills_dir):
        return ""

    sections: list[str] = []
    sections.append("\n## Available Pegasus Skills\n")
    sections.append(
        "The following slash commands are available. When the user invokes one, "
        "follow the instructions in the corresponding skill document.\n"
    )

    for name in sorted(os.listdir(skills_dir)):
        skill_dir = os.path.join(skills_dir, name)
        if not os.path.isdir(skill_dir):
            continue

        # Read metadata
        meta_path = os.path.join(skill_dir, "metadata.json")
        slash_cmd = f"/{name}"
        display_name = name.replace("-", " ").title()
        description = ""

        if os.path.isfile(meta_path):
            try:
                with open(meta_path) as f:
                    meta = json.load(f)
                display_name = meta.get("name", display_name)
                description = meta.get("description", "")
                slash_cmd = meta.get("slash_command", slash_cmd)
            except Exception:
                pass

        sections.append(f"- **`{slash_cmd}`** — {display_name}: {description}")

    return "\n".join(sections) + "\n"


def build_agents_appendix() -> str:
    """Build a markdown section listing available agent personas.

    Returns a string like:

        ## Available Agent Personas

        - **workflow-architect**: Expert Pegasus WMS workflow designer...
        - **pipeline-debugger**: LLM-powered Pegasus workflow debugging...
    """
    agents_dir = os.path.join(KNOWLEDGE_ROOT, "agents")
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


def copy_agents_to_dir(dst_dir: str) -> int:
    """Copy all agent .md files to dst_dir. Returns count copied."""
    agents_src = os.path.join(KNOWLEDGE_ROOT, "agents")
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
    skills_src = os.path.join(KNOWLEDGE_ROOT, "skills")
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
