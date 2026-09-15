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
        # A skill that declares deliverables gets them stated here, in the
        # always-on context. The requirement is defined once, in the skill —
        # this renders it rather than restating it. Without this the obligation
        # lived only inside a skill the model had to choose to load, and a
        # weaker model would finish a workflow with no README or run_manual.sh
        # and report it complete.
        required = meta.get("deliverables")
        if isinstance(required, list) and required:
            sections.append(
                "  Every workflow it produces must contain, before you call it "
                "done: " + ", ".join(f"`{r}`" for r in required) + "."
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


def build_paths_appendix() -> str:
    """State where the knowledge tree actually is, as an absolute path.

    Skills refer to their reference guide and templates by paths relative to the
    plugin directory (``references/PEGASUS.md``, ``assets/templates/...``), which
    is correct for the plugin layout but resolves against nothing useful at
    runtime: the chat starts in the user's workspace, and the tree is under
    ``~/.local/share/pegasus-ai/marketplace``. Nothing in the generated context
    named that directory, so the model guessed — a pod log shows it reaching for
    ``$HOME/references/PEGASUS.md``, which does not exist, and then scaffolding a
    workflow with neither the guide nor any template.

    The resolved root is known here, so it is stated here. One place computes it
    and every skill refers to it, rather than each skill hardcoding a path that
    is wrong for some other consumer of the same tree.
    """
    root = layout.knowledge_root()
    if not os.path.isdir(root):
        return ""

    lines = [
        "\n## Where the Pegasus knowledge base lives\n",
        f"The skills, reference guide and templates are at `{root}`.\n",
        "When a skill tells you to read a path like `references/PEGASUS.md` or "
        "`assets/templates/workflow_generator_template.py`, that path is "
        "relative to the directory above, **not** to your working directory. "
        "Resolve it there.\n",
    ]

    # Name the two subdirectories explicitly. A model that has the root can
    # derive these, but the whole failure being fixed here was a model deriving
    # a path when it had no basis to, so they are spelled out.
    for label, path in (
        ("Reference guide", layout.reference_document()),
        ("Templates", layout.templates_dir()),
        ("Worked examples", layout.examples_dir()),
    ):
        if path:
            lines.append(f"- {label}: `{path}`")

    return "\n".join(lines) + "\n"


def build_knowledge_appendix() -> str:
    """Build a combined paths + skills + agents appendix for context files."""
    return build_paths_appendix() + build_skills_appendix() + build_agents_appendix()


def context_body() -> str:
    """The generated context document plus the skills/agents appendix."""
    content = layout.context_document()
    if not content:
        return ""
    appendix = build_knowledge_appendix()
    return content + ("\n" + appendix if appendix else "")


# Phrases only the generated document contains. An unmarked file carrying one
# was written by an older build and is ours to replace; an unmarked file without
# one was written by the user and must survive.
_GENERATED_PHRASES = (
    "## Available Pegasus Skills",
    "## Available Agent Personas",
    "## Where the Pegasus knowledge base lives",
    "Pegasus AI Studio — Environment",
)


def _looks_generated(text: str) -> bool:
    return any(p in text for p in _GENERATED_PHRASES)


def ensure_context_file(path: str) -> bool:
    """Write, and keep current, the generated context document at ``path``.

    These files are generated by a tool's install(), but startup only runs
    update_llm_config() — so once written they were never touched again. A user
    who had a CLAUDE.md from last week kept last week's instructions no matter
    what shipped: the image carried corrected guidance while the assistant went
    on reading the stale copy, which is indistinguishable from the fix never
    having been made.

    Written inside a managed block so it can be refreshed without touching
    anything the user added around it. A file with no block predates this and
    was generated wholesale by an earlier build, so it is replaced.

    Returns True if the file changed.
    """
    body = context_body()
    if not body:
        return False

    existing = ""
    if os.path.isfile(path):
        try:
            with open(path) as f:
                existing = f.read()
        except OSError as e:
            log.warning("context_file_unreadable", path=path, error=str(e))
            return False

    try:
        if existing and _BLOCK_BEGIN in existing:
            # Preserves whatever the user put outside the block.
            before = existing
            upsert_managed_block(path, body)
            with open(path) as f:
                return f.read() != before
        block = f"{_BLOCK_BEGIN}\n{body.rstrip()}\n{_BLOCK_END}\n"
        if existing == block:
            return False
        if existing and not _looks_generated(existing):
            # Someone wrote this by hand. Keep it, and put the managed block
            # above it so it can be refreshed from here on.
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "w") as f:
                f.write(block + "\n" + existing.lstrip())
            log.info("context_file_wrapped_user_content", path=path)
            return True
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w") as f:
            f.write(block)
    except OSError as e:
        log.warning("context_file_write_failed", path=path, error=str(e))
        return False

    log.info("context_file_written", path=path, refreshed=bool(existing))
    return True


