"""Where a run's results actually are, and what's in them.

Phase 4 of the non-specialist redesign (#241). Studio has never had a
concept of a run's *outputs*: no endpoint, no UI. A scientist runs an analysis
in order to get files back, and today the only way to find them is to leave for
JupyterLab and go hunting through the filesystem — which requires knowing that
Pegasus stages results to a storage directory, and where the site catalog says
that is. This module answers "where are my results" so the UI can stop making
that the user's problem.

## Finding the output directory

Pegasus stages outputs to the *output site*'s storage directory, declared in
the site catalog the generator writes. That catalog is the authoritative
answer, so it is tried first and the convention-based guesses only run when it
can't be read. Every result records *how* it was found, because a guess and a
declaration deserve different confidence in the UI — telling a user "no results
found" is very different depending on whether we read a catalog or just checked
for a directory named `output`.
"""

from __future__ import annotations

import mimetypes
import os
from datetime import datetime, timezone
from collections.abc import Sequence
from typing import Any

import structlog

from workspace import is_protected

log = structlog.get_logger()

# Conventional storage directory names, in preference order. Used only when the
# site catalog can't be read — every Pegasus workflow in the wild that doesn't
# declare one puts results in a directory named some variant of "output".
_CONVENTIONAL_DIRS = ("output", "outputs", "wf-output", "results")

# Files Pegasus leaves in a storage dir that are bookkeeping, not results.
_NOISE_NAMES = {
    ".pegasus-metadata",
    "braindump.yml",
    "braindump.yaml",
}

# Extensions we can show inline, grouped by how the UI should render them.
_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".bmp"}
_PDF_EXT = {".pdf"}
_HTML_EXT = {".html", ".htm"}
_TABLE_EXT = {".csv", ".tsv"}
_TEXT_EXT = {
    ".txt", ".log", ".md", ".json", ".yml", ".yaml", ".xml",
    ".py", ".sh", ".r", ".cfg", ".ini", ".toml", ".dat", ".out", ".err",
}

# Above this, don't offer an inline preview — offer a download instead. A
# scientist's output can be gigabytes, and pulling one into a browser tab to
# render it as text is a way to hang their session, not a feature.
PREVIEW_MAX_BYTES = 2 * 1024 * 1024  # 2 MB

# Images are drawn by the browser from the URL, not read into the page as text,
# so the text ceiling doesn't protect anything for them. Held at 2 MB it hid
# every plot earthquake-workflow makes (2.8-3.1 MB PNGs) behind Download.
IMAGE_PREVIEW_MAX_BYTES = 25 * 1024 * 1024  # 25 MB


def preview_limit(kind: str) -> int:
    """The largest file of this kind that may be shown inline. PDFs, like
    images, are rendered by the browser from the URL, not read into the page."""
    return IMAGE_PREVIEW_MAX_BYTES if kind in ("image", "pdf", "html") else PREVIEW_MAX_BYTES


#: Kinds that can be previewed in part: the start of a long table or log is
#: as useful as the whole for a glance, and costs the page only the limit.
_PARTIAL_KINDS = frozenset({"text", "table", "html"})


def can_preview(kind: str, size: int) -> tuple[bool, bool]:
    """(previewable, partial) for a file of this kind and size."""
    if kind == "binary":
        return False, False
    if size <= preview_limit(kind):
        return True, False
    return (True, True) if kind in _PARTIAL_KINDS else (False, False)


#: Types a browser renders without ever running a script from the file, so
#: they may be served inline as themselves with no sandbox. Everything else is
#: sandboxed. PDF is here because Chrome refuses to show a PDF under a CSP
#: sandbox, and its viewer runs the document's scripts in its own isolation.
_INERT_INLINE_TYPES = frozenset({
    "image/png", "image/jpeg", "image/gif", "image/webp", "image/bmp",
    "application/pdf",
})


def serve_media_type(path: str) -> str:
    """The Content-Type to serve a workflow file inline with.

    Chosen here, never left to FileResponse, because Starlette guesses from
    the whole name: `report.html.gz` comes back `text/html` with no
    Content-Encoding, so the browser renders the plain HTML inside, and
    nothing that looked at the last extension saw it coming. A compressed
    file is bytes to the browser, not the document it unpacks to.
    """
    media, encoding = mimetypes.guess_type(path)
    if encoding:
        return "application/octet-stream"
    return media or "text/plain"


def serve_headers(path: str) -> dict[str, str]:
    """Extra response headers for serving a workflow file inline.

    Workflow outputs are untrusted and served from Studio's own origin, where
    the session cookie rides along on every request. A document opened as a
    page would otherwise run its scripts as Studio. The CSP `sandbox`
    directive gives it an opaque origin: scripts still run, so interactive
    reports work, but they cannot read Studio's cookies or responses, and
    their writes are refused as cross-origin.

    This is an allowlist (#470 was a list of dangerous extensions, and
    `.xsl`, `.rdf`, `.html.gz` and whatever /etc/mime.types adds all got
    past it): only types that cannot run a script go unsandboxed. Text,
    tables and JSON are read by the page with fetch(), which the sandbox
    does not touch. `nosniff` stops a browser second-guessing the type.
    """
    headers = {"X-Content-Type-Options": "nosniff"}
    if serve_media_type(path) not in _INERT_INLINE_TYPES:
        headers["Content-Security-Policy"] = "sandbox allow-scripts"
    return headers


