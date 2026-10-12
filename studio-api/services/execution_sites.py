"""Which execution sites a project's Run form should offer.

The "Where it runs" choice used to be a fixed pair, ``local`` and
``condorpool``. That is right for a workspace attached to an HTCondor pool and
wrong everywhere else: on a Slurm cluster (Studio under Open OnDemand, a
hosted site catalog such as Unity's) the site is called ``compute``, and
offering ``condorpool`` there plans against a site nothing defines.

Workflows written to the portable-sites pattern (pegasus-ai's
``custom_sites.py``) keep scheduler details out of the generator and resolve
the site catalog in this order: an entry in the project's ``sites.yml``, then a
hosted catalog named in ``~/.pegasusrc``, then a default HTCondor
``condorpool``. This module reads the same sources, so the form offers exactly
the sites planning can resolve, and nothing it cannot.

A deployment can steer the defaults without touching any workflow
(``site.env`` in an OOD install, the pod environment otherwise):

``STUDIO_EXEC_SITE``
    The site the form preselects (and offers even if nothing else names it).
``STUDIO_SITE_STYLE`` / ``STUDIO_SITE_QUEUE`` / ``STUDIO_SITE_PROJECT``
    Prefill the generator's ``--site-style``, ``--queue`` and ``--project``
    options, for generators that have them — e.g. ``slurm``, the partition and
    the allocation on a Slurm-only site. Users can still change them.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger()

#: The HTCondor site the portable generators create when nothing else
#: defines one, and Studio's historical default.
DEFAULT_CONDOR_SITE = "condorpool"

#: Hosted catalogs (github.com/pegasushub/pegasus-site-catalogs) all name
#: their execution site this.
HOSTED_SITE = "compute"

#: Generator option (argparse dest) -> deployment variable that prefills it.
GENERATOR_SITE_DEFAULTS = {
    "site_style": "STUDIO_SITE_STYLE",
    "queue": "STUDIO_SITE_QUEUE",
    "project": "STUDIO_SITE_PROJECT",
}


def hosted_catalog(pegasusrc: Path | None = None) -> str | None:
    """The hosted catalog named in ~/.pegasusrc, or None."""
    rc = pegasusrc or Path.home() / ".pegasusrc"
    name = None
    try:
        for line in rc.read_text().splitlines():
            key, sep, value = line.partition("=")
            if sep and key.strip() == "pegasus.catalog.site.repo.file":
                name = value.strip()
    except OSError:
        return None
    return name or None


def _catalog_sites(path: str) -> dict[str, dict]:
    """{site name: entry} from a Pegasus 5 site catalog; {} if unreadable."""
    try:
        import yaml

        with open(path) as fh:
            data = yaml.safe_load(fh)
    except Exception:  # noqa: BLE001 — a bad catalog just offers nothing
        return {}
    if not isinstance(data, dict) or not isinstance(data.get("sites"), list):
        return {}
    return {
        str(s["name"]): s
        for s in data["sites"]
        if isinstance(s, dict) and s.get("name")
    }


def site_style(entry: dict | None) -> str | None:
    """'condor', 'slurm', 'batch' (another scheduler), or None if unstated."""
    profiles = (entry or {}).get("profiles") or {}
    if not isinstance(profiles, dict):
        return None
    style = (profiles.get("pegasus") or {}).get("style")
    grid = str((profiles.get("condor") or {}).get("grid_resource") or "")
    if style == "glite" or grid.startswith("batch"):
        return "slurm" if "slurm" in grid.split() else "batch"
    if style == "condor":
        return "condor"
    return None


def execution_sites(
    project_dir: str,
    *,
    environ: dict[str, str] | None = None,
    pegasusrc: Path | None = None,
) -> dict[str, Any]:
    """{"default": name, "sites": [{name, style, source}, ...]} for a project.

    ``local`` is always offered first (planning on the submit host needs no
    catalog entry). ``source`` says where a site came from — "project"
    (its sites.yml), "hosted", "deployment" or "default" — so the UI can
    describe it honestly.
    """
    env = os.environ if environ is None else environ
    sites: dict[str, dict[str, Any]] = {}

    def offer(name: str, style: str | None, source: str) -> None:
        if name == "local":
            return
        known = sites.get(name)
        if known is None:
            sites[name] = {"name": name, "style": style, "source": source}
        elif known["style"] is None and style is not None:
            known["style"] = style

    project_sites = _catalog_sites(os.path.join(project_dir, "sites.yml"))
    hosted = project_hosted_catalog(project_dir) or hosted_catalog(pegasusrc)
    # The planner leaves a copy of the hosted file where it ran; it says which
    # sites the hosted catalog defines and how each submits.
    hosted_sites = (
        _catalog_sites(os.path.join(project_dir, hosted)) if hosted else {}
    )

    for name, entry in project_sites.items():
        # An overlay on a hosted catalog may state no style of its own.
        offer(name, site_style(entry) or site_style(hosted_sites.get(name)),
              "project")
    for name, entry in hosted_sites.items():
        offer(name, site_style(entry), "hosted")
    if hosted and not hosted_sites:
        # Not planned here yet, so no local copy: hosted catalogs all call
        # their site "compute" and describe batch clusters.
        offer(HOSTED_SITE, None, "hosted")

    deployment = (env.get("STUDIO_EXEC_SITE") or "").strip()
    if deployment and deployment != "local":
        deployment_style = (env.get("STUDIO_SITE_STYLE") or "").strip() or None
        offer(deployment, deployment_style, "deployment")
    if not sites:
        # Nothing defines an execution site: the portable generators add this
        # one. Not offered otherwise — on a Slurm-only project it would
        # preselect a pool that does not exist there.
        offer(DEFAULT_CONDOR_SITE, "condor", "default")

    if deployment:
        default = deployment
    elif hosted and HOSTED_SITE in sites:
        default = HOSTED_SITE
    elif DEFAULT_CONDOR_SITE in sites:
        # Studio's historical default, when the project (or fallback) has it.
        default = DEFAULT_CONDOR_SITE
    else:
        # The project's own site, e.g. a Slurm `compute` in its sites.yml.
        default = next(iter(sites), "local")

    return {
        "default": default,
        "sites": [{"name": "local", "style": None, "source": "default"}]
        + list(sites.values()),
    }


def project_hosted_catalog(project_dir: str) -> str | None:
    """A hosted catalog named in the project's own pegasus.properties.

    The gromacs-template generators write it there (`-s/--hosted-site-catalog`),
    and pegasus-plan reads it ahead of ~/.pegasusrc, so it is checked first.
    """
    return hosted_catalog(Path(project_dir) / "pegasus.properties")


# ── Keeping the site in sync with gromacs-template generators (#466) ──

#: Generator options that name the execution site. The template binds every
#: transformation to this name, so it must equal pegasus-plan's -s: a generator
#: left at its default (`compute`) and a plan against `condorpool` cannot plan.
SITE_ARG_DESTS = frozenset({"execution_site_name"})

#: Sites pegasus-plan defines itself when no catalog does.
_BUILTIN_SITES = frozenset({"local", DEFAULT_CONDOR_SITE})


def _site_param(params: list[dict[str, Any]]) -> dict[str, Any] | None:
    return next((p for p in params if p.get("dest") in SITE_ARG_DESTS), None)


def sync_site_arg(
    args: list[str], params: list[dict[str, Any]], site: str
) -> list[str]:
    """Generator argv with its execution-site option set to `site`.

    Any value already present (a hand-set `-e`, `--execution-site-name=x`) is
    replaced: "Where it runs" is the one place the site is chosen. A generator
    without the option gets its argv back unchanged.
    """
    param = _site_param(params)
    if param is None or not site:
        return list(args)
    flags = [f for f in (param.get("flags") or [param.get("flag")]) if f]
    out: list[str] = []
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in flags:
            i += 2  # the flag and its value
            continue
        if any(arg.startswith(f"{f}=") for f in flags if f.startswith("--")):
            i += 1
            continue
        out.append(arg)
        i += 1
    return out + [str(param.get("flag") or flags[0]), site]


def hide_managed_params(params: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The run form without the options Studio sets itself. Offering `-e` beside
    "Where it runs" would be the same question twice, and the two answers
    disagreeing is exactly the failure being prevented."""
    return [p for p in params if p.get("dest") not in SITE_ARG_DESTS]


