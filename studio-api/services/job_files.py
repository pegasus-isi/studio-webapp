"""The files a job reads and writes, and where each one can be opened.

Backs the graph's job panel: click a step, see its inputs and outputs, preview
the ones that still exist. The graph alone showed only jobs, so "which step
made this plot, and from what?" meant reading workflow.yml by hand.

The abstract workflow lists every file a job uses (``jobs[].uses``), but not
where it ends up. That depends on what kind of file it is and how far the run
got, so each one is looked for in order:

* **result**: staged out to the output site's storage directory. Only outputs
  with ``stageOut`` land here, and they stay after the run, which also makes
  this the place to find an input that an upstream job produced.
* **scratch**: the run's directory under the local site's ``sharedScratch``.
  Intermediates and staged-in inputs live here while the run is going; the
  cleanup jobs remove them at the end, so an absent scratch file is normal.
* **project**: an input whose replica-catalog entry is a local file inside the
  project, e.g. a samplesheet checked into the repo.

Anything found nowhere is still listed, without a preview: knowing a job
*should* have produced a file is half of debugging why it didn't.

Every location resolves to a base directory, and file content is only ever
served through ``run_outputs.safe_output_path`` inside that base.
"""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlparse

import structlog

from services import run_outputs

log = structlog.get_logger()

#: Ordered: where a file is looked for, by how it is used.
_OUTPUT_ORDER = ("result", "scratch")
# An input produced by an upstream job is often also a staged-out result
# (earthquake's catalog.csv feeds four jobs), and the results outlive scratch.
_INPUT_ORDER = ("scratch", "result", "project")

_WORKFLOW_NAMES = ("workflow.yml", "workflow.yaml")


def _load_yaml(path: str) -> Any:
    try:
        import yaml

        with open(path, encoding="utf-8", errors="replace") as fh:
            return yaml.safe_load(fh)
    except Exception as e:  # noqa: BLE001 — unreadable means "nothing known"
        log.debug("job_files_yaml_unreadable", path=path, error=str(e))
        return None


def find_job(doc: dict[str, Any], job_id: str) -> dict[str, Any] | None:
    """The abstract job a graph node id refers to.

    The default graph uses abstract ids (`ID0000001`); the "Pegasus jobs" view
    uses executable ids, which for a user job are `<transformation>_<id>`.
    Auxiliary jobs (stage_in_…, create_dir_…) match nothing and get no files.
    """
    jobs = [j for j in (doc.get("jobs") or []) if isinstance(j, dict)]
    for j in jobs:
        if str(j.get("id") or "") == job_id:
            return j
    for j in jobs:
        jid = str(j.get("id") or "")
        if jid and job_id.endswith(f"_{jid}"):
            return j
    return None


def job_uses(doc: dict[str, Any], job_id: str) -> list[dict[str, Any]] | None:
    """[{lfn, link, stage_out}] for a job, or None if there is no such job."""
    job = find_job(doc, job_id)
    if job is None:
        return None
    out: list[dict[str, Any]] = []
    for use in job.get("uses") or []:
        if not isinstance(use, dict) or not use.get("lfn"):
            continue
        link = str(use.get("type") or "input").lower()
        out.append({
            "lfn": str(use["lfn"]),
            # Pegasus also has "checkpoint" and "inout"; both are written by
            # the job, so they are found where outputs are.
            "link": "input" if link == "input" else "output",
            "stage_out": bool(use.get("stageOut", link != "input")),
        })
    return out


def _site_directory(catalog: str, site: str, kinds: tuple[str, ...]) -> str | None:
    data = _load_yaml(catalog)
    if not isinstance(data, dict):
        return None
    for entry in data.get("sites") or []:
        if not isinstance(entry, dict) or entry.get("name") != site:
            continue
        for directory in entry.get("directories") or []:
            if not isinstance(directory, dict):
                continue
            kind = str(directory.get("type", "")).lower().replace("_", "")
            if kind in kinds and directory.get("path"):
                return os.path.realpath(str(directory["path"]))
    return None


