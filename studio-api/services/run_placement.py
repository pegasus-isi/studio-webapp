"""Which machines one run's steps may use, as a pegasus-plan property (#572).

The Run panel's "Where it runs" picks a Pegasus *site*. On the hosted
deployment one site (Studio's pool, ``compute``) is served by more than one
set of machines: Studio's own execution points, the user's annex nodes
(the access point routes each job to whichever is free, pool *or* annex),
and the OSPool for steps carrying a project name. Which of those a run may
use used to be a standing ~/.pegasusrc row (the Resources page's annex-only
switch, #558/#564), which applied to every later run and was easy to leave
on, after which every run waited for nodes that were not coming.

This makes it a choice per run instead. pegasus-plan takes any property
as ``-D``, including site profiles, and a ``-D`` profile overrides the same
key from ~/.pegasusrc while the other keys still merge (verified on the
hosted pod, 2026-10-20: ``-D...condor.requirements`` replaced the pin,
``+ProjectName`` from ~/.pegasusrc stayed). Nothing is written anywhere.

GPUs are not set here. #572 had a per-run "GPUs per step", which gave every
compute step a GPU when typically one or two need one; whether a step needs a
GPU belongs to the workflow, per job or transformation (#576). The Run panel
reads that from the workflow instead (services/workflow_gpus.py).
"""

from __future__ import annotations

import re

PLACEMENTS = ("any", "annex", "pool")

# The site name lands inside a property key; anything odd is refused.
_SITE = re.compile(r"^[A-Za-z0-9_.-]+$")
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def plan_properties(site: str, placement: str, annex_name: str) -> dict[str, str]:
    """The ``-D`` properties for this run; empty for "anywhere".

    - ``annex``: only the user's annex nodes, which carry ``AnnexName``.
    - ``pool``: only machines with no ``AnnexName``, which is Studio's pool
      and the OSPool, never an annex node.
    - ``any``: nothing, so the access point's own annex-or-pool routing
      applies. A requirements row of the user's own in ~/.pegasusrc is
      deliberately left in force.
    """
    if placement not in PLACEMENTS:
        raise ValueError(f"unknown placement {placement!r}")
    if not _SITE.match(site):
        raise ValueError(f"unusable site name {site!r}")
    prefix = f"pegasus.catalog.site.sites.{site}.profiles.condor."
    props: dict[str, str] = {}
    if placement == "annex":
        if not _NAME.match(annex_name):
            raise ValueError(f"unusable annex name {annex_name!r}")
        props[prefix + "requirements"] = f'TARGET.AnnexName =?= "{annex_name}"'
    elif placement == "pool":
        props[prefix + "requirements"] = "TARGET.AnnexName =?= undefined"
    return props


def plan_args(props: dict[str, str]) -> list[str]:
    """``-Dkey=value`` arguments, one per property, in a stable order."""
    return [f"-D{k}={v}" for k, v in sorted(props.items())]