def plan_output_dir(project_dir: str, output_site: str = "local") -> str | None:
    """`--output-dir` for pegasus-plan, or None to leave it to the catalog.

    A gromacs-template project defines no `local` site, so Pegasus's built-in
    one would stage results to `./wf-output`; the template plans with
    `--output-dir <project>/output`, and so does Studio. A project whose
    sites.yml says where the output site stores results keeps that.
    """
    from services.run_outputs import _site_catalog_storage

    for name in ("sites.yml", "sites.yaml"):
        catalog = os.path.join(project_dir, name)
        if os.path.isfile(catalog) and _site_catalog_storage(catalog, output_site):
            return None
    return os.path.join(project_dir, "output")


def site_problem(
    project_dir: str,
    site: str,
    *,
    environ: dict[str, str] | None = None,
    pegasusrc: Path | None = None,
) -> str | None:
    """Why planning against `site` cannot work, or None.

    Checked before the run starts, because pegasus-plan reports a site no
    catalog defines as a missing-entry error halfway through its output.
    """
    if site in _BUILTIN_SITES:
        return None
    offered = execution_sites(project_dir, environ=environ, pegasusrc=pegasusrc)
    if any(s["name"] == site for s in offered["sites"]):
        return None
    return (
        f"Nothing defines an execution site named \u201c{site}\u201d for this "
        "workflow: there is no site catalog for it in the project, in its "
        "pegasus.properties, or in ~/.pegasusrc. Pick another place under "
        "\u201cWhere it runs\u201d, or set up a hosted site catalog for your "
        "cluster and allocation."
    )


def apply_generator_site_defaults(
    params: list[dict[str, Any]],
    *,
    environ: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Generator params with deployment site defaults filled in.

    Only options the generator actually has are touched, and a ``choices``
    list is respected, so an older generator — or a value it would reject —
    is left exactly as introspected.
    """
    env = os.environ if environ is None else environ
    out = []
    for p in params:
        var = GENERATOR_SITE_DEFAULTS.get(p.get("dest", ""))
        value = (env.get(var) or "").strip() if var else ""
        choices = p.get("choices")
        if value and (not choices or value in choices):
            p = {**p, "default": value}
        out.append(p)
    return out
