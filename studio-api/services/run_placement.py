"""Which machines a workflow's steps may use: its annex, or everywhere else.

The Run panel's "Where it runs" picks a Pegasus *site*. On the hosted
deployment one site (Studio's pool, ``compute``, or ``condorpool`` beside a
chosen cluster) is served by more than one set of machines: Studio's own
execution points and the OSPool, and the user's annex nodes. Left alone, the
access point routes each job to whichever is free, pool *or* annex.

The choice is per workflow, and it lives where ``pegasus-plan`` reads it
whoever runs it: the project's own ``pegasus.properties``, as a site profile.
So the Run button and a ``pegasus-plan`` typed in a terminal (or run by the
assistant) plan the same thing. Two choices:

- **Only my annex**: the project file gets
  ``…sites.<site>.profiles.condor.requirements = TARGET.AnnexName =?= MY.Owner``.
  ``MY.Owner`` is the submitting user and the access point names every annex
  after its owner (its job transform targets ``TargetAnnexName = Owner``), so
  the line holds no username and works for anyone who clones the workflow.
- **Everywhere else** (the default): Studio's pool and the OSPool, never an
  annex. The project file has no line; the default comes from ~/.pegasusrc,
  where Studio keeps ``… = TARGET.AnnexName =?= undefined`` for the hosted
  sites. A project's pegasus.properties outranks ~/.pegasusrc, so one
  workflow can opt in without touching the rest.

Earlier designs: a standing ~/.pegasusrc pin for every run (#558/#564), easy
to leave on, after which every run waited for nodes that were not coming;
then a per-run ``-D`` (#572), which a terminal ``pegasus-plan`` never saw.

A requirements line Studio did not write (the workflow's own, or one typed
by hand) is never changed; it is reported as set by the workflow.

GPUs are not set here: whether a step needs one belongs to the workflow
(#576, services/workflow_gpus.py).
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger()

ANNEX = "annex"
ELSEWHERE = "elsewhere"
PLACEMENTS = (ANNEX, ELSEWHERE)
# Older clients' values: "any" let jobs onto the annex or the pool, "pool"
# was today's "everywhere else". Both now mean everywhere else.
LEGACY = {"any": ELSEWHERE, "pool": ELSEWHERE}

ANNEX_REQ = "TARGET.AnnexName =?= MY.Owner"
ELSEWHERE_REQ = "TARGET.AnnexName =?= undefined"
# The #572 per-run value, which named the user; read as "annex" if found.
_NAMED_ANNEX_REQ = re.compile(r'^TARGET\.AnnexName\s*=\?=\s*"[A-Za-z0-9][A-Za-z0-9._-]*"$')

#: The hosted sites the pool-or-annex routing applies to.
HOSTED_SITES = ("compute", "condorpool")

_SITE = re.compile(r"^[A-Za-z0-9_.-]+$")
_PROPERTIES = "pegasus.properties"


def normalise(placement: str) -> str:
    value = LEGACY.get(placement, placement)
    if value not in PLACEMENTS:
        raise ValueError(f"unknown placement {placement!r}")
    return value


def requirements_key(site: str) -> str:
    if not _SITE.match(site):
        raise ValueError(f"unusable site name {site!r}")
    return f"pegasus.catalog.site.sites.{site}.profiles.condor.requirements"


# ── one line of a properties file ──────────────────────────────────────────


def _split(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped or stripped[0] in "#!":
        return None
    m = re.match(r"^([^=:\s]+)\s*[=:\s]\s*(.*)$", stripped)
    return (m.group(1), m.group(2).strip()) if m else None


def read_property(path: Path, key: str) -> str | None:
    """The last value of `key` in a properties file (the one that wins)."""
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return None
    value = None
    for line in lines:
        kv = _split(line)
        if kv and kv[0] == key:
            value = kv[1]
    return value


def write_property(path: Path, key: str, value: str | None, *, mode: int | None = None) -> None:
    """Set (or with None, remove) one key; every other line is kept as is."""
    try:
        lines = path.read_text().splitlines()
    except OSError:
        lines = []
    out: list[str] = []
    placed = False
    for line in lines:
        kv = _split(line)
        if kv and kv[0] == key:
            if value is not None and not placed:
                out.append(f"{key} = {value}")
                placed = True
            continue
        out.append(line)
    if value is not None and not placed:
        out.append(f"{key} = {value}")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text("\n".join(out) + ("\n" if out else ""))
    if mode is not None:
        os.chmod(tmp, mode)
    elif path.exists():
        os.chmod(tmp, path.stat().st_mode & 0o777)
    os.replace(tmp, path)


# ── a workflow's choice ────────────────────────────────────────────────────


def _classify(value: str | None) -> str | None:
    if value is None:
        return None
    v = value.strip()
    if v == ANNEX_REQ or _NAMED_ANNEX_REQ.match(v):
        return ANNEX
    if v == ELSEWHERE_REQ:
        return ELSEWHERE
    return "foreign"


def project_placement(project_dir: str, site: str) -> dict[str, Any]:
    """{"placement", "source", "foreign"} for one workflow and site.

    ``source`` is "workflow" when the project's pegasus.properties decides,
    "default" when it says nothing (everywhere else). ``foreign`` holds a
    requirements line Studio did not write, which then decides instead.
    """
    value = read_property(Path(project_dir) / _PROPERTIES, requirements_key(site))
    kind = _classify(value)
    if kind is None:
        return {"placement": ELSEWHERE, "source": "default", "foreign": None}
    if kind == "foreign":
        return {"placement": None, "source": "workflow", "foreign": value}
    return {"placement": kind, "source": "workflow", "foreign": None}


def set_project_placement(project_dir: str, site: str, placement: str) -> dict[str, Any]:
    """Write the choice into the project's pegasus.properties.

    Raises ValueError, changing nothing, when the workflow carries a
    requirements line of its own for this site.
    """
    placement = normalise(placement)
    current = project_placement(project_dir, site)
    if current["foreign"]:
        raise ValueError(
            "This workflow's pegasus.properties sets its own requirements for "
            f"{site} ({current['foreign']}); Studio leaves that alone."
        )
    path = Path(project_dir) / _PROPERTIES
    key = requirements_key(site)
    want = ANNEX_REQ if placement == ANNEX else None
    if read_property(path, key) != want and (want or path.exists()):
        write_property(path, key, want)
        log.info("workflow_placement_set", project=project_dir, site=site, placement=placement)
    return project_placement(project_dir, site)


# ── the user-wide default ──────────────────────────────────────────────────


def ensure_default(pegasusrc: Path | None = None, sites: tuple[str, ...] = HOSTED_SITES) -> list[str]:
    """Make "everywhere else" the default in ~/.pegasusrc for the hosted sites.

    Only where the file has no requirements line for that site: one the user
    or an older Studio wrote (the #558 annex pin, say) is left for them. Returns
    the sites a line was added for.
    """
    path = pegasusrc or Path.home() / ".pegasusrc"
    added = []
    for site in sites:
        key = requirements_key(site)
        if read_property(path, key) is None:
            write_property(path, key, ELSEWHERE_REQ, mode=0o600)
            added.append(site)
    if added:
        log.info("placement_default_written", sites=added)
    return added


def plan_args(props: dict[str, str]) -> list[str]:
    """``-Dkey=value`` arguments, one per property, in a stable order (the run
    flow's generic per-run properties; placement no longer uses them)."""
    return [f"-D{k}={v}" for k, v in sorted(props.items())]