def read_head(path: str, limit: int = PREVIEW_MAX_BYTES) -> bytes:
    """The first `limit` bytes of a file, cut back to the last line break so
    a table never ends on half a row (nor text on half a UTF-8 character)."""
    with open(path, "rb") as fh:
        data = fh.read(limit)
    cut = data.rfind(b"\n")
    return data[: cut + 1] if cut > 0 else data

# Hard ceiling on how many files we will even enumerate. A run that wrote
# 200k files should degrade to "here are the first 5000" rather than blocking
# the event loop walking the tree.
_WALK_LIMIT = 5000


def classify(name: str) -> str:
    """Coarse file kind, for choosing a preview renderer."""
    ext = os.path.splitext(name)[1].lower()
    if ext in _IMAGE_EXT:
        return "image"
    if ext in _PDF_EXT:
        return "pdf"
    if ext in _HTML_EXT:
        return "html"
    if ext in _TABLE_EXT:
        return "table"
    if ext in _TEXT_EXT:
        return "text"
    return "binary"


def _site_catalog_storage(catalog_path: str, output_site: str) -> str | None:
    """The localStorage path for `output_site` from a Pegasus 5 site catalog."""
    try:
        import yaml

        with open(catalog_path) as fh:
            data = yaml.safe_load(fh)
    except Exception:  # noqa: BLE001 — unreadable catalog just means "guess"
        return None
    if not isinstance(data, dict):
        return None

    sites = data.get("sites")
    if not isinstance(sites, list):
        return None
    for site in sites:
        if not isinstance(site, dict) or site.get("name") != output_site:
            continue
        for directory in site.get("directories") or []:
            if not isinstance(directory, dict):
                continue
            # Pegasus 5 writes "localStorage"; be lenient about spelling so a
            # catalog written by hand still resolves.
            if str(directory.get("type", "")).lower().replace("_", "") in (
                "localstorage",
                "sharedstorage",
            ):
                path = directory.get("path")
                if path:
                    return os.path.realpath(str(path))
    return None


def project_dir_for_run(
    run_dir: str, workflows_root: str | Sequence[str]
) -> str | None:
    """The project directory a run belongs to.

    Runs live at `<workflows_root>/<project>/…/run0001`, so the project is the
    first path component under the workflows root. Returns None for a run that
    isn't under any of them (a workflow submitted by hand elsewhere).

    Several roots are accepted because projects are listed from more than one:
    the workspace's own `workflows/`, plus the legacy `work/workflows/`. A run
    in the legacy tree that resolved to no project lost its sites.yml, and with
    it the difference between "this workflow puts results here and there are
    none yet" and "we guessed and found nothing".
    """
    roots = [workflows_root] if isinstance(workflows_root, str) else workflows_root
    run = os.path.realpath(run_dir)
    for candidate in roots:
        root = os.path.realpath(candidate)
        if not run.startswith(root + os.sep):
            continue
        relative = run[len(root) + 1 :]
        project = relative.split(os.sep, 1)[0]
        if project:
            return os.path.join(root, project)
    return None


def planned_output_dir(run_dir: str) -> str | None:
    """The `--output-dir` the run was planned with, from braindump.yml.

    It overrides every catalog: Pegasus staged this run's results there. The
    gromacs-template projects define no local site and are planned with it
    (#466).
    """
    import shlex

    try:
        from services.workflow_scanner import _parse_braindump

        bd = _parse_braindump(os.path.join(run_dir, "braindump.yml"))
        argv = shlex.split(str(bd.get("planner_arguments") or "").strip().strip('"'))
    except Exception:  # noqa: BLE001 — no braindump or unparsable: no override
        return None
    for i, arg in enumerate(argv):
        if arg == "--output-dir" and i + 1 < len(argv):
            return argv[i + 1]
        if arg.startswith("--output-dir="):
            return arg.split("=", 1)[1]
    return None


def run_catalogs(run_dir: str, project_dir: str | None) -> list[str]:
    """Site catalogs that may describe a run, most authoritative first.

    `catalogs/sites-combined.yml` is what pegasus-plan actually used: the
    project's sites.yml, any hosted catalog and its own built-in local site,
    merged. A gromacs-template project has no sites.yml at all, so without it
    nothing says where scratch is (`wf-scratch/LOCAL`).
    """
    out = [os.path.join(run_dir, "catalogs", "sites-combined.yml")]
    for directory in [run_dir] + ([project_dir] if project_dir else []):
        out += [os.path.join(directory, n) for n in ("sites.yml", "sites.yaml")]
    return [c for c in out if os.path.isfile(c)]