def scratch_run_dir(run_dir: str, project_dir: str | None) -> str | None:
    """The run's directory under the local site's shared scratch.

    Pegasus lays scratch out like the submit tree: the run's path relative to
    the submit base (`braindump.yml` basedir) under the scratch root. That is
    how earthquake's `submit/kthare10/pegasus/earthquake/run0001` maps to
    `scratch/kthare10/pegasus/earthquake/run0001`.
    """
    from services.workflow_scanner import _parse_braindump

    try:
        bd = _parse_braindump(os.path.join(run_dir, "braindump.yml"))
    except Exception:  # noqa: BLE001
        return None
    basedir = str(bd.get("basedir") or "")
    submit_dir = str(bd.get("submit_dir") or run_dir)
    if not basedir:
        return None
    try:
        relative = os.path.relpath(submit_dir, basedir)
    except ValueError:
        return None
    if relative.startswith(".."):
        return None

    for directory in [run_dir] + ([project_dir] if project_dir else []):
        for name in ("sites.yml", "sites.yaml"):
            catalog = os.path.join(directory, name)
            if not os.path.isfile(catalog):
                continue
            root = _site_directory(catalog, "local", ("sharedscratch",))
            if root:
                candidate = os.path.join(root, relative)
                return candidate if os.path.isdir(candidate) else None
    return None


def _replica_paths(doc: dict[str, Any], project_dir: str | None) -> dict[str, str]:
    """lfn -> local path, for replica entries that are plain files."""
    entries: list[Any] = []
    rc = doc.get("replicaCatalog")
    if isinstance(rc, dict):
        entries += rc.get("replicas") or []
    if project_dir:
        for name in ("replicas.yml", "replicas.yaml"):
            path = os.path.join(project_dir, name)
            if not os.path.isfile(path):
                continue
            data = _load_yaml(path)
            if isinstance(data, dict):
                entries += data.get("replicas") or []
    out: dict[str, str] = {}
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get("lfn"):
            continue
        for pfn in entry.get("pfns") or []:
            raw = str((pfn or {}).get("pfn") or "") if isinstance(pfn, dict) else ""
            parsed = urlparse(raw)
            if parsed.scheme in ("", "file") and (parsed.path or raw).startswith("/"):
                out.setdefault(str(entry["lfn"]), parsed.path or raw)
                break
    return out


def bases_for_run(
    run_dir: str, roots: list[str], output_site: str = "local"
) -> dict[str, str]:
    """location -> base directory, for the locations this run has."""
    project_dir = run_outputs.project_dir_for_run(run_dir, roots)
    bases: dict[str, str] = {}
    output_dir, _ = run_outputs.resolve_output_dir(run_dir, roots, output_site)
    if output_dir and os.path.isdir(output_dir):
        bases["result"] = output_dir
    scratch = scratch_run_dir(run_dir, project_dir)
    if scratch:
        bases["scratch"] = scratch
    if project_dir and os.path.isdir(project_dir):
        bases["project"] = os.path.realpath(project_dir)
    return bases


def _locate(
    lfn: str,
    order: tuple[str, ...],
    bases: dict[str, str],
    replicas: dict[str, str],
) -> tuple[str, str, str] | None:
    """(location, path relative to its base, full path) for the first hit."""
    for location in order:
        base = bases.get(location)
        if not base:
            continue
        if location == "project":
            local = replicas.get(lfn)
            if not local:
                continue
            try:
                full = run_outputs.safe_output_path(
                    base, os.path.relpath(os.path.realpath(local), base)
                )
            except ValueError:
                continue  # a replica outside the project is never served
        else:
            try:
                full = run_outputs.safe_output_path(base, lfn)
            except ValueError:
                continue
        if os.path.isfile(full):
            return location, os.path.relpath(full, os.path.realpath(base)), full
    return None


def describe(
    uses: list[dict[str, Any]],
    bases: dict[str, str],
    doc: dict[str, Any],
) -> list[dict[str, Any]]:
    """Each file a job uses, with where it can be opened (if anywhere)."""
    project_dir = bases.get("project")
    replicas = _replica_paths(doc, project_dir)
    files: list[dict[str, Any]] = []
    for use in uses:
        order = _OUTPUT_ORDER if use["link"] == "output" else _INPUT_ORDER
        name = os.path.basename(use["lfn"]) or use["lfn"]
        item: dict[str, Any] = {
            "lfn": use["lfn"],
            "name": name,
            "link": use["link"],
            "stage_out": use["stage_out"],
            "kind": run_outputs.classify(name),
            "location": None,
            "path": None,
            "size": None,
            "previewable": False,
        }
        hit = _locate(use["lfn"], order, bases, replicas)
        if hit:
            location, relative, full = hit
            entry = run_outputs._entry(full, relative)  # size/kind/previewable
            if entry:
                item.update(
                    location=location,
                    path=relative,
                    size=entry["size"],
                    previewable=entry["previewable"],
                )
        files.append(item)
    return files


def workflow_doc(path: str | None) -> dict[str, Any]:
    data = _load_yaml(path) if path else None
    return data if isinstance(data, dict) else {}
