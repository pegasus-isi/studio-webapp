"""Knowledge layer endpoints — skills, agents, templates, examples."""

from __future__ import annotations

import asyncio
import os

import structlog
from fastapi import APIRouter, HTTPException

from knowledge import layout
from models import AgentInfo, SkillMetadata, SkillResponse

log = structlog.get_logger()

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])

@router.get("/skills")
async def list_skills() -> dict[str, list[SkillMetadata]]:
    """List the skills in the knowledge tree, in presentation order."""
    # Sort by the "order" field (workflow-authoring importance), falling back to
    # alphabetical for any skill without one.
    collected: list[tuple[int, str, SkillMetadata]] = []

    for name in layout.skill_names():
        meta = layout.skill_meta(name)
        collected.append((
            int(meta.get("order", 1000)),
            name,
            SkillMetadata(
                name=meta.get("name", name),
                description=meta.get("description", ""),
                slash_command=meta.get("slash_command"),
            ),
        ))

    collected.sort(key=lambda t: (t[0], t[1]))
    return {"skills": [s for _, _, s in collected]}


@router.get("/skills/{name}", response_model=SkillResponse)
async def get_skill(name: str) -> SkillResponse:
    """Read a skill's markdown body."""
    document = layout.skill_document(name)
    if not document:
        raise HTTPException(status_code=404, detail=f"Skill not found: {name}")

    with open(document) as f:
        content = f.read()

    meta = layout.skill_meta(name)
    metadata = SkillMetadata(
        name=meta.get("name", name),
        description=meta.get("description", ""),
        slash_command=meta.get("slash_command"),
    )

    return SkillResponse(name=name, content=content, metadata=metadata)


@router.get("/agents")
async def list_agents() -> dict[str, list[AgentInfo]]:
    """Scan agents/*.md, parse YAML frontmatter for name/description.

    Ported from handlers.py:_load_agents().
    """
    agents_dir = layout.agents_dir()
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
            front = layout.parse_frontmatter(fpath)
            agents.append(AgentInfo(
                id=agent_id,
                name=front.get("name") or agent_id.replace("-", " ").title(),
                description=front.get("description", ""),
            ))
        except Exception:
            agents.append(AgentInfo(id=agent_id, name=agent_id))

    return {"agents": agents}


@router.get("/templates")
async def list_templates() -> dict[str, list[dict[str, str]]]:
    """List template files shipped with the knowledge tree."""
    templates_dir = layout.templates_dir()
    templates: list[dict[str, str]] = []

    if not templates_dir or not os.path.isdir(templates_dir):
        return {"templates": templates}

    for fname in sorted(os.listdir(templates_dir)):
        fpath = os.path.join(templates_dir, fname)
        if os.path.isfile(fpath):
            templates.append({"name": fname, "path": fpath})

    return {"templates": templates}


@router.get("/examples")
async def list_examples() -> dict[str, list[dict[str, str]]]:
    """List example workflow files shipped with the knowledge tree."""
    examples_dir = layout.examples_dir()
    examples: list[dict[str, str]] = []

    if not examples_dir or not os.path.isdir(examples_dir):
        return {"examples": examples}

    for fname in sorted(os.listdir(examples_dir)):
        fpath = os.path.join(examples_dir, fname)
        if os.path.isfile(fpath):
            examples.append({"name": fname, "path": fpath})

    return {"examples": examples}


@router.get("/marketplace")
async def marketplace_status() -> dict[str, object]:
    """Which marketplace checkout this session reads, and at which commit.

    The SHA is the version — there is no package number to consult — so this is
    the only way a user can tell which skills they are running.
    """
    from knowledge import checkout

    return checkout.status()


@router.post("/marketplace/update")
async def marketplace_update(force: bool = False) -> dict[str, object]:
    """Fast-forward the checkout to upstream.

    Declines rather than discards when the checkout is dirty or carries local
    commits; the reason comes back in the response so the UI can say why.
    """
    from knowledge import checkout

    return await asyncio.to_thread(checkout.update, force=force)
