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

import asyncio
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
        with open(path) as fh:
            return _catalog_sites_text(fh.read())
    except OSError:
        return {}


def _catalog_sites_text(text: str | None) -> dict[str, dict]:
    """{site name: entry} from site-catalog YAML; {} for none or a bad one."""
    if not text:
        return {}
    try:
        import yaml

        data = yaml.safe_load(text)
    except Exception:  # noqa: BLE001 — a bad catalog just offers nothing
        return {}
    if not isinstance(data, dict) or not isinstance(data.get("sites"), list):
        return {}
    return {
        str(s["name"]): s
        for s in data["sites"]
        if isinstance(s, dict) and s.get("name")
    }


def _hosted_catalog_view(name: str) -> tuple[str, dict[str, dict], list[str]]:
    """(label, {site: entry}, needs) for the hosted catalog `name`, read
    without a plan having run. `needs` is the allocation variables the
    catalog references; [] when its text is not at hand.

    The label is the cluster's name ("Unity (UMass)", "Studio's own pool"),
    which is what the run form shows: it does not change between the first
    visit and the first plan, unlike the site's style, which used to be known
    only once the planner left a copy of the catalog in the project. Before
    that, Studio's own pool was labelled an HPC cluster and switched to a
    pool after one run (#517).

    Studio's own catalog is read from the image or the shared copy; a
    published one from the listing cache, which the Resources page and the
    params route keep warm — and may be cold, in which case only the label
    is known and the entries are {}.
    """
    from services import site_config

    if name == site_config.STUDIO_CATALOG:
        label, text = site_config.default_label(), site_config.studio_catalog_text()
    else:
        label, text = site_config.catalog_label(name), site_config.cached_catalog_text(name)
    return label, _catalog_sites_text(text), site_config.needed_vars(text or "")


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
    """{"default": name, "sites": [{name, style, source, label}, ...]}.

    ``local`` is always offered first (planning on the submit host needs no
    catalog entry). ``source`` says where a site came from — "project"
    (its sites.yml), "hosted", "deployment" or "default" — so the UI can
    describe it honestly. ``label`` is the cluster's own name for a site a
    hosted catalog defines (None otherwise): the run form shows it as is,
    so what the user reads does not depend on how much is known about the
    site's scheduler yet (#517). ``advanced`` marks a site the form should
    keep out of the main choice: still selectable, not offered as an equal.
    ``configured`` is False, with ``missing`` naming the fields, when the
    cluster's catalog references allocation details ~/.pegasusrc does not
    set — a site to show, not to run on, until Resources is finished (#516).
    """
    env = os.environ if environ is None else environ
    sites: dict[str, dict[str, Any]] = {}

    def offer(
        name: str, style: str | None, source: str, label: str | None = None
    ) -> None:
        if name == "local":
            return
        known = sites.get(name)
        if known is None:
            sites[name] = {
                "name": name, "style": style, "source": source, "label": label,
                "advanced": False, "configured": True, "missing": [],
            }
            return
        if known["style"] is None and style is not None:
            known["style"] = style
        if known["label"] is None and label is not None:
            known["label"] = label

    project_sites = _catalog_sites(os.path.join(project_dir, "sites.yml"))
    hosted = project_hosted_catalog(project_dir) or hosted_catalog(pegasusrc)
    hosted_label: str | None = None
    hosted_sites: dict[str, dict] = {}
    hosted_needs: list[str] = []
    if hosted:
        hosted_label, catalog_sites, hosted_needs = _hosted_catalog_view(hosted)
        # The planner leaves a copy of the hosted file where it ran; it says
        # which sites the hosted catalog defines and how each submits. The
        # catalog itself is the fallback, so the answer is the same before
        # the first plan.
        hosted_sites = (
            _catalog_sites(os.path.join(project_dir, hosted)) or catalog_sites
        )

    for name, entry in project_sites.items():
        # An overlay on a hosted catalog may state no style of its own, and
        # is still that cluster.
        overlay = name in hosted_sites or (hosted and name == HOSTED_SITE)
        offer(name, site_style(entry) or site_style(hosted_sites.get(name)),
              "project", hosted_label if overlay else None)
    for name, entry in hosted_sites.items():
        offer(name, site_style(entry), "hosted", hosted_label)
    if hosted and not hosted_sites:
        # Neither planned here nor in the cache: hosted catalogs all call
        # their site "compute"; which scheduler is behind it is not known.
        offer(HOSTED_SITE, None, "hosted", hosted_label)

    deployment = (env.get("STUDIO_EXEC_SITE") or "").strip()
    if deployment and deployment != "local":
        deployment_style = (env.get("STUDIO_SITE_STYLE") or "").strip() or None
        offer(deployment, deployment_style, "deployment")
    if not sites:
        # Nothing defines an execution site: the portable generators add this
        # one. Not offered otherwise — on a Slurm-only project it would
        # preselect a pool that does not exist there.
        offer(DEFAULT_CONDOR_SITE, "condor", "default")

    stale_pool = sites.get(DEFAULT_CONDOR_SITE)
    if (
        stale_pool is not None
        and (hosted or deployment)
        and stale_pool["source"] == "project"
        and stale_pool["style"] in ("condor", None)
    ):
        # The generator wrote a `condorpool` into sites.yml — the portable
        # generators do that when they run before a cluster is chosen, and
        # older ones always — and it stays there after the user picks a
        # cluster on Resources. Offered as an equal, the form showed two
        # places to run when the user had chosen one (#518). On Studio's own
        # deployment it is the same pool as "compute" under another name.
        # Kept reachable, under "more places", for a workflow bound to it.
        stale_pool["advanced"] = True

    if hosted_needs:
        # The cluster's catalog wants allocation details (${RESOURCE_*}).
        # Without them the plan goes through and the job fails on the
        # cluster, so the form shows the cluster and says what it still
        # needs instead of letting Run find out (#516).
        from services import site_config

        rc_env = site_config.read_config(pegasusrc)["env"]
        missing = site_config.missing_allocation(hosted_needs, rc_env)
        if missing:
            for site in sites.values():
                if site["label"] == hosted_label:
                    site["configured"] = False
                    site["missing"] = list(missing)

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

    # "This machine" (#516). It is the submit host: the pod, which shares its
    # CPU with JupyterLab and the AI tools, or under Open OnDemand the login
    # node, where running work is usually against the rules. So it is the
    # main choice only when nothing else is, a "more places" option once a
    # pool or cluster is, and not offered at all where the deployment itself
    # is a batch cluster — the administrator's catalog or STUDIO_EXEC_SITE
    # with a Slurm/batch style. Planning against it stays possible (it is a
    # built-in site): site_problem() does not refuse it.
    offered = [
        {
            "name": "local", "style": None, "source": "default", "label": None,
            "advanced": any(not s["advanced"] for s in sites.values()),
            "configured": True, "missing": [],
        }
    ] + list(sites.values())
    if _deployment_is_batch(sites, default, env):
        offered = offered[1:]

    return {"default": default, "sites": offered}