# Generated tool config that used to be written into the workspace when the
# workspace was $HOME/work. Terminals and tools start in $HOME, so a file one
# directory down was invisible unless the user happened to cd into it — the
# same cwd problem the $HOME-scoped copies were added to work around. These are
# pruned from $HOME/work so a copy left there cannot win when a shell starts in
# it. The workspace itself is $HOME now (#401), which the prune below refuses
# to touch: that is where the live files are.
LEGACY_WORKSPACE_FILES = (
    "CLAUDE.md",
    "AGENTS.md",
    "opencode.json",
    ".mcp.json",
)
# Generated *inside* those directories. Listed individually rather than removing
# the parent, so a user's own project settings sitting beside ours (a
# .claude/settings.local.json, say) are not collateral. Empty parents are
# cleaned up afterwards.
LEGACY_WORKSPACE_PATHS = (
    os.path.join(".opencode", "skills"),
    os.path.join(".opencode", "agents"),
    os.path.join(".opencode", "opencode.json"),
    os.path.join(".agents", "skills"),
    os.path.join(".codex", "agents"),
    os.path.join(".claude", "settings.json"),
    os.path.join(".antigravity", "context.md"),
)

# Parents to remove once whatever we generated inside them is gone — and only
# if nothing else remains.
LEGACY_WORKSPACE_PARENTS = (
    ".opencode",
    ".agents",
    ".codex",
    ".claude",
    ".antigravity",
)


def config_root() -> str:
    """Where generated tool config belongs: the user's ``$HOME``.

    Every tool here looks for its config in the directory it starts in and then
    upwards, and terminals start in ``$HOME``. Writing to ``$HOME/work`` meant
    the files existed but were not found.
    """
    return os.environ.get("HOME", "/tmp")


def prune_legacy_workspace_copies(workspace: str) -> list[str]:
    """Remove generated config left behind in the workspace.

    Skipped when the workspace *is* the config root, so this can never delete
    the live files. Only ever touches paths this code generates.
    """
    root = os.path.realpath(config_root())
    if not workspace or os.path.realpath(workspace) == root:
        return []

    removed: list[str] = []

    def _drop(rel: str) -> None:
        path = os.path.join(workspace, rel)
        try:
            if os.path.islink(path):
                os.unlink(path)
            elif os.path.isdir(path):
                shutil.rmtree(path)
            elif os.path.exists(path):
                os.unlink(path)
            else:
                return
        except OSError:
            return
        removed.append(rel)

    for name in LEGACY_WORKSPACE_FILES:
        _drop(name)
    for rel in LEGACY_WORKSPACE_PATHS:
        _drop(rel)

    # Only empty ones: anything the user put there keeps the directory alive.
    for name in LEGACY_WORKSPACE_PARENTS:
        path = os.path.join(workspace, name)
        try:
            if os.path.isdir(path) and not os.path.islink(path) and not os.listdir(path):
                os.rmdir(path)
                removed.append(name)
        except OSError:
            pass

    if removed:
        log.info("legacy_workspace_config_pruned", workspace=workspace, removed=removed)
    return removed


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


def link_tree(src: str, dst: str) -> bool:
    """Point ``dst`` at ``src`` with a symlink, replacing whatever was there.

    Tools want skills under their own directory (``.opencode/skills``,
    ``.agents/skills``). Copying them there made a per-user duplicate that went
    stale the moment the shared checkout moved: a workspace seeded weeks ago
    still offered the skills of that week, and nothing ever refreshed it. Live
    pods were found carrying three different versions of the same skills.

    A symlink gives the tool the layout it expects while there stays exactly one
    copy on disk, so a ``git pull`` in the checkout is visible everywhere at
    once. An existing real directory is removed first — it is generated product
    output, not the user's work, and leaving it would keep the stale copy
    winning.

    Falls back to copying where symlinks are unavailable. Returns True if a
    symlink was made.
    """
    if not os.path.isdir(src):
        return False

    os.makedirs(os.path.dirname(dst.rstrip("/")) or ".", exist_ok=True)

    if os.path.islink(dst):
        if os.path.realpath(dst) == os.path.realpath(src):
            return True
        os.unlink(dst)
    elif os.path.isdir(dst):
        shutil.rmtree(dst)
    elif os.path.exists(dst):
        os.unlink(dst)

    try:
        os.symlink(src, dst, target_is_directory=True)
        return True
    except OSError as e:
        log.warning("knowledge_symlink_failed_copying", src=src, dst=dst, error=str(e))
        shutil.copytree(src, dst)
        return False


def link_agents_to_dir(dst_dir: str) -> bool:
    """Point a tool's agents directory at the shared checkout."""
    return link_tree(layout.agents_dir(), dst_dir)


def link_skills_to_dir(dst_dir: str) -> bool:
    """Point a tool's skills directory at the shared checkout.

    The checkout already uses ``<skill>/SKILL.md``, which is the layout every
    tool here expects, so no per-skill rewriting is needed.
    """
    return link_tree(layout.skills_dir(), dst_dir)
