"""Where a user's workflows run: a hosted site catalog and their allocation.

The workflows follow the pegasus-gromacs template (#466): a workflow never
defines its execution site. The site comes from one of Pegasus' centrally
hosted catalogs (github.com/pegasushub/pegasus-site-catalogs, the branch
matching the planner's MAJOR.MINOR), named in ``~/.pegasusrc`` as
``pegasus.catalog.site.repo.file``. Per-user allocation details are passed as
``env.RESOURCE_*`` properties, which the catalogs reference as
``${RESOURCE_USERNAME}``, ``${RESOURCE_PROJECT}`` and so on. That is the setup
ACCESS uses (ACCESS-Pegasus-Examples/00-Setup), done here from a form (#468).

Every catalog names its execution site ``compute``, so once one is chosen
"Where it runs" offers and defaults to it (services/execution_sites).

``~/.pegasusrc`` may hold other properties the user set by hand; only the keys
this module owns are touched, and every other line is kept as written.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any

import httpx
import structlog

log = structlog.get_logger()

REPO_FILE_KEY = "pegasus.catalog.site.repo.file"
#: Base URL of the catalog repository (default: the pegasushub GitHub repo)
#: and how often the planner re-fetches its copy. Both are set only for
#: Studio's own pool, which is served from the image (#474).
REPO_BASE_KEY = "pegasus.catalog.site.repo"
REPO_INTERVAL_KEY = "pegasus.catalog.site.repo.interval"
STUDIO_CATALOG = "pegasus-studio.yml"


def studio_repo_dir() -> str:
    """Where the image installs Studio's own catalog (<series>/conf/...)."""
    return os.environ.get("STUDIO_SITE_REPO", "/opt/studio/site-catalogs")


def studio_pool_available() -> bool:
    """Whether this image ships Studio's own catalog (defining "compute")."""
    root = Path(studio_repo_dir())
    return any(root.glob(f"*/conf/{STUDIO_CATALOG}"))
_ENV_PREFIX = "env."
#: Allocation variables a catalog may reference. Anything else in a catalog's
#: ${...} placeholders is ignored: it is not ours to ask for.
_RESOURCE_VAR = re.compile(r"\$\{(RESOURCE_[A-Z0-9_]+)\}")

_CATALOG_REPO = os.environ.get(
    "STUDIO_SITE_CATALOG_REPO", "pegasushub/pegasus-site-catalogs"
)
_FETCH_TIMEOUT = float(os.environ.get("STUDIO_SITE_CATALOG_TIMEOUT", "10"))
_CACHE_TTL = 3600.0

#: Friendlier names for the catalogs that exist today. A catalog not listed
#: here is shown by its file name, so a new one upstream needs no release.
_LABELS = {
    "access-pegasus.yml": "ACCESS Pegasus (TestPool)",
    "access-pegasus-expanse.yml": "ACCESS Pegasus → SDSC Expanse",
    "nersc-perlmutter.yml": "NERSC Perlmutter",
    "ospool-ap40.yml": "OSPool (ap40)",
    "ospool-ap41.yml": "OSPool (ap41)",
    "purdue-anvil.yml": "Purdue Anvil",
    "unity.yml": "Unity (UMass)",
    "usc-discovery.yml": "USC Discovery",
    "usc-laguna.yml": "USC Laguna",
}

#: How to ask for each allocation variable. Unknown ones get a generic label.
FIELD_LABELS = {
    "RESOURCE_USERNAME": "Your username on the cluster",
    "RESOURCE_PROJECT": "Project / allocation to charge",
    "RESOURCE_SCRATCH_DIR": "Scratch directory on the cluster",
}

_SAFE_CATALOG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*\.ya?ml$")
_SAFE_VAR = re.compile(r"^RESOURCE_[A-Z0-9_]+$")

_cache: dict[str, tuple[float, Any]] = {}


def pegasusrc_path() -> Path:
    return Path.home() / ".pegasusrc"