def resolve_output_dir(
    run_dir: str,
    workflows_root: str | Sequence[str],
    output_site: str = "local",
) -> tuple[str | None, str]:
    """(output directory, how it was found).

    `found_via` is one of "catalog", "convention", or "none" — the UI uses it
    to distinguish "the workflow says it puts results here and there are none
    yet" from "we guessed and found nothing".
    """
    project_dir = project_dir_for_run(run_dir, workflows_root)

    # 0. An explicit --output-dir wins over any catalog's storage directory.
    planned = planned_output_dir(run_dir)
    if planned:
        return os.path.realpath(planned), "catalog"

    # 1. The site catalog the run used, then the project's. pegasus-plan
    #    leaves its merged catalog in the submit directory — the one actually
    #    used for this run, not whatever the project has since regenerated.
    for candidate in run_catalogs(run_dir, project_dir):
        storage = _site_catalog_storage(candidate, output_site)
        if storage:
            return storage, "catalog"

    # 2. Convention, relative to the project.
    if project_dir:
        for name in _CONVENTIONAL_DIRS:
            candidate = os.path.join(project_dir, name)
            if os.path.isdir(candidate):
                return os.path.realpath(candidate), "convention"

    return None, "none"


def _entry(full_path: str, relative: str) -> dict[str, Any] | None:
    try:
        stat = os.stat(full_path)
    except OSError:
        return None
    name = os.path.basename(relative)
    kind = classify(name)
    return {
        "path": relative,
        "name": name,
        "size": stat.st_size,
        "modified": datetime.fromtimestamp(
            stat.st_mtime, tz=timezone.utc
        ).isoformat(),
        "kind": kind,
        # Previewability is a property of the file, decided here rather than in
        # the UI, so the size ceiling can't drift between the two.
        "previewable": can_preview(kind, stat.st_size)[0],
        # Over the limit but worth a look: the preview shows the start (#460).
        "partial": can_preview(kind, stat.st_size)[1],
    }


def collect(output_dir: str) -> tuple[list[dict[str, Any]], bool]:
    """(files, hit_walk_limit) under `output_dir`, newest first.

    Newest first because the most recently written file is usually the one a
    user is looking for — a plot or summary produced by the last step, not the
    intermediate the first step wrote.
    """
    files: list[dict[str, Any]] = []
    truncated = False

    for dirpath, dirnames, filenames in os.walk(output_dir):
        # Don't descend into Pegasus bookkeeping, or into hidden directories:
        # hidden files were already skipped, but opening a result in
        # JupyterLab writes `.ipynb_checkpoints/<name>-checkpoint.<ext>` beside
        # it, and that copy was listed as a second result of the run.
        dirnames[:] = [
            d for d in dirnames if d not in _NOISE_NAMES and not d.startswith(".")
        ]
        for filename in filenames:
            if filename in _NOISE_NAMES or filename.startswith("."):
                continue
            full = os.path.join(dirpath, filename)
            relative = os.path.relpath(full, output_dir)
            entry = _entry(full, relative)
            if entry is not None:
                files.append(entry)
            if len(files) >= _WALK_LIMIT:
                truncated = True
                break
        if truncated:
            break

    files.sort(key=lambda f: str(f["modified"]), reverse=True)
    return files, truncated


def listing(
    run_dir: str,
    workflows_root: str | Sequence[str],
    *,
    output_site: str = "local",
    offset: int = 0,
    limit: int = 100,
) -> dict[str, Any]:
    """A page of a run's outputs, plus enough context to explain an empty one."""
    output_dir, found_via = resolve_output_dir(
        run_dir, workflows_root, output_site
    )
    if output_dir is None or not os.path.isdir(output_dir):
        return {
            "output_dir": output_dir,
            "found_via": found_via,
            "exists": False,
            "total": 0,
            "files": [],
            "truncated": False,
            "offset": offset,
            "limit": limit,
        }

    files, truncated = collect(output_dir)
    page = files[offset : offset + limit]
    return {
        "output_dir": output_dir,
        "found_via": found_via,
        "exists": True,
        "total": len(files),
        "files": page,
        "truncated": truncated,
        "offset": offset,
        "limit": limit,
    }


def safe_output_path(output_dir: str, relative: str) -> str:
    """Resolve `relative` inside `output_dir`, or raise ValueError.

    The outputs API takes a caller-supplied path, so this is the boundary that
    stops `../../.ssh/id_rsa` from being served. Resolve first, compare after —
    checking for ".." in the string is defeated by symlinks and by encodings
    that normalize to it.
    """
    root = os.path.realpath(output_dir)
    resolved = os.path.realpath(os.path.join(root, relative))
    if resolved != root and not resolved.startswith(root + os.sep):
        raise ValueError("path escapes the output directory")
    # Staying inside the output directory is not enough on its own: where that
    # directory is comes from the workflow's site catalog, which a cloned repo
    # writes, and a storage dir of $HOME puts .ssh, .condor (the pool token)
    # and .studio (the settings DB) "inside" it. Same boundary as the file API.
    if is_protected(resolved):
        raise ValueError("that directory holds credentials")
    return resolved
