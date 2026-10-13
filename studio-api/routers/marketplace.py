"""Workflow Marketplace — browse Pegasus workflows from GitHub and clone one
into the workspace.

Hackathon scope: the catalog is the **curated trio** (quickstart,
somospie-pegasus-workflow, earthquake-workflow) defined in `_CURATED` below.
The live PegasusHub registry fetch (`_fetch_registry`) is intentionally not
called, so the catalog is deterministic and offline-friendly. "Add" performs
a shallow ``git clone`` of the selected repo into
``<workspace>/workflows/<id>`` so it shows up on the Workflows page.
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from typing import Any

import httpx
import structlog
from fastapi import APIRouter, HTTPException
from workspace import workflows_dir, workspace_root

log = structlog.get_logger()

router = APIRouter(prefix="/api/marketplace", tags=["marketplace"])

# PegasusHub's official machine-readable registry (community catalog).
REGISTRY_URL = os.environ.get(
    "STUDIO_MARKETPLACE_REGISTRY_URL",
    "https://raw.githubusercontent.com/pegasushub/workflow-registry/main/data/workflows.json",
)
_FETCH_TIMEOUT = float(os.environ.get("STUDIO_MARKETPLACE_TIMEOUT", "10"))
_CACHE_TTL = float(os.environ.get("STUDIO_MARKETPLACE_TTL", "3600"))
_CLONE_TIMEOUT = float(os.environ.get("STUDIO_MARKETPLACE_CLONE_TIMEOUT", "180"))
# Kill-switch for the GitHub description lookup. Off in tests, which must not
# reach the network, and available to an operator behind a strict egress policy.
_ENRICH = os.environ.get("STUDIO_MARKETPLACE_ENRICH", "1") not in ("0", "false", "")

WORKSPACE_ROOT = workspace_root()
WORKFLOWS_DIR = workflows_dir(WORKSPACE_ROOT)

# Curated Pegasus-ISI workflows to surface. Hackathon scope: exactly three —
# quickstart (Hello-World onramp), somospie (soil-moisture downscaling), and
# earthquake (USGS science example). Descriptions from each repo's GitHub metadata.
_CURATED: list[dict[str, str]] = [
    {
        "id": "quickstart",
        "name": "quickstart-workflow",
        "description": "A simple Hello World Pegasus workflow — the fastest way to see an end-to-end run.",
        "topic": "tutorial",
        "repo_url": "https://github.com/pegasus-isi/quickstart-workflow",
    },
    {
        "id": "somospie",
        "name": "somospie-pegasus-workflow",
        "description": "A Pegasus workflow for SOMOSPIE that downloads terrain and soil-moisture data, trains a RandomForest regressor, and produces downscaled soil-moisture maps for states across the United States",
        "topic": "earth science",
        "repo_url": "https://github.com/TauferLab/somospie-pegasus-workflow",
    },
    {
        "id": "earthquake",
        "name": "earthquake-workflow",
        "description": "Analyzes earthquake data from the USGS Earthquake API — an AI-assisted scientific workflow example.",
        "topic": "earth science",
        "repo_url": "https://github.com/pegasus-isi/earthquake-workflow",
    },
]

# Catalog order: quickstart first (gentlest onramp), then somospie, then
# earthquake. Everything else is out of hackathon scope.
_FEATURED_ORDER = ("quickstart", "somospie", "earthquake")

_cache: dict[str, Any] = {"ts": 0.0, "data": None}
_lock = asyncio.Lock()


def _sort_key(entry: dict[str, Any]) -> tuple[int, bool, str]:
    """Catalog ordering: featured ids first (in _FEATURED_ORDER), then
    Pegasus-ISI curated entries, then everything else alphabetically."""
    try:
        featured = _FEATURED_ORDER.index(entry["id"])
    except ValueError:
        featured = len(_FEATURED_ORDER)
    return (featured, entry["source"] != "Pegasus-ISI", entry["name"].lower())


def _norm_repo(url: str) -> str:
    """Normalize a repo URL for dedup: strip scheme, .git, trailing slash, case."""
    u = (url or "").strip().lower()
    u = re.sub(r"^https?://", "", u)
    u = u.rstrip("/")
    if u.endswith(".git"):
        u = u[:-4]
    return u


def _id_from_repo(url: str) -> str:
    """Derive a filesystem-safe id from the repo name."""
    slug = _norm_repo(url).rsplit("/", 1)[-1]
    return re.sub(r"[^A-Za-z0-9_-]", "-", slug) or "workflow"


def _topics(raw: str) -> list[str]:
    """Split a registry `topic` string into normalized tags (#314).

    The registry is community-editable and its `topic` field is free text, so
    it arrives as things like "Machine-Learning, Optuna, Pytorch-Cnn,
    Transfer-Learning". Passing that through whole made it a single filter chip
    that matched exactly one workflow, while "Machine Learning" and
    "Machine-Learning" sat beside it as two more. Nine of thirteen chips
    returned one result each.

    Splitting on commas/slashes and folding case and hyphens collapses those
    back into the handful of real subject areas the catalog actually has.
    """
    tags: list[str] = []
    for part in re.split(r"[,/;|]+", raw or ""):
        tag = re.sub(r"[-_]+", " ", part).strip().lower()
        tag = re.sub(r"\s+", " ", tag)
        if tag and tag not in tags:
            tags.append(tag)
    return tags


def _name_from_slug(slug: str) -> str:
    """A readable name from a repo slug, for registry entries with no `name`.

    "variant-calling-pegasus-workflow" is an identifier, not a title, and every
    card carrying one repeats the word "workflow" that is already in the page
    heading, the search placeholder and the description.
    """
    words = re.sub(r"[-_]+", " ", slug).strip()
    # Trailing "workflow"/"pegasus workflow" is noise on a page of workflows.
    words = re.sub(r"\s*(pegasus\s+)?workflow$", "", words, flags=re.IGNORECASE)
    words = re.sub(r"^pegasus\s+", "", words, flags=re.IGNORECASE).strip()
    if not words:
        return slug
    # Title-case, but leave anything already mixed-case or numeric alone so
    # "1000genome", "v3" and "LLM" survive.
    return " ".join(w if (w != w.lower() or w[:1].isdigit()) else w.capitalize()
                    for w in words.split())


def _normalize(entry: dict[str, Any], source: str) -> dict[str, Any] | None:
    repo = str(entry.get("repo_url") or entry.get("github_repo") or "").strip()
    # The registry is community-editable, so repo_url is untrusted input to
    # `git clone`: the ext::/file:: transports execute commands, and a
    # leading "-" is parsed as a git flag. Only plain http(s) entries ever
    # enter the catalog.
    if not repo.startswith(("https://", "http://")):
        return None
    slug = _id_from_repo(repo)
    raw_topic = str(entry.get("topic") or "")
    topics = _topics(raw_topic)
    return {
        "id": entry.get("id") or slug,
        "name": entry.get("name") or _name_from_slug(slug),
        "description": entry.get("description") or "",
        # `topic` stays a single string for any client still reading it; the
        # split list is what the filter chips should use.
        "topic": topics[0] if topics else "",
        "topics": topics,
        "repo_url": repo,
        "image": entry.get("image") or "",
        "license": entry.get("license") or "",
        "pegasus_version": entry.get("pegasus_version") or "",
        "source": source,
    }


async def _fetch_registry() -> list[dict[str, Any]]:
    """Fetch the PegasusHub registry; return [] on any failure (curated still shows)."""
    try:
        async with httpx.AsyncClient(timeout=_FETCH_TIMEOUT) as client:
            resp = await client.get(REGISTRY_URL)
            resp.raise_for_status()
            data = resp.json()
    except (httpx.HTTPError, ValueError) as e:
        log.warning("marketplace_registry_fetch_failed", error=str(e))
        return []
    items = data.get("workflows") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return []
    out = []
    for it in items:
        norm = _normalize(it, "PegasusHub")
        if norm:
            out.append(norm)
    return out


def _enrich_enabled() -> bool:
    """Read at call time so a test can flip it with monkeypatch.setenv."""
    return os.environ.get("STUDIO_MARKETPLACE_ENRICH", "1" if _ENRICH else "0") not in (
        "0",
        "false",
        "",
    )


def _gh_api_url(repo: str) -> str | None:
    """github.com/owner/name → api.github.com/repos/owner/name, else None."""
    m = re.match(r"^github\.com/([^/]+)/([^/]+)$", _norm_repo(repo))
    return f"https://api.github.com/repos/{m.group(1)}/{m.group(2)}" if m else None


async def _enrich_descriptions(entries: list[dict[str, Any]]) -> None:
    """Fill blank descriptions from each repo's GitHub metadata (#314).

    Roughly half the registry omits `description`, which left the catalog as a
    grid of names with nothing to choose between. The repo already has a
    one-liner; we just were not asking for it.

    Mutates in place, best-effort. Unauthenticated GitHub allows 60 requests an
    hour per IP and the catalog is cached for `_CACHE_TTL` (an hour by default),
    so a full refresh costs well under the budget — but a rate-limit, a timeout
    or a private repo must degrade to "no description" rather than failing the
    catalog. A card with no description is honest; a placeholder repeated down
    the grid looks like content and carries none.
    """
    if not _enrich_enabled():
        return
    missing = [e for e in entries if not e.get("description")]
    if not missing:
        return

    async def one(client: httpx.AsyncClient, entry: dict[str, Any]) -> None:
        url = _gh_api_url(entry["repo_url"])
        if not url:
            return
        try:
            resp = await client.get(
                url, headers={"Accept": "application/vnd.github+json"}
            )
            if resp.status_code != 200:
                return
            body = resp.json()
        except (httpx.HTTPError, ValueError):
            return
        if not isinstance(body, dict):
            return
        desc = (body.get("description") or "").strip()
        if desc:
            entry["description"] = desc
        # Repo topics are curated by the repo owner and are cleaner than the
        # registry's free-text field, so they are worth folding in when the
        # registry gave us nothing.
        if not entry.get("topics"):
            topics = [t for t in (body.get("topics") or []) if isinstance(t, str)]
            folded = _topics(", ".join(topics))
            if folded:
                entry["topics"] = folded
                entry["topic"] = folded[0]

    try:
        async with httpx.AsyncClient(timeout=_FETCH_TIMEOUT) as client:
            await asyncio.gather(*(one(client, e) for e in missing))
    except Exception as e:  # noqa: BLE001 - enrichment must never break the catalog
        log.warning("marketplace_description_enrich_failed", error=str(e))


async def _catalog(force: bool = False) -> list[dict[str, Any]]:
    """Curated-only catalog (quickstart, somospie, earthquake), cached for _CACHE_TTL.

    The live PegasusHub registry fetch is intentionally disabled for the
    hackathon scope: _fetch_registry() is kept for a post-hackathon revert but
    not called, so the catalog is deterministic and works offline.
    """
    async with _lock:
        age = time.monotonic() - _cache["ts"]
        if not force and _cache["data"] is not None and age < _CACHE_TTL:
            return _cache["data"]

        catalog = [n for e in _CURATED if (n := _normalize(e, "Pegasus-ISI"))]
        await _enrich_descriptions(catalog)
        catalog.sort(key=_sort_key)

        _cache["ts"] = time.monotonic()
        _cache["data"] = catalog
        return catalog


@router.get("")
async def list_workflows() -> dict[str, Any]:
    """Browse the curated Pegasus-ISI workflow catalog."""
    catalog = await _catalog()
    return {"workflows": catalog, "count": len(catalog)}


@router.post("/{workflow_id}/add")
async def add_workflow(workflow_id: str) -> dict[str, Any]:
    """Clone the selected workflow's GitHub repo into the workspace.

    Creates ``<workspace>/workflows/<id>`` via a shallow ``git clone``. The
    project then appears on the Workflows page.
    """
    if not re.fullmatch(r"[A-Za-z0-9_-]+", workflow_id):
        raise HTTPException(status_code=400, detail="Invalid workflow id")

    catalog = await _catalog()
    entry = next((w for w in catalog if w["id"] == workflow_id), None)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Unknown workflow: {workflow_id}")

    project_dir = os.path.join(WORKFLOWS_DIR, workflow_id)
    if os.path.exists(project_dir):
        raise HTTPException(
            status_code=409,
            detail=f"'{workflow_id}' already exists in your workspace.",
        )

    repo_url = str(entry["repo_url"])
    # Defense in depth behind the _normalize filter: never exec git on
    # anything but a plain http(s) URL, pass it after "--" so it can't be
    # read as a flag, and pin git's allowed transports so a redirect or
    # submodule can't downgrade to an exec-capable one (ext::, file::).
    if not repo_url.startswith(("https://", "http://")):
        raise HTTPException(
            status_code=400, detail="Unsupported repository URL scheme"
        )

    os.makedirs(WORKFLOWS_DIR, exist_ok=True)
    proc = await asyncio.create_subprocess_exec(
        "git", "clone", "--depth", "1", "--", repo_url, project_dir,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={**os.environ, "GIT_ALLOW_PROTOCOL": "http:https"},
    )
    try:
        _out, err = await asyncio.wait_for(proc.communicate(), timeout=_CLONE_TIMEOUT)
    except asyncio.TimeoutError:
        proc.kill()
        raise HTTPException(status_code=504, detail="git clone timed out")
    if proc.returncode != 0:
        detail = err.decode("utf-8", errors="replace").strip()[-400:]
        raise HTTPException(status_code=502, detail=f"git clone failed: {detail}")

    log.info("marketplace_workflow_added", id=workflow_id, repo=entry["repo_url"])
    return {
        "status": "added",
        "project_id": workflow_id,
        "project_dir": project_dir,
        "repo_url": entry["repo_url"],
    }