def _deployment_is_batch(
    sites: dict[str, dict[str, Any]], default: str, env: Any
) -> bool:
    """Whether the place this install runs things is a batch cluster: the
    login node is then not somewhere to run an analysis."""
    from services import site_config

    chosen = sites.get(default)
    if chosen is None or chosen["style"] not in ("slurm", "batch"):
        return False
    if chosen["source"] == "deployment":
        return True
    return chosen["source"] == "hosted" and site_config.admin_catalog_file() is not None


async def prime_hosted_catalog(
    project_dir: str, *, pegasusrc: Path | None = None
) -> None:
    """Fetch (into the cache) the published catalog this project plans
    against, so execution_sites() can read its sites' styles synchronously.

    Studio's own catalog is a local file and needs no fetching. Failures are
    the cache's own business: the sites are still offered, by label.
    """
    from services import site_config

    hosted = project_hosted_catalog(project_dir) or hosted_catalog(pegasusrc)
    if not hosted or hosted == site_config.STUDIO_CATALOG:
        return
    if site_config.cached_catalog_text(hosted) is not None:
        return
    loop = asyncio.get_running_loop()
    version = await loop.run_in_executor(None, site_config._local_version)
    await site_config.catalog_text(hosted, site_config.series(version))


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
    found = next((s for s in offered["sites"] if s["name"] == site), None)
    if found is not None and found["configured"]:
        return None
    if found is not None:
        # Offered, but its catalog wants allocation details that are not
        # set. The plan would go through and the job fail on the cluster
        # with the scheduler's own error (#516).
        return (
            f"{found['label'] or site} still needs "
            f"{', '.join(found['missing'])} before an analysis can run "
            "there. Fill that in on the Resources page, or pick another "
            "place under “Where it runs”."
        )
    return (
        f"Nothing defines an execution site named \u201c{site}\u201d for this "
        "workflow: there is no site catalog for it in the project, in its "
        "pegasus.properties, or in ~/.pegasusrc. Pick another place under "
        "\u201cWhere it runs\u201d, or choose your cluster and allocation on "
        "the Resources page."
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
