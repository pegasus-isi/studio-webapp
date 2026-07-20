"""Workflow Marketplace — browse Pegasus workflows from GitHub and clone one
into the workspace.

Nothing is baked into the image. The catalog is the **live PegasusHub registry**
(``pegasushub/workflow-registry`` → ``data/workflows.json``) merged with a
curated set of Pegasus-ISI repos, deduped by repository URL and cached. "Add"
performs a shallow ``git clone`` of the selected repo into
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

WORKSPACE_ROOT = os.environ.get(
    "STUDIO_WORKSPACE_DIR",
    os.path.join(os.environ.get("HOME", "/tmp"), "work"),
)
WORKFLOWS_DIR = os.path.join(WORKSPACE_ROOT, "workflows")

# Curated Pegasus-ISI workflows to always surface, merged (and deduped by repo
# URL) with the live registry. Descriptions from each repo's GitHub metadata.
_CURATED: list[dict[str, str]] = [
    {
        "id": "quickstart",
        "name": "quickstart-workflow",
        "description": "A simple Hello World Pegasus workflow — the fastest way to see an end-to-end run.",
        "topic": "tutorial",
        "repo_url": "https://github.com/pegasus-isi/quickstart-workflow",
    },
    {
        "id": "soilmoisture",
        "name": "soilmoisture-workflow",
        "description": "Analyzes soil-moisture data from the Agromonitoring API to answer “Should I water my crops?”",
        "topic": "earth science",
        "repo_url": "https://github.com/pegasus-isi/soilmoisture-workflow",
    },
    {
        "id": "airquality",
        "name": "airquality-workflow",
        "description": "Processes air-quality sensor data from OpenAQ/Sage to detect anomalies and analyze pollutant trends.",
        "topic": "earth science",
        "repo_url": "https://github.com/pegasus-isi/airquality-workflow",
    },
    {
        "id": "earthquake",
        "name": "earthquake-workflow",
        "description": "Analyzes earthquake data from the USGS Earthquake API — an AI-assisted scientific workflow example.",
        "topic": "earth science",
        "repo_url": "https://github.com/pegasus-isi/earthquake-workflow",
    },
    {
        "id": "crophealth",
        "name": "crophealth-workflow",
        "description": "Crop-health disease detection Pegasus workflow.",
        "topic": "machine learning",
        "repo_url": "https://github.com/pegasus-isi/crophealth-workflow",
    },
    {
        "id": "variant-calling-pegasus",
        "name": "variant-calling-pegasus-workflow",
        "description": "Automates a variant-calling workflow (Data Carpentry “Data Wrangling and Processing for Genomics”).",
        "topic": "bioinformatics",
        "repo_url": "https://github.com/pegasus-isi/variant-calling-pegasus-workflow",
    },
]

# Ids pinned to the front of the catalog, in this order, so newcomers meet the
# gentlest examples first: a Hello-World tutorial, then a simple single-API
# science workflow, then a real multi-step bioinformatics pipeline. Everything
# else follows in the default order (Pegasus-ISI first, then alphabetical).
_FEATURED_ORDER = ("quickstart", "soilmoisture", "variant-calling-pegasus")

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


def _normalize(entry: dict[str, Any], source: str) -> dict[str, Any] | None:
    repo = entry.get("repo_url") or entry.get("github_repo") or ""
    if not repo:
        return None
    return {
        "id": entry.get("id") or _id_from_repo(repo),
        "name": entry.get("name") or _id_from_repo(repo),
        "description": entry.get("description") or "",
        "topic": entry.get("topic") or "",
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


async def _catalog(force: bool = False) -> list[dict[str, Any]]:
    """Merged, deduped catalog (registry + curated), cached for _CACHE_TTL."""
    async with _lock:
        age = time.monotonic() - _cache["ts"]
        if not force and _cache["data"] is not None and age < _CACHE_TTL:
            return _cache["data"]

        registry = await _fetch_registry()
        curated = [n for e in _CURATED if (n := _normalize(e, "Pegasus-ISI"))]

        merged: dict[str, dict[str, Any]] = {}
        # Curated first so our ids/descriptions win; registry fills the rest.
        for entry in curated + registry:
            key = _norm_repo(entry["repo_url"])
            if key and key not in merged:
                merged[entry["id"]] = entry
                merged[key] = entry  # dedup marker
        catalog = [v for k, v in merged.items() if k == v["id"]]
        catalog.sort(key=_sort_key)

        _cache["ts"] = time.monotonic()
        _cache["data"] = catalog
        return catalog


@router.get("")
async def list_workflows() -> dict[str, Any]:
    """Browse the merged PegasusHub + Pegasus-ISI catalog."""
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

    os.makedirs(WORKFLOWS_DIR, exist_ok=True)
    proc = await asyncio.create_subprocess_exec(
        "git", "clone", "--depth", "1", entry["repo_url"], project_dir,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
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