# ── ~/.pegasusrc ──────────────────────────────────────────────────────────


def _split(line: str) -> tuple[str, str] | None:
    """(key, value) for a `key = value` / `key: value` property line."""
    stripped = line.strip()
    if not stripped or stripped[0] in "#!":
        return None
    m = re.match(r"^([^=:\s]+)\s*[=:]\s*(.*)$", stripped)
    return (m.group(1), m.group(2).strip()) if m else None


def read_config(path: Path | None = None) -> dict[str, Any]:
    """{"catalog", "env", "studio_pool"} from ~/.pegasusrc.

    `catalog` is a published catalog's file name, or None. `studio_pool` is
    True when ~/.pegasusrc points at Studio's own catalog in the image (#474):
    that is "no cluster chosen", shown as Studio's own pool, not a catalog.
    """
    path = path or pegasusrc_path()
    catalog: str | None = None
    base: str | None = None
    env: dict[str, str] = {}
    try:
        lines = path.read_text().splitlines()
    except OSError:
        lines = []
    for line in lines:
        kv = _split(line)
        if not kv:
            continue
        key, value = kv
        if key == REPO_FILE_KEY:
            catalog = value or None
        elif key == REPO_BASE_KEY:
            base = value or None
        elif key.startswith(_ENV_PREFIX) and _SAFE_VAR.match(key[len(_ENV_PREFIX):]):
            env[key[len(_ENV_PREFIX):]] = value
    studio_pool = catalog == STUDIO_CATALOG and (base or "").startswith("file://")
    return {
        "catalog": None if studio_pool else catalog,
        "env": env,
        "studio_pool": studio_pool,
    }


def _clean(value: str) -> str:
    """A property value that cannot break the file: one line, trimmed."""
    return " ".join(str(value).split())


def write_config(
    catalog: str | None, env: dict[str, str], path: Path | None = None
) -> dict[str, Any]:
    """Set the catalog and allocation in ~/.pegasusrc; keep every other line.

    `catalog=None` removes the catalog (Studio's own pool); an empty env value
    removes that variable. Written atomically, 0600: an allocation is not a
    secret, but it is nobody else's business either.
    """
    path = path or pegasusrc_path()
    if catalog is not None and not _SAFE_CATALOG.match(catalog):
        raise ValueError(f"Not a site catalog file name: {catalog!r}")
    for var in env:
        if not _SAFE_VAR.match(var):
            raise ValueError(f"Not an allocation variable: {var!r}")

    wanted: dict[str, str] = {}
    if catalog:
        # A published catalog: no repo base, so pegasus-plan fetches it from
        # the pegasushub repository rather than looking in the image.
        wanted[REPO_FILE_KEY] = catalog
    elif studio_pool_available():
        # "No cluster": Studio's own pool, which defines "compute" (#474).
        wanted[REPO_BASE_KEY] = f"file://{studio_repo_dir()}"
        wanted[REPO_FILE_KEY] = STUDIO_CATALOG
        wanted[REPO_INTERVAL_KEY] = "0"
    for var, value in env.items():
        if _clean(value):
            wanted[f"{_ENV_PREFIX}{var}"] = _clean(value)
    owned = {REPO_FILE_KEY, REPO_BASE_KEY, REPO_INTERVAL_KEY} | {
        f"{_ENV_PREFIX}{v}" for v in env
    }

    try:
        lines = path.read_text().splitlines()
    except OSError:
        lines = []
    out: list[str] = []
    for line in lines:
        kv = _split(line)
        if kv and kv[0] in owned:
            if kv[0] in wanted:
                out.append(f"{kv[0]} = {wanted.pop(kv[0])}")
            continue  # owned but no longer wanted: dropped
        out.append(line)
    out += [f"{k} = {v}" for k, v in wanted.items()]

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".pegasusrc.{os.getpid()}.tmp")
    tmp.write_text("\n".join(out) + ("\n" if out else ""))
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    log.info("site_config_written", catalog=catalog, vars=sorted(wanted_keys(env)))
    return read_config(path)


