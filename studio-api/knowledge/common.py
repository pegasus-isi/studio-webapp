"""Shared utilities for knowledge propagation across AI tool adapters.

Provides helpers to build skill/agent appendices that can be appended to
any tool's context file (CLAUDE.md, AGENTS.md, context.md, etc.).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from collections.abc import Callable
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


def model_limits_by_bare_id(prov: dict[str, Any]) -> dict[str, dict[str, int]]:
    """A provider's ``model_limits`` keyed the way the adapters name models.

    The propagator keys limits by the id the listing reported, which may be
    namespaced (``qwen/qwen3.8-27b`` on OpenRouter-style gateways), while
    every adapter declares the bare id (``rsplit("/", 1)[-1]``) so the
    stored default_model and the listing agree. Looked up by the bare id, a
    namespaced entry would silently lose its limits — exactly the model that
    needed them. Both spellings are keyed; a bare id that was reported
    directly wins over one derived from a namespaced id. Only positive
    integer ``context`` / ``output`` values survive.
    """
    raw = prov.get("model_limits") or {}
    out: dict[str, dict[str, int]] = {}

    def clean(lim: Any) -> dict[str, int]:
        if not isinstance(lim, dict):
            return {}
        return {
            k: v for k, v in lim.items()
            if k in ("context", "output")
            and isinstance(v, int) and not isinstance(v, bool) and v > 0
        }

    for mid, lim in raw.items():
        cleaned = clean(lim)
        if not cleaned:
            continue
        bare = str(mid).rsplit("/", 1)[-1]
        if bare != mid:
            out.setdefault(bare, cleaned)
    for mid, lim in raw.items():
        cleaned = clean(lim)
        if cleaned:
            out[str(mid)] = cleaned
    return out


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


# --- uninstall primitives ------------------------------------------------------
#
# The adapters' uninstall() receives config_root() == $HOME, where ``.claude``,
# ``.codex``, ``.agents``, ``.antigravity`` and ``.config/opencode`` are the
# tools' real state: credentials, transcripts, sessions, the user's own skills
# and agents. The helpers below are the only way adapters remove anything, and
# each one deletes only what it can positively identify as Studio-generated.
# Leaving a stale generated file behind is acceptable; deleting something the
# user or the tool wrote is not (NEW-01).


def _config_home() -> str:
    return os.environ.get("XDG_CONFIG_HOME") or os.path.join(config_root(), ".config")


def is_protected_dir(path: str) -> bool:
    """True for ``$HOME`` and anything one level below it (``~/.claude``,
    ``~/.codex``, ``~/.agents``, ``~/.antigravity``, ``~/work``), and for one
    level below the XDG config dir (``~/.config/opencode``).

    Those directories belong to a tool or to the user. Studio only ever puts
    individual files inside them, so nothing in this package has a reason to
    delete one recursively — and doing so took Claude Code credentials and
    every transcript with it (NEW-01). Follows ``realpath`` so an alias to one
    of them is caught too.
    """
    real = os.path.realpath(path)
    for root in (os.path.realpath(config_root()), os.path.realpath(_config_home())):
        if real == root or os.path.dirname(real) == root:
            return True
    return False


def safe_rmtree(path: str) -> bool:
    """The one recursive delete knowledge code is allowed to call.

    Refuses protected directories (see ``is_protected_dir``) instead of
    raising: a refusal must never turn an uninstall into a 500 that leaves the
    DB row behind. Returns True if something was removed.
    """
    if is_protected_dir(path):
        log.error("rmtree_refused_protected_dir", path=path)
        return False
    if not os.path.isdir(path) or os.path.islink(path):
        return False
    shutil.rmtree(path)
    return True


def rmdir_if_empty(path: str) -> bool:
    """Remove a directory Studio may have created — only if nothing is in it.

    ``os.rmdir`` fails on a non-empty directory; that failure is the guarantee.
    """
    if os.path.islink(path) or not os.path.isdir(path):
        return False
    if os.path.realpath(path) == os.path.realpath(config_root()):
        return False
    try:
        os.rmdir(path)
    except OSError:
        return False
    log.info("empty_generated_dir_removed", path=path)
    return True


def remove_generated_context_file(path: str) -> bool:
    """Undo ensure_context_file()/upsert_managed_block()/a wholesale install write.

    The managed block is always dropped. The file itself is deleted only when
    what remains is ours: nothing, a phrase only the generated document
    contains, or exactly the document we would generate now (install() and the
    entrypoint write it wholesale, without markers). Anything else was written
    by the user and is put back without the block. Returns True if the file
    was deleted.
    """
    if os.path.islink(path) or not os.path.isfile(path):
        return False
    try:
        with open(path) as f:
            existing = f.read()
    except OSError:
        return False

    remainder = _BLOCK_RE.sub("", existing).strip()
    ours = (
        not remainder
        or _looks_generated(remainder)
        or remainder == context_body().strip()
        or remainder == layout.context_document().strip()
    )
    try:
        if ours:
            os.remove(path)
            log.info("generated_context_file_removed", path=path)
            return True
        if remainder != existing.strip():
            with open(path, "w") as f:
                f.write(remainder + "\n")
            log.info("managed_block_removed_user_content_kept", path=path)
    except OSError as e:
        log.warning("context_file_cleanup_failed", path=path, error=str(e))
    return False


def knowledge_roots() -> list[str]:
    """Realpaths a Studio-made symlink may point into.

    More than ``knowledge_root()``: the link may predate a change in which
    root wins (image seed vs. the user's checkout), and a stale link into the
    old root is still ours to remove.
    """
    roots = {
        layout.knowledge_root(),
        layout.marketplace_root(),
        layout.user_marketplace_root(),
        layout.SITE_MARKETPLACE,
        "/opt/pegasus-ai/knowledge",
    }
    return [os.path.realpath(r) for r in roots if r]


def unlink_if_links_into(path: str, roots: list[str] | None = None) -> bool:
    """Remove ``path`` only if it is a symlink into the knowledge tree.

    A real directory under the same name — the user's own ``~/.agents/skills``,
    an agent they wrote by hand — is left exactly as it is. A dangling link
    still resolves to a path string, so a link into a root that has since been
    deleted is recognised and removed too.
    """
    if not os.path.islink(path):
        return False
    target = os.path.realpath(path)
    roots = knowledge_roots() if roots is None else roots
    if not any(target == r or target.startswith(r + os.sep) for r in roots):
        log.info("symlink_kept_not_into_knowledge_tree", path=path, target=target)
        return False
    try:
        os.unlink(path)
    except OSError:
        return False
    log.info("knowledge_symlink_removed", path=path)
    return True


def remove_file_if(path: str, looks_ours: Callable[[str], bool]) -> bool:
    """Delete a regular file only when its content passes ``looks_ours``.

    A predicate that raises (malformed JSON, say) counts as "not ours": the
    file stays.
    """
    if os.path.islink(path) or not os.path.isfile(path):
        return False
    try:
        with open(path) as f:
            text = f.read()
    except OSError:
        return False
    try:
        if not looks_ours(text):
            log.info("file_kept_not_generated", path=path)
            return False
    except Exception:
        return False
    try:
        os.remove(path)
    except OSError:
        return False
    log.info("generated_file_removed", path=path)
    return True


# ---- generated-file manifest ----------------------------------------------
#
# A fingerprint ("this JSON names the Studio MCP server") tells a file Studio
# wrote apart from one the user wrote from scratch. It cannot tell a file
# Studio wrote apart from one Studio wrote and the user then edited — a
# provider added by hand to opencode.json, a profile tuned in config.toml —
# and deleting that on uninstall throws away the user's work just as surely
# as deleting ~/.claude did. So every whole-file config write is recorded
# here with a hash of its content, and uninstall removes the file only while
# it still matches. Edited means it is the user's now, and it stays.


def _generated_manifest_path() -> str:
    home = os.environ.get("HOME", "")
    base = os.path.join(home, ".studio") if home else "/tmp/studio-state"
    return os.path.join(base, "generated-files.json")


def _read_manifest() -> dict[str, str]:
    try:
        with open(_generated_manifest_path()) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_manifest(data: dict[str, str]) -> None:
    path = _generated_manifest_path()
    if not data:
        # Nothing left to remember: leave no trace, so an uninstall of the
        # last tool returns a pristine home.
        try:
            os.remove(path)
        except OSError:
            pass
        rmdir_if_empty(os.path.dirname(path))
        return
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(data, f, indent=2, sort_keys=True)
    except OSError as e:
        log.warning("generated_manifest_write_failed", path=path, error=str(e))


def _sha256(path: str) -> str | None:
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return None



def write_private(path: str, text: str) -> None:
    """Write a config file that holds a credential: 0600, in a 0700 directory.

    Homes are 0755 and the umask is 022, so a plain open() leaves an API key
    readable by every account that mounts the home (on the hosted service,
    other users' jobs on the access point). Created 0600 from the start
    rather than chmod'd after, so there is no window, and via a temp file and
    a rename so a reader never sees half a file.
    """
    directory = os.path.dirname(path)
    os.makedirs(directory, mode=0o700, exist_ok=True)
    try:
        os.chmod(directory, 0o700)
    except OSError:
        pass
    tmp = f"{path}.{os.getpid()}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.chmod(tmp, 0o600)  # O_CREAT's mode does nothing if tmp already existed
    os.replace(tmp, path)


def record_generated_file(path: str) -> None:
    """Remember the content Studio just wrote to ``path``.

    Call after every whole-file config write (opencode.json, crush.json,
    config.toml). ``remove_if_unmodified`` then knows whether the file is
    still Studio's or has become the user's.
    """
    digest = _sha256(path)
    if digest is None:
        return
    data = _read_manifest()
    data[os.path.abspath(path)] = digest
    _write_manifest(data)


def remove_if_unmodified(path: str, looks_ours: Callable[[str], bool]) -> bool:
    """Remove a generated config only if it is still exactly what Studio wrote.

    With a manifest record: byte-identical means ours, remove it and drop
    the record; anything else means the user edited it since, and it stays
    with a warning in the log. The record stays with it: dropping it would
    leave the next uninstall with no evidence but the content fingerprint,
    which an edited copy still passes — and the file that survived the
    first uninstall would go on the second.

    Without a record (a config written before the manifest existed) the only
    evidence is the content, so this falls back to the ``looks_ours``
    fingerprint — the best that can be done for an install that predates
    this, and the reason every write now records.
    """
    key = os.path.abspath(path)
    data = _read_manifest()
    recorded = data.get(key)
    if os.path.islink(path) or not os.path.isfile(path):
        if recorded is not None:
            del data[key]
            _write_manifest(data)
        return False
    if recorded is None:
        return remove_file_if(path, looks_ours)
    if _sha256(path) != recorded:
        log.warning("generated_file_kept_user_modified", path=path)
        return False
    try:
        os.remove(path)
    except OSError:
        return False
    del data[key]
    _write_manifest(data)
    log.info("generated_file_removed", path=path)
    return True


def remove_json_if_empty(path: str) -> bool:
    """Delete a JSON file that is exactly ``{}`` once our keys are unmerged."""
    if os.path.islink(path) or not os.path.isfile(path):
        return False
    try:
        with open(path) as f:
            if json.load(f) != {}:
                return False
        os.remove(path)
    except (json.JSONDecodeError, OSError):
        return False
    return True


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
