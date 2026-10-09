"""Workflow Marketplace — browse Pegasus workflows from GitHub and clone one
into the workspace.

Nothing is baked into the image. The catalog is the **live PegasusHub registry**
(``pegasushub/workflow-registry`` → ``data/workflows.json``) merged with every
public repo in the Pegasus-ISI GitHub org that carries the
``pegasus-studio-example`` topic,
deduped by repository URL and cached. Listing a workflow is a repo setting, not
a Studio release: add the topic on GitHub (``gh repo edit
pegasus-isi/<repo> --add-topic pegasus-studio-example``) and it appears on the next refresh.
"Add" performs a shallow ``git clone`` of the selected repo into
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

# Org and topic that mark a repo as a Studio example. Overridable so a site
# running its own Studio can list its own org's workflows. The topic is
# deliberately specific: a bare `studio` is what someone would add to describe
# studio-webapp or studio-system, and either would then be offered as a
# runnable example. The search is org-scoped, so other orgs' use of a topic
# never matters; only an accidental tag inside the org would.
_GITHUB_ORG = os.environ.get("STUDIO_MARKETPLACE_GITHUB_ORG", "pegasus-isi").strip()
_GITHUB_TOPIC = os.environ.get(
    "STUDIO_MARKETPLACE_TOPIC", "pegasus-studio-example"
).strip()
GITHUB_SEARCH_URL = os.environ.get(
    "STUDIO_MARKETPLACE_GITHUB_SEARCH_URL",
    "https://api.github.com/search/repositories",
)

WORKSPACE_ROOT = workspace_root()
WORKFLOWS_DIR = workflows_dir(WORKSPACE_ROOT)

# The label the catalog's source filter shows for org repos. Kept from when this
# was a hand-maintained list, because clients filter on the exact string.
_ORG_SOURCE = "Pegasus-ISI"

# Ids pinned to the front of the catalog, in this order: the Hello-World
# tutorial, then the examples that follow the portable execution-site pattern
# (custom_sites.py + --site-style/--queue/--project), so they plan against
# whatever site this install defines (condorpool, Slurm `compute`, a hosted
# catalog) instead of assuming condorpool. Within those, least setup first:
# earthquake needs no credentials, airquality needs OPENAQ_API_KEY, crophealth
# needs Kaggle credentials and trains a CNN. Everything else follows in the
# default order (Pegasus-ISI first, then alphabetical); move a repo up here
# once it adopts the pattern.
_FEATURED_ORDER = ("quickstart", "earthquake", "airquality", "crophealth")

_cache: dict[str, Any] = {"ts": 0.0, "data": None}
_lock = asyncio.Lock()


def _sort_key(entry: dict[str, Any]) -> tuple[int, bool, str]:
    """Catalog ordering: featured ids first (in _FEATURED_ORDER), then
    Pegasus-ISI org entries, then everything else alphabetically."""
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
    """Fetch the PegasusHub registry; return [] on any failure (org repos still show)."""
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


def _id_from_org_repo(name: str) -> str:
    """`earthquake-workflow` → `earthquake`.

    The ids the hand-maintained list used, so a repo moved onto the topic keeps
    its clone directory (`workflows/earthquake`), its place in
    `_FEATURED_ORDER`, and the "already in your workspace" check for anyone who
    added it before.
    """
    slug = re.sub(r"[^A-Za-z0-9_-]", "-", name)
    return re.sub(r"-workflow$", "", slug, flags=re.IGNORECASE) or slug or "workflow"


def _from_search(items: list[Any]) -> list[dict[str, Any]]:
    """Catalog entries from a GitHub repository-search response's `items`."""
    out: list[dict[str, Any]] = []
    for repo in items:
        if not isinstance(repo, dict) or repo.get("archived") or repo.get("private"):
            continue
        name = str(repo.get("name") or "")
        topics = [
            t for t in (repo.get("topics") or [])
            if isinstance(t, str) and t.lower() != _GITHUB_TOPIC.lower()
        ]
        norm = _normalize(
            {
                "id": _id_from_org_repo(name),
                "name": name,
                "description": (repo.get("description") or "").strip(),
                # The marker topic is how the repo got here, not a subject;
                # as a filter chip it would match every org entry.
                "topic": ", ".join(topics),
                "repo_url": repo.get("html_url") or "",
                "license": ((repo.get("license") or {}).get("name") or ""),
            },
            _ORG_SOURCE,
        )
        if norm:
            out.append(norm)
    return out


# The last successful topic search, reused when GitHub is unreachable or
# rate-limits us. Unauthenticated search allows 10 requests a minute per IP;
# a failed refresh must not empty the org half of the catalog for an hour.
_last_topic_repos: list[dict[str, Any]] = []


async def _fetch_topic_repos() -> list[dict[str, Any]]:
    """Public repos in `_GITHUB_ORG` tagged `_GITHUB_TOPIC`."""
    global _last_topic_repos
    if not _GITHUB_ORG or not _GITHUB_TOPIC:
        return []
    params = {
        "q": f"org:{_GITHUB_ORG} topic:{_GITHUB_TOPIC}",
        "per_page": "100",
        "sort": "updated",
    }
    try:
        async with httpx.AsyncClient(timeout=_FETCH_TIMEOUT) as client:
            resp = await client.get(
                GITHUB_SEARCH_URL,
                params=params,
                headers={"Accept": "application/vnd.github+json"},
            )
            resp.raise_for_status()
            data = resp.json()
    except (httpx.HTTPError, ValueError) as e:
        log.warning(
            "marketplace_topic_search_failed",
            error=str(e),
            reusing=len(_last_topic_repos),
        )
        return list(_last_topic_repos)
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return list(_last_topic_repos)
    _last_topic_repos = _from_search(items)
    return list(_last_topic_repos)


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
    """Merged, deduped catalog (org topic repos + registry), cached."""
    async with _lock:
        age = time.monotonic() - _cache["ts"]
        if not force and _cache["data"] is not None and age < _CACHE_TTL:
            return _cache["data"]

        registry, tagged = await asyncio.gather(
            _fetch_registry(), _fetch_topic_repos()
        )

        merged: dict[str, dict[str, Any]] = {}
        # Org repos first so our ids/descriptions win; registry fills the rest.
        # Skip an entry whose repo OR id is already taken. The id check matters
        # (BE-9): two distinct-repo entries sharing an id used to silently
        # overwrite the first's id slot, dropping it from the catalog even
        # though its repo key survived. An explicit skip keeps the org one.
        for entry in tagged + registry:
            key = _norm_repo(entry["repo_url"])
            eid = entry["id"]
            if not key or key in merged or eid in merged:
                continue
            merged[eid] = entry
            merged[key] = entry  # dedup marker
        catalog = [v for k, v in merged.items() if k == v["id"]]
        await _enrich_descriptions(catalog)
        catalog.sort(key=_sort_key)

        _cache["ts"] = time.monotonic()
        _cache["data"] = catalog
        return catalog


@router.get("")
async def list_workflows() -> dict[str, Any]:
    """Browse the merged PegasusHub + Pegasus-ISI (marker topic) catalog."""
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
