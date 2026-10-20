"""Which of a workflow's steps declare that they need a GPU (#576).

Whether a step needs a GPU is the workflow's to say, per job or per
transformation, with a Pegasus ``gpus`` or HTCondor ``request_gpus``
profile. HTCondor gives a GPU only to a job that asks, and Pegasus then
adds Singularity's ``--nv`` for its container (PM-1621). A per-run "GPUs per
step" (#572) gave every compute step a GPU, wasting them on the steps that
do not use one, so it is gone. What the Run panel needs instead is to know
whether this workflow's steps will ask, so it can say when no machine has
a GPU to give them.

Reads the generated workflow and a separate transformation catalog if the
project has one. Before the generator has run there is nothing to read,
and the answer is "none declared"; that is the honest answer, not a
guarantee.
"""

from __future__ import annotations

import os
from typing import Any

import structlog

log = structlog.get_logger()

# Bigger files are skipped: this is a hint, never worth parsing a giant DAX.
_MAX_BYTES = 20 * 1024 * 1024
_CATALOGS = ("transformations.yml", "transformations.yaml", "tc.yml", "tc.yaml")


def _positive(value: Any) -> bool:
    try:
        return float(value) > 0
    except (TypeError, ValueError):
        return False


def _asks(profiles: Any) -> bool:
    if not isinstance(profiles, dict):
        return False
    pegasus = profiles.get("pegasus") or {}
    condor = profiles.get("condor") or {}
    return (isinstance(pegasus, dict) and _positive(pegasus.get("gpus"))) or (
        isinstance(condor, dict) and _positive(condor.get("request_gpus"))
    )


def _walk(node: Any, found: set[str], owner: str | None = None, named: bool = True) -> None:
    """Collect the names of jobs/transformations whose profiles ask for GPUs,
    wherever they sit (job, transformation, or a transformation's site).

    A transformation's `sites` entries carry the *site's* name; a profile
    there still belongs to the transformation, so they do not rename it.
    """
    if isinstance(node, dict):
        name = (node.get("name") or node.get("id") or owner) if named else owner
        if _asks(node.get("profiles")):
            found.add(str(name) if name else "(unnamed)")
        for key, value in node.items():
            if key != "profiles":
                _walk(value, found, str(name) if name else owner, named=key != "sites")
    elif isinstance(node, list):
        for item in node:
            _walk(item, found, owner, named)


def gpu_steps(project_dir: str, workflow_file: str | None) -> list[str]:
    """Names of the jobs or transformations that ask for a GPU, sorted."""
    import yaml

    paths = [workflow_file] if workflow_file else []
    paths += [os.path.join(project_dir, n) for n in _CATALOGS]
    found: set[str] = set()
    for path in paths:
        try:
            if not path or not os.path.isfile(path) or os.path.getsize(path) > _MAX_BYTES:
                continue
            with open(path, encoding="utf-8", errors="replace") as fh:
                _walk(yaml.safe_load(fh), found)
        except (OSError, yaml.YAMLError) as e:
            log.debug("workflow_gpus_unreadable", path=path, error=str(e))
    return sorted(found)
