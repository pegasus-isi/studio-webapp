"""Does this workflow need a filesystem its execution site doesn't have? (#520)

A workflow written for a cluster with a shared home (Unity, Expanse) can
assume the worker sees the submit host's paths. On such a cluster that holds:
the catalog is `nonsharedfs` over a home every node mounts. On an HTCondor
pool it doesn't. Studio's own pool and OSPool run `condorio`, the pod and the
access point share `ap:/home/<user>`, and nothing mounts it on the workers.
There every job fails with a missing-file error that names neither the
setting nor the deployment, so the run is stopped before planning with a
message that names both.

What counts as an assumption, each checked against how Pegasus behaves:

- `pegasus.data.configuration = sharedfs`: jobs run in a directory on a
  filesystem the workers are meant to share.
- under `nonsharedfs`, `pegasus.transfer.bypass.input.staging`: PegasusLite
  then reads each input from its original location, so a file:// input in
  the home has to exist on the worker. Under `condorio` the same setting is
  fine and common, because HTCondor transfers the bypassed file from the
  submit host, so it is not flagged there.
- under `sharedfs`/`nonsharedfs`, a directory with `sharedFileSystem: true`
  on the chosen site: the job's scratch is a path the workers would need.
- in any mode, a container mount whose source is under the home or the
  project: bind mounts are resolved on the worker, where neither exists.
  Paths outside the home (/cvmfs on OSPool, a cluster's /scratch) may well
  exist there and are not second-guessed.

The data configuration is read the way the planner reads it: the site's own
`pegasus` profile first (properties are the lowest-priority place for a
profile), then `-D` overrides, the project's pegasus.properties and
~/.pegasusrc, defaulting to `condorio` (Pegasus 5).

Runs after generate, because generators write pegasus.properties and the
catalogs, and only when the site is known to be an HTCondor pool. A site
whose scheduler is unknown is not blocked.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger()

DATA_CONFIG = "pegasus.data.configuration"
BYPASS = "pegasus.transfer.bypass.input.staging"
_TRUE = {"true", "yes", "1", "on"}


def read_properties(path: Path | str) -> dict[str, str]:
    """A Java properties file as {key: value}; {} if unreadable."""
    out: dict[str, str] = {}
    try:
        text = Path(path).read_text()
    except OSError:
        return out
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line[0] in "#!":
            continue
        # key = value, key: value, or key value (Java allows all three).
        for i, ch in enumerate(line):
            if ch in "=:" or ch.isspace():
                key, rest = line[:i], line[i + 1:]
                out[key.strip()] = rest.strip().lstrip("=:").strip()
                break
        else:
            out[line] = ""
    return out


def _merged_properties(
    project_dir: str, plan_properties: dict[str, str] | None, pegasusrc: Path | None
) -> dict[str, str]:
    props = read_properties(pegasusrc or Path.home() / ".pegasusrc")
    props.update(read_properties(Path(project_dir) / "pegasus.properties"))
    props.update(plan_properties or {})
    return props


def _site_entry(project_dir: str, site: str, pegasusrc: Path | None) -> dict[str, Any]:
    """The chosen site's catalog entry: the project's sites.yml, else the
    hosted catalog (the planner's copy in the project, else the listing)."""
    from services import execution_sites as xs

    entry = xs._catalog_sites(os.path.join(project_dir, "sites.yml")).get(site)
    if entry:
        return entry
    hosted = xs.project_hosted_catalog(project_dir) or xs.hosted_catalog(pegasusrc)
    if hosted:
        copy = xs._catalog_sites(os.path.join(project_dir, hosted)).get(site)
        if copy:
            return copy
        return xs._hosted_catalog_view(hosted)[1].get(site) or {}
    return {}


def _profile(entry: dict[str, Any], key: str) -> str | None:
    profiles = entry.get("profiles") or {}
    pegasus = profiles.get("pegasus") if isinstance(profiles, dict) else None
    if isinstance(pegasus, dict) and pegasus.get(key) is not None:
        return str(pegasus[key]).strip()
    return None


def data_configuration(
    site: str, entry: dict[str, Any], props: dict[str, str]
) -> str:
    """sharedfs, nonsharedfs or condorio, as the planner would choose."""
    value = (
        _profile(entry, "data.configuration")
        or props.get(f"pegasus.catalog.site.sites.{site}.profiles.pegasus.data.configuration")
        or props.get(DATA_CONFIG)
        or "condorio"
    )
    return value.strip().lower()


def _shared_directories(entry: dict[str, Any]) -> list[str]:
    dirs = entry.get("directories") or []
    return [
        str(d.get("path") or d.get("type") or "?")
        for d in dirs
        if isinstance(d, dict) and d.get("sharedFileSystem") is True
    ]


def _transformation_docs(project_dir: str) -> list[dict[str, Any]]:
    """The transformation catalogs planning would read: workflow.yml's
    embedded one and a separate transformations.yml / tc.yml."""
    import yaml

    docs: list[dict[str, Any]] = []
    for name in ("workflow.yml", "workflow.yaml"):
        path = os.path.join(project_dir, name)
        try:
            with open(path) as fh:
                data = yaml.safe_load(fh)
        except (OSError, yaml.YAMLError):
            continue
        if isinstance(data, dict) and isinstance(data.get("transformationCatalog"), dict):
            docs.append(data["transformationCatalog"])
        break
    for name in ("transformations.yml", "transformations.yaml", "tc.yml"):
        path = os.path.join(project_dir, name)
        try:
            with open(path) as fh:
                data = yaml.safe_load(fh)
        except (OSError, yaml.YAMLError):
            continue
        if isinstance(data, dict):
            docs.append(data)
    return docs


def _local_mounts(project_dir: str) -> list[str]:
    """Container mount sources that only exist on this side: under the home
    or the project directory."""
    home = os.path.realpath(os.path.expanduser("~"))
    project = os.path.realpath(project_dir)
    found: list[str] = []
    for doc in _transformation_docs(project_dir):
        for container in doc.get("containers") or []:
            if not isinstance(container, dict):
                continue
            for mount in container.get("mounts") or []:
                src = os.path.expandvars(os.path.expanduser(str(mount).split(":", 1)[0]))
                if not os.path.isabs(src):
                    continue
                real = os.path.realpath(src)
                for base in (project, home):
                    if real == base or real.startswith(base + os.sep):
                        found.append(str(mount).split(":", 1)[0])
                        break
    return sorted(set(found))


def assumptions(
    project_dir: str,
    site: str,
    *,
    plan_properties: dict[str, str] | None = None,
    pegasusrc: Path | None = None,
) -> list[str]:
    """What in this workflow needs the site's workers to see this workspace,
    in words for the message. [] when nothing does."""
    entry = _site_entry(project_dir, site, pegasusrc)
    props = _merged_properties(project_dir, plan_properties, pegasusrc)
    mode = data_configuration(site, entry, props)
    reasons: list[str] = []
    if mode == "sharedfs":
        reasons.append(f"it sets {DATA_CONFIG} = sharedfs")
    if mode == "nonsharedfs" and props.get(BYPASS, "").strip().lower() in _TRUE:
        reasons.append(f"it sets {BYPASS} under nonsharedfs, so jobs read inputs where they are")
    if mode in ("sharedfs", "nonsharedfs"):
        shared = _shared_directories(entry)
        if shared:
            reasons.append(
                f"its site catalog gives {site} a shared directory ({', '.join(shared)})"
            )
    mounts = _local_mounts(project_dir)
    if mounts:
        reasons.append(
            "its containers bind-mount files from this workspace ("
            + ", ".join(mounts)
            + ")"
        )
    return reasons


def problem(
    project_dir: str,
    site: str,
    *,
    plan_properties: dict[str, str] | None = None,
    environ: dict[str, str] | None = None,
    pegasusrc: Path | None = None,
) -> str | None:
    """Why this workflow cannot run on `site` for want of a shared
    filesystem, or None. Only a site known to be an HTCondor pool is
    judged: a cluster (slurm/batch) has the shared home these workflows
    expect, `local` runs on the submit host, and an unknown style is given
    the benefit of the doubt."""
    from services import execution_sites as xs

    if site == "local":
        return None
    offered = xs.execution_sites(project_dir, environ=environ, pegasusrc=pegasusrc)
    found = next((s for s in offered["sites"] if s["name"] == site), None)
    if not found or found["style"] != "condor":
        return None
    reasons = assumptions(
        project_dir, site, plan_properties=plan_properties, pegasusrc=pegasusrc
    )
    if not reasons:
        return None
    where = found.get("label") or site
    return (
        f"This workflow expects a shared filesystem ({'; '.join(reasons)}), and "
        f"{where} does not have one: its jobs cannot see files in this "
        "workspace. Choose a place that does, under “Where Workflow Runs”, "
        "or change the workflow so its inputs are staged."
    )
