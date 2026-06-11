"""Knowledge layer endpoints — skills, agents, templates, examples."""

from __future__ import annotations

import json
import os
import re

import structlog
from fastapi import APIRouter, HTTPException

from models import AgentInfo, SkillMetadata, SkillResponse

log = structlog.get_logger()

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])

KNOWLEDGE_ROOT = os.environ.get(
    "KNOWLEDGE_ROOT", "/opt/pegasus-ai/knowledge"
)


@router.get("/skills")
async def list_skills() -> dict[str, list[SkillMetadata]]:
    """Scan KNOWLEDGE_ROOT/skills/*/metadata.json for skill definitions."""
    skills_dir = os.path.join(KNOWLEDGE_ROOT, "skills")
    skills: list[SkillMetadata] = []

    if not os.path.isdir(skills_dir):
        return {"skills": skills}

    for name in sorted(os.listdir(skills_dir)):
        meta_path = os.path.join(skills_dir, name, "metadata.json")
        if os.path.isfile(meta_path):
            try:
                with open(meta_path) as f:
                    data = json.load(f)
                skills.append(SkillMetadata(
                    name=data.get("name", name),
                    description=data.get("description", ""),
                    slash_command=data.get("slash_command"),
                ))
            except Exception:
                skills.append(SkillMetadata(name=name))
        else:
            # Check for canonical.md directly
            canonical = os.path.join(skills_dir, name, "canonical.md")
            if os.path.isfile(canonical):
                skills.append(SkillMetadata(name=name))

    return {"skills": skills}


@router.get("/skills/{name}", response_model=SkillResponse)
async def get_skill(name: str) -> SkillResponse:
    """Read a skill's canonical.md content."""
    skill_dir = os.path.join(KNOWLEDGE_ROOT, "skills", name)
    canonical = os.path.join(skill_dir, "canonical.md")

    if not os.path.isfile(canonical):
        raise HTTPException(status_code=404, detail=f"Skill not found: {name}")

    with open(canonical) as f:
        content = f.read()

    # Load metadata
    meta_path = os.path.join(skill_dir, "metadata.json")
    metadata = SkillMetadata(name=name)
    if os.path.isfile(meta_path):
        try:
            with open(meta_path) as f:
                data = json.load(f)
            metadata = SkillMetadata(
                name=data.get("name", name),
                description=data.get("description", ""),
                slash_command=data.get("slash_command"),
            )
        except Exception:
            pass

    return SkillResponse(name=name, content=content, metadata=metadata)


@router.get("/agents")
async def list_agents() -> dict[str, list[AgentInfo]]:
    """Scan agents/*.md, parse YAML frontmatter for name/description.

    Ported from handlers.py:_load_agents().
    """
    agents_dir = os.path.join(KNOWLEDGE_ROOT, "agents")
    agents: list[AgentInfo] = [
        AgentInfo(
            id="general",
            name="General",
            description="General-purpose Pegasus WMS workflow assistant",
        )
    ]

    if not os.path.isdir(agents_dir):
        return {"agents": agents}

    for fname in sorted(os.listdir(agents_dir)):
        if not fname.endswith(".md"):
            continue
        agent_id = fname[:-3]
        fpath = os.path.join(agents_dir, fname)
        try:
            with open(fpath) as f:
                content = f.read()

            name = agent_id.replace("-", " ").title()
            description = ""

            # Parse YAML frontmatter
            fm_match = re.match(
                r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL
            )
            if fm_match:
                frontmatter = fm_match.group(1)
                for line in frontmatter.split("\n"):
                    if line.startswith("name:"):
                        name = line.split(":", 1)[1].strip()
                    elif line.startswith("description:"):
                        description = line.split(":", 1)[1].strip()

            agents.append(AgentInfo(
                id=agent_id, name=name, description=description,
            ))
        except Exception:
            agents.append(AgentInfo(id=agent_id, name=agent_id))

    return {"agents": agents}


@router.get("/templates")
async def list_templates() -> dict[str, list[dict[str, str]]]:
    """List template files from templates/ directory."""
    templates_dir = os.path.join(KNOWLEDGE_ROOT, "templates")
    templates: list[dict[str, str]] = []

    if not os.path.isdir(templates_dir):
        return {"templates": templates}

    for fname in sorted(os.listdir(templates_dir)):
        fpath = os.path.join(templates_dir, fname)
        if os.path.isfile(fpath):
            templates.append({"name": fname, "path": fpath})

    return {"templates": templates}


@router.get("/examples")
async def list_examples() -> dict[str, list[dict[str, str]]]:
    """List example workflow files from examples/ directory."""
    examples_dir = os.path.join(KNOWLEDGE_ROOT, "examples")
    examples: list[dict[str, str]] = []

    if not os.path.isdir(examples_dir):
        return {"examples": examples}

    for fname in sorted(os.listdir(examples_dir)):
        fpath = os.path.join(examples_dir, fname)
        if os.path.isfile(fpath):
            examples.append({"name": fname, "path": fpath})

    return {"examples": examples}
