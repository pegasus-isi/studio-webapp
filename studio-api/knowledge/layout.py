"""Where the Pegasus AI knowledge tree lives, and what shape it has.

The skills, agents, templates and examples are authored once, in
pegasus-isi/claude-plugin-marketplace, and reach this container as the installed
``pegasus_ai_knowledge`` package. Studio does not carry its own copy: a second
copy is what let the two drift apart, to the point where Claude Code users were
told to build Apptainer images while opencode users were told to build Docker
ones.

Everything that needs the tree goes through this module, so there is one
resolution order and one place that knows the on-disk shape.

Two layouts are accepted, deliberately:

* the plugin layout (``skills/<name>/SKILL.md``, ``.mcp.json``,
  ``assets/templates``), which is what ships today
* the older Studio layout (``skills/<name>/canonical.md`` + ``metadata.json``,
  ``mcp/servers.json``, ``templates/``)

The second is kept because tests build fixture trees in it and because a
container built before the switch may still have one on disk. It is not
something to author against.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

import structlog

log = structlog.get_logger()

# Studio-specific context that does NOT belong in the shared plugin: which
# directories persist, where runs land, what the file browser can see. Composed
# on top of the portable guide when generating a tool's context file.
OVERLAY_ROOT = os.environ.get("KNOWLEDGE_OVERLAY", "/opt/pegasus-ai/overlay")

# Portable reference doc, as named in the plugin. The legacy name is the
# condensed Studio restatement that the overlay replaces.
_REFERENCE_NAMES = ("PEGASUS.md", "PEGASUS_AI.md")

_FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def knowledge_root() -> str:
    """Resolve the root of the knowledge tree.

    Order:

    1. ``KNOWLEDGE_ROOT``, so a developer or a test can point at a scratch tree
    2. the installed ``pegasus_ai_knowledge`` package, which is the real answer
       in a built image
    3. ``/opt/pegasus-ai/knowledge``, the path older images copied into

    Resolved on each call rather than cached at import: tests monkeypatch the
    environment variable, and an import-time constant would freeze the first
    value read.
    """
    env = os.environ.get("KNOWLEDGE_ROOT")
    if env:
        return env

    try:
        import pegasus_ai_knowledge

        return str(pegasus_ai_knowledge.plugin_dir())
    except Exception as exc:  # ImportError, or the plugin missing from the wheel
        log.debug("knowledge_package_unavailable", error=str(exc))

    return "/opt/pegasus-ai/knowledge"


def skills_dir() -> str:
    return os.path.join(knowledge_root(), "skills")


def agents_dir() -> str:
    return os.path.join(knowledge_root(), "agents")


def _first_existing(*paths: str) -> str | None:
    for path in paths:
        if os.path.exists(path):
            return path
    return None


def templates_dir() -> str | None:
    root = knowledge_root()
    return _first_existing(
        os.path.join(root, "assets", "templates"), os.path.join(root, "templates")
    )


def examples_dir() -> str | None:
    root = knowledge_root()
    return _first_existing(
        os.path.join(root, "assets", "examples"), os.path.join(root, "examples")
    )


def mcp_servers_path() -> str | None:
    root = knowledge_root()
    return _first_existing(
        os.path.join(root, ".mcp.json"), os.path.join(root, "mcp", "servers.json")
    )


def skill_names() -> list[str]:
    """Return the names of skills present, sorted."""
    directory = skills_dir()
    if not os.path.isdir(directory):
        return []
    return sorted(
        name
        for name in os.listdir(directory)
        if skill_document(name) is not None
    )


def skill_document(name: str) -> str | None:
    """Return the path to a skill's markdown body, or None if there isn't one."""
    skill_dir = os.path.join(skills_dir(), name)
    return _first_existing(
        os.path.join(skill_dir, "SKILL.md"), os.path.join(skill_dir, "canonical.md")
    )


def parse_frontmatter(path: str) -> dict[str, Any]:
    """Parse a markdown file's YAML frontmatter, shallowly.

    Deliberately not a YAML parser. Only scalar ``key: value`` pairs at the top
    level are read, which covers ``name``, ``description`` and ``order``; nested
    keys such as ``allowed-tools`` are skipped rather than mis-parsed. Folded
    values (``description: >``) continue onto following indented lines and are
    joined, because the kiso skill uses one.
    """
    try:
        with open(path) as fh:
            content = fh.read()
    except OSError:
        return {}

    match = _FRONTMATTER.match(content)
    if not match:
        return {}

    data: dict[str, Any] = {}
    key: str | None = None
    folded: list[str] = []

    for line in match.group(1).split("\n"):
        if line[:1] in (" ", "\t"):
            if key and folded is not None:
                folded.append(line.strip())
            continue

        if key and folded:
            data[key] = " ".join(folded).strip()
        key, folded = None, []

        if ":" not in line or line.lstrip().startswith("#"):
            continue

        raw_key, _, raw_value = line.partition(":")
        raw_key = raw_key.strip()
        value = raw_value.strip()

        if value in (">", "|", ">-", "|-", ""):
            # Folded/literal scalar, or a nested block we will ignore unless
            # indented lines follow.
            key, folded = raw_key, []
            continue

        data[raw_key] = value.strip("'\"")

    if key and folded:
        data[key] = " ".join(folded).strip()

    return data


def skill_meta(name: str) -> dict[str, Any]:
    """Return a skill's display metadata.

    Frontmatter is the source of truth, so a skill's description cannot disagree
    with the skill itself. ``metadata.json`` is still read when present, for
    fixture trees built in the older layout, but frontmatter wins.
    """
    skill_dir = os.path.join(skills_dir(), name)
    meta: dict[str, Any] = {
        "name": name.replace("-", " ").title(),
        "description": "",
        "slash_command": f"/{name}",
        "order": 999,
    }

    legacy = os.path.join(skill_dir, "metadata.json")
    if os.path.isfile(legacy):
        try:
            with open(legacy) as fh:
                meta.update(json.load(fh))
        except Exception as exc:
            log.warning("skill_metadata_unreadable", skill=name, error=str(exc))

    document = skill_document(name)
    if document:
        front = parse_frontmatter(document)
        if front.get("name"):
            meta["slash_command"] = f"/{front['name']}"
        if front.get("description"):
            meta["description"] = front["description"]
        if front.get("order"):
            try:
                meta["order"] = int(front["order"])
            except (TypeError, ValueError):
                pass

    return meta


def reference_document() -> str | None:
    """Return the portable reference guide shipped with the knowledge tree."""
    root = knowledge_root()
    return _first_existing(
        *(os.path.join(root, "references", name) for name in _REFERENCE_NAMES)
    )


def overlay_document() -> str | None:
    """Return the Studio environment overlay, if this deployment has one."""
    return _first_existing(os.path.join(OVERLAY_ROOT, "STUDIO_ENV.md"))


def context_document() -> str:
    """Build the base context written into CLAUDE.md / AGENTS.md / context.md.

    The environment overlay comes first: a model that writes a workflow into a
    directory that does not persist has failed regardless of how well it knows
    Pegasus.
    """
    parts: list[str] = []

    overlay = overlay_document()
    if overlay:
        with open(overlay) as fh:
            parts.append(fh.read().rstrip("\n"))

    reference = reference_document()
    if reference:
        with open(reference) as fh:
            parts.append(fh.read().rstrip("\n"))

    if not parts:
        log.warning("knowledge_context_empty", root=knowledge_root())

    return "\n\n".join(parts) + "\n" if parts else ""