def wanted_keys(env: dict[str, str]) -> list[str]:
    return [k for k, v in env.items() if _clean(v)]


# ── the hosted catalogs ───────────────────────────────────────────────────


def series(version: str | None) -> str | None:
    """`6.0.0dev` / `5.1.2` → `6.0` / `5.1`: the catalog repo's branch name."""
    m = re.match(r"\s*(\d+)\.(\d+)", version or "")
    return f"{m.group(1)}.{m.group(2)}" if m else None


def needed_vars(catalog_text: str) -> list[str]:
    """The allocation variables a catalog references, in first-use order."""
    seen: list[str] = []
    for var in _RESOURCE_VAR.findall(catalog_text):
        if var not in seen:
            seen.append(var)
    return seen


async def _get(client: httpx.AsyncClient, url: str, **kw: Any) -> httpx.Response:
    resp = await client.get(url, **kw)
    resp.raise_for_status()
    return resp


async def list_catalogs(ref: str | None) -> list[dict[str, Any]]:
    """[{name, label, needs}] for the catalogs on branch `ref` (else main).

    Cached for an hour; on any failure, the last good list, so a GitHub hiccup
    does not empty the picker. `needs` comes from each catalog's own
    ${RESOURCE_*} placeholders, so the form asks for exactly what that cluster
    uses and nothing more.
    """
    key = f"catalogs:{ref}"
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < _CACHE_TTL:
        return hit[1]
    try:
        async with httpx.AsyncClient(timeout=_FETCH_TIMEOUT) as client:
            refs = [ref, "main"] if ref else ["main"]
            items = None
            for r in refs:
                try:
                    resp = await _get(
                        client,
                        f"https://api.github.com/repos/{_CATALOG_REPO}/contents/conf",
                        params={"ref": r},
                        headers={"Accept": "application/vnd.github+json"},
                    )
                    items, used = resp.json(), r
                    break
                except httpx.HTTPStatusError:
                    continue  # no branch for this Pegasus series yet
            if not isinstance(items, list):
                raise ValueError("no catalog listing")
            names = sorted(
                i["name"] for i in items
                if isinstance(i, dict) and _SAFE_CATALOG.match(str(i.get("name", "")))
            )

            async def describe(name: str) -> dict[str, Any]:
                needs: list[str] = []
                try:
                    raw = await _get(
                        client,
                        f"https://raw.githubusercontent.com/{_CATALOG_REPO}/{used}/conf/{name}",
                    )
                    needs = needed_vars(raw.text)
                except httpx.HTTPError:
                    pass
                return {"name": name, "label": _LABELS.get(name, name), "needs": needs}

            catalogs = list(await asyncio.gather(*(describe(n) for n in names)))
    except (httpx.HTTPError, ValueError) as e:
        log.warning("site_catalog_list_failed", error=str(e), ref=ref)
        return hit[1] if hit else []
    _cache[key] = (time.monotonic(), catalogs)
    return catalogs


# ── Slurm accounts ────────────────────────────────────────────────────────


async def slurm_accounts() -> list[str]:
    """The user's Slurm accounts, where `sacctmgr` exists (an OOD/HPC install).

    Empty anywhere else, and on any failure: the field then takes free text.
    """
    exe = shutil.which("sacctmgr")
    if not exe:
        return []
    user = os.environ.get("USER") or os.environ.get("LOGNAME") or ""
    try:
        proc = await asyncio.create_subprocess_exec(
            exe, "-nP", "show", "assoc", f"user={user}", "format=account",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
    except (OSError, asyncio.TimeoutError) as e:
        log.info("slurm_accounts_unavailable", error=str(e))
        return []
    accounts: list[str] = []
    for line in out.decode("utf-8", "replace").splitlines():
        name = line.strip().strip("|")
        if name and name not in accounts:
            accounts.append(name)
    return accounts
