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
import subprocess
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


def studio_repo_image_dir() -> str:
    """Where the image installs Studio's own catalog (<series>/conf/...)."""
    return os.environ.get("STUDIO_SITE_REPO_IMAGE", "/opt/studio/site-catalogs")


def studio_repo_dir() -> str:
    """Where ~/.pegasusrc points for Studio's own catalog: a copy in the home
    directory (#476).

    Not the image path: pegasus-plan usually runs on the HTCondor submit host,
    which cannot see /opt/studio in this container but shares the home
    directory (its NFS export, mounted at the same path). The entrypoint keeps
    the copy current; ensure_studio_repo() makes it on demand.
    """
    return os.environ.get(
        "STUDIO_SITE_REPO", str(Path.home() / ".studio" / "site-catalogs")
    )


def _has_catalog(root: str) -> bool:
    return any(Path(root).glob(f"*/conf/{STUDIO_CATALOG}"))


def admin_catalog_file() -> str | None:
    """A site catalog the administrator supplies for every user (#496).

    Set as STUDIO_SITE_CATALOG_FILE, e.g. by an Open OnDemand site's
    site.env. It takes the place of Studio's own pegasus-studio.yml, so
    everything built on that default (the shared copy the submit host reads,
    ~/.pegasusrc, the Resources page, site overrides) works unchanged: on a
    cluster, "compute" means the cluster's own resources, not a pool that
    only exists in the hosted deployment.
    """
    path = os.environ.get("STUDIO_SITE_CATALOG_FILE", "").strip()
    return path if path and os.path.isfile(path) else None


def deployment() -> str:
    """Which kind of install this is: ``"hosted"`` or ``"ood"`` (#532).

    The two differ in how a user reaches more compute. On the hosted
    deployment the pool is Studio's own and extra capacity comes from an
    HTCondor annex the user leases from a cluster they have an account on.
    Under Open OnDemand the cluster *is* the install, described by the
    administrator's catalog (STUDIO_SITE_CATALOG_FILE / STUDIO_SITE_CATALOG
    in site.env), and an annex makes no sense. The Resources page shows a
    different set of cards for each.

    Derived from whether an admin catalog is configured, because that is
    what every OOD install sets and no hosted pod does. STUDIO_DEPLOYMENT
    overrides it, for a local test instance of either shape.
    """
    forced = os.environ.get("STUDIO_DEPLOYMENT", "").strip().lower()
    if forced in ("hosted", "ood"):
        return forced
    if admin_catalog_file() or os.environ.get("STUDIO_SITE_CATALOG", "").strip():
        return "ood"
    return "hosted"


def default_label() -> str:
    """What the Resources page calls the default catalog."""
    if admin_catalog_file():
        return os.environ.get("STUDIO_SITE_CATALOG_LABEL", "").strip() or "This cluster"
    return "Studio’s own pool"


def _series_dirs(root: str) -> list[Path]:
    return sorted(p for p in Path(root).glob("*/conf") if p.is_dir())


def install_admin_catalog(root: str | None = None) -> bool:
    """Put the admin's catalog in place of pegasus-studio.yml under every
    series in `root` (the shared copy). True when one was installed.

    Every series the image ships, because the image's own series is the one
    its planner looks under; if the image ships none, the local Pegasus
    series. Copied, not linked: the submit host reads this directory and may
    not see the admin's path.
    """
    src = admin_catalog_file()
    if not src:
        return False
    root = root or studio_repo_dir()
    targets = _series_dirs(root) or _series_dirs(studio_repo_image_dir())
    if not targets:
        found = series(_local_version())
        if not found:
            return False
        targets = [Path(found) / "conf"]
    try:
        for conf in targets:
            dest = Path(root) / conf.parent.name / "conf"
            dest.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest / STUDIO_CATALOG)
    except OSError as e:
        log.warning("admin_site_catalog_copy_failed", error=str(e), source=src)
        return False
    return True


def _local_version() -> str | None:
    try:
        out = subprocess.run(
            ["pegasus-version"], capture_output=True, text=True, timeout=30
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return out.strip().splitlines()[0] if out.strip() else None


def ensure_studio_repo() -> bool:
    """Make sure the shared copy exists (copying from the image if needed);
    True when Studio's own catalog, or the admin's in its place, is usable."""
    if not _has_catalog(studio_repo_dir()):
        image = studio_repo_image_dir()
        if _has_catalog(image):
            try:
                shutil.copytree(image, studio_repo_dir(), dirs_exist_ok=True)
            except OSError as e:
                log.warning("studio_site_repo_copy_failed", error=str(e))
    install_admin_catalog()
    return _has_catalog(studio_repo_dir())


def studio_pool_available() -> bool:
    """Whether the default catalog (defining "compute") can be used here."""
    return (
        admin_catalog_file() is not None
        or _has_catalog(studio_repo_dir())
        or _has_catalog(studio_repo_image_dir())
    )
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

# ── Site overrides (#478) ──
#
# Properties.add_site_profile() writes these, and pegasus-plan applies them
# over whatever site catalog is active. They live in ~/.pegasusrc beside the
# catalog choice, survive catalog refreshes, and — unlike a user-level
# pegasus.catalog.site.file — do not replace a project's own sites.yml.
OVERRIDE_PREFIX = "pegasus.catalog.site.sites."
NAMESPACES = ("condor", "pegasus", "env", "dagman", "globus", "selector")
_OVERRIDE_KEY = re.compile(
    r"^pegasus\.catalog\.site\.sites\.([A-Za-z0-9_-]+)\.profiles\."
    r"(" + "|".join(NAMESPACES) + r")\.(\+?[A-Za-z0-9_.-]+)$"
)
_SAFE_SITE = re.compile(r"^[A-Za-z0-9_-]+$")
# HTCondor ClassAd attributes may start with "+" (+ProjectName).
_SAFE_PROFILE_KEY = re.compile(r"^\+?[A-Za-z0-9_.-]+$")

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
    overrides: list[dict[str, str]] = []
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
        elif key.startswith(OVERRIDE_PREFIX):
            m = _OVERRIDE_KEY.match(key)
            if m:
                overrides.append({
                    "site": m.group(1), "namespace": m.group(2),
                    "key": m.group(3), "value": value,
                })
        elif key.startswith(_ENV_PREFIX) and _SAFE_VAR.match(key[len(_ENV_PREFIX):]):
            env[key[len(_ENV_PREFIX):]] = value
    studio_pool = catalog == STUDIO_CATALOG and (base or "").startswith("file://")
    return {
        "catalog": None if studio_pool else catalog,
        "env": env,
        "studio_pool": studio_pool,
        "overrides": overrides,
    }


def _clean(value: str) -> str:
    """A property value that cannot break the file: one line, trimmed."""
    return " ".join(str(value).split())


def validate_overrides(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Normalised override rows, or ValueError naming the first bad one.

    Empty rows (no key) are dropped; a later row for the same site, namespace
    and key wins, as it would in the file.
    """
    out: dict[tuple[str, str, str], dict[str, str]] = {}
    for row in rows or []:
        site = str(row.get("site") or "").strip()
        ns = str(row.get("namespace") or "").strip().lower()
        key = str(row.get("key") or "").strip()
        value = _clean(row.get("value") or "")
        if not key:
            continue
        if not _SAFE_SITE.match(site):
            raise ValueError(f"Not a site name: {site!r}")
        if ns not in NAMESPACES:
            raise ValueError(f"Not a profile namespace: {ns!r} (use one of {', '.join(NAMESPACES)})")
        if not _SAFE_PROFILE_KEY.match(key):
            raise ValueError(f"Not a profile key: {key!r}")
        if not value:
            raise ValueError(f"{site} / {ns} / {key} has no value")
        out[(site, ns, key)] = {"site": site, "namespace": ns, "key": key, "value": value}
    return list(out.values())


def override_property(row: dict[str, str]) -> str:
    return f"{OVERRIDE_PREFIX}{row['site']}.profiles.{row['namespace']}.{row['key']}"


def write_config(
    catalog: str | None,
    env: dict[str, str],
    path: Path | None = None,
    overrides: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Set the catalog and allocation in ~/.pegasusrc; keep every other line.

    `catalog=None` removes the catalog (Studio's own pool); an empty env value
    removes that variable. `overrides`, when given, replaces every site
    override in the file (Studio manages them all once the editor is used);
    None leaves them as they are. Written atomically, 0600: an allocation is
    not a secret, but it is nobody else's business either.
    """
    path = path or pegasusrc_path()
    rows = validate_overrides(overrides) if overrides is not None else None
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
    elif ensure_studio_repo():
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
    if rows is not None:
        for row in rows:
            wanted[override_property(row)] = row["value"]

    try:
        lines = path.read_text().splitlines()
    except OSError:
        lines = []
    out: list[str] = []
    for line in lines:
        kv = _split(line)
        if kv and rows is not None and kv[0].startswith(OVERRIDE_PREFIX):
            owned.add(kv[0])  # every override is Studio's once rows are given
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


def missing_allocation(needs: list[str], env: dict[str, str]) -> list[str]:
    """The allocation fields a catalog references that ~/.pegasusrc does not
    set, as the labels the Resources form shows for them (#516).

    A catalog whose placeholders are unfilled still plans, and the job then
    fails on the cluster with the scheduler's own error, which names neither
    the field nor the page that sets it. The Resources form refuses to save
    with a field empty, so this is normally [] — but ~/.pegasusrc can be
    edited by hand, and a catalog can grow a new placeholder upstream.
    """
    return [
        FIELD_LABELS.get(var, var) for var in needs if not (env.get(var) or "").strip()
    ]


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


# ── The effective site ────────────────────────────────────────────────────


def studio_catalog_text() -> str | None:
    """Studio's own catalog, from the shared copy or the image."""
    for root in (studio_repo_dir(), studio_repo_image_dir()):
        for f in sorted(Path(root).glob(f"*/conf/{STUDIO_CATALOG}")):
            try:
                return f.read_text()
            except OSError:
                continue
    return None


def catalog_label(name: str) -> str:
    """The cluster's name for a published catalog, or the file name."""
    return _LABELS.get(name, name)


def cached_catalog_text(name: str) -> str | None:
    """A published catalog's YAML if catalog_text() fetched it within the
    hour, for any Pegasus series; None otherwise. Never fetches: this is
    for callers that cannot await (services/execution_sites)."""
    suffix = f":{name}"
    now = time.monotonic()
    for key, (stamp, text) in _cache.items():
        if key.startswith("text:") and key.endswith(suffix) and now - stamp < _CACHE_TTL:
            return text
    return None


async def catalog_text(name: str, ref: str | None) -> str | None:
    """A published catalog's YAML, cached like the listing."""
    if not _SAFE_CATALOG.match(name):
        return None
    key = f"text:{ref}:{name}"
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < _CACHE_TTL:
        return hit[1]
    try:
        async with httpx.AsyncClient(timeout=_FETCH_TIMEOUT) as client:
            for r in ([ref, "main"] if ref else ["main"]):
                try:
                    raw = await _get(
                        client,
                        f"https://raw.githubusercontent.com/{_CATALOG_REPO}/{r}/conf/{name}",
                    )
                except httpx.HTTPStatusError:
                    continue
                _cache[key] = (time.monotonic(), raw.text)
                return raw.text
    except httpx.HTTPError as e:
        log.info("site_catalog_text_failed", name=name, error=str(e))
    return hit[1] if hit else None


def effective_site(
    text: str | None, site: str, overrides: list[dict[str, str]]
) -> str | None:
    """The catalog's entry for `site` with the overrides applied, as YAML.

    What pegasus-plan will see for that site, so a user can check an
    override before running anything. ${RESOURCE_*} placeholders are shown as
    written; Pegasus fills them from the allocation at plan time.
    """
    import copy

    import yaml

    entry: dict[str, Any] = {"name": site}
    try:
        doc = yaml.safe_load(text) if text else None
    except yaml.YAMLError:
        doc = None
    for s in (doc or {}).get("sites") or []:
        if isinstance(s, dict) and s.get("name") == site:
            entry = copy.deepcopy(s)
            break
    profiles = entry.setdefault("profiles", {}) or {}
    entry["profiles"] = profiles
    for row in overrides:
        if row["site"] == site:
            profiles.setdefault(row["namespace"], {})[row["key"]] = row["value"]
    if not profiles:
        entry.pop("profiles")
    return yaml.safe_dump(entry, sort_keys=False, default_flow_style=False)


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
