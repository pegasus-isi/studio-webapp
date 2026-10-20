"""The user's HTCondor annex: nodes they leased from a cluster of their own.

On the hosted deployment extra compute comes from an *annex* (HTCondor's
"Bringing Your Own Resources"): the user runs ``htcondor annex create
$USER`` here, carries the tarball to a Slurm cluster they have an account
on, and submits it there. The nodes it starts join the access point and
run only that user's jobs. The access point stamps every job with
``TargetAnnexName = Owner`` (a job transform, see
``docker/htcondor/90-studio.conf``), so the annex *must* be named after the
user, and jobs still run on Studio's pool when the annex is down.

Status is what ``htcondor annex status <name>`` prints, but structured.
That command's only output is prose, and it talks to three places the pool
snapshot in :mod:`services.condor_pool` never looks at:

* the schedd, for the *annex request* jobs (``hpc_annex_name`` set), one
  per ``htcondor annex create``/``add``;
* ``ANNEX_COLLECTOR``, a collector the access point runs for annex slots
  alone (``ap:9618?sock=ap_collector``): annex nodes never register with
  the pool's central manager, so ``condor_status`` against the pool shows
  nothing and the pool's "annex" bucket stays empty;
* the schedd again, for the jobs targeting the annex.

The same three queries, through the JSON CLI (the image has the ``htcondor``
command but no Python bindings), summarized by :func:`summarize`, which is
pure so tests can feed it ClassAds. Mirrors
``htcondor_cli/annex.py::Status`` upstream, including its reading of request
state: a request job that is IDLE has been granted; one with slots in the
collector is part of the established annex; granted with no slots left is
retired.
"""

from __future__ import annotations

import asyncio
import getpass
import os
import re
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

import structlog

from services.condor_pool import _lower_keys, _run_json

log = structlog.get_logger()

_CACHE_TTL = float(os.environ.get("STUDIO_ANNEX_CACHE_TTL", "30"))
_CMD_TIMEOUT = 20

# Annex names are usernames; anything else is refused before it reaches a
# ClassAd constraint.
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

_cache: dict[str, Any] = {"ts": 0.0, "data": None}
_cache_lock = asyncio.Lock()
# One create/shutdown at a time: each create submits a request job and
# writes a tarball, and two at once would race on both.
_action_lock = asyncio.Lock()
# ANNEX_COLLECTOR / HPC_ANNEX_ENABLED from condor_config_val, read once: they
# are static per container. None until read; "" when annex is off here.
_config: dict[str, str] | None = None


def annex_name() -> str:
    """The annex a user's jobs target: their username (the AP's transform)."""
    return (
        os.environ.get("STUDIO_ANNEX_NAME")
        or os.environ.get("USER")
        or getpass.getuser()
    )


async def _config_val(name: str) -> str:
    """One value from the local HTCondor config, "" when unset or unreadable.
    One name per call: with several, condor_config_val leaves a missing one
    out of stdout and the remaining lines no longer line up."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "condor_config_val", name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=_CMD_TIMEOUT)
    except (FileNotFoundError, OSError, asyncio.TimeoutError):
        return ""
    if proc.returncode != 0:
        return ""
    value = stdout.decode("utf-8", errors="replace").strip()
    return "" if "not defined" in value.lower() else value


async def _condor_config() -> dict[str, str]:
    """{"enabled": "true"/"", "collector": address or ""} from the local
    HTCondor config, where ``htcondor annex`` itself reads them. Read once:
    they are static for the life of the container."""
    global _config
    if _config is not None:
        return _config
    enabled = (await _config_val("HPC_ANNEX_ENABLED")).lower() == "true"
    # `htcondor annex create` refuses to run with nothing queued for the
    # annex unless the administrator sets HPC_ANNEX_REQUIRE_JOB = False.
    # The page gates its button on the same rule (#537).
    require_job = (await _config_val("HPC_ANNEX_REQUIRE_JOB")).lower() != "false" if enabled else True
    config = {
        "enabled": "true" if enabled else "",
        "collector": await _config_val("ANNEX_COLLECTOR") if enabled else "",
        "require_job": "true" if require_job else "",
    }
    _config = config
    return config


def _state(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _num(value: Any) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return 0


def summarize(
    name: str,
    requests: list[dict[str, Any]],
    slots: list[dict[str, Any]],
    jobs: list[dict[str, Any]],
) -> dict[str, Any]:
    """One annex's status from its request jobs, its slots, and the jobs
    targeting it. Keys of every ad are matched case-insensitively."""
    # request id -> "requested" | "granted" | "held" | dict of slot aggregates
    by_request: dict[str, Any] = {}
    hold_reason: str | None = None
    for ad in requests:
        ad = _lower_keys(ad)
        rid = str(ad.get("hpc_annex_request_id", ""))
        state = _state(ad.get("jobstatus"))
        if state in (3, 4):
            # Removed or completed: on its way out of the queue, and not a
            # request anyone is waiting on. Counting it as pending kept a
            # removed run's request on the status card (#558).
            continue
        if state == 5:
            # Held. Upstream folds this into "pending", which hid the one
            # line that says what is wrong (the first live test's request
            # sat held on a missing working directory, #558).
            by_request[rid] = "held"
            hold_reason = hold_reason or (str(ad.get("holdreason") or "") or None)
            continue
        by_request[rid] = "granted" if state == 1 else "requested"

    oldest_start: int | None = None
    oldest_stop: int | None = None
    for ad in slots:
        ad = _lower_keys(ad)
        rid = str(ad.get("hpc_annex_request_id", "None"))
        agg = by_request.get(rid)
        if not isinstance(agg, dict):
            agg = by_request[rid] = {"nodes": 0, "total_cpus": 0, "busy_cpus": 0}
        # Partitionable slots only: each is one node, and its children are
        # the busy part. Static slots never come from an annex.
        if ad.get("partitionableslot"):
            agg["nodes"] += 1
            agg["total_cpus"] += _num(ad.get("totalslotcpus"))
            children = ad.get("childcpus") or []
            agg["busy_cpus"] += sum(_num(c) for c in children) if isinstance(children, list) else 0
        start = ad.get("daemonstarttime")
        if isinstance(start, (int, float)) and (oldest_start is None or start < oldest_start):
            oldest_start = int(start)
            stop = ad.get("daemonstoptime")
            oldest_stop = int(stop) if isinstance(stop, (int, float)) else None

    pending = active = retired = held_requests = 0
    nodes = total_cpus = busy_cpus = 0
    for value in by_request.values():
        if isinstance(value, dict):
            active += 1
            nodes += value["nodes"]
            total_cpus += value["total_cpus"]
            busy_cpus += value["busy_cpus"]
        elif value == "granted":
            retired += 1
        elif value == "held":
            held_requests += 1
        else:
            pending += 1

    running = idle = held = 0
    for ad in jobs:
        status = _state(_lower_keys(ad).get("jobstatus"))
        if status == 2:
            running += 1
        elif status == 1:
            idle += 1
        elif status == 5:
            held += 1

    return {
        "name": name,
        "established": total_cpus > 0,
        "nodes": nodes,
        "total_cpus": total_cpus,
        "busy_cpus": busy_cpus,
        "requests": {
            "total": len(by_request),
            "pending": pending,
            "active": active,
            "retired": retired,
            "held": held_requests,
        },
        "hold_reason": hold_reason,
        "jobs": {"total": len(jobs), "running": running, "idle": idle, "held": held},
        "oldest_started_at": oldest_start,
        "oldest_retires_at": oldest_stop,
    }


# ── the setup bundle and the two actions (#534) ────────────────────────────


def bundle_dir() -> Path:
    """Where Studio keeps the annex tarball: under ~/.studio, which the file
    tools treat as protected (workspace.is_protected), because the tarball
    carries an IDTOKEN good for 90 days."""
    return Path(os.environ.get("STUDIO_ANNEX_DIR") or Path.home() / ".studio" / "annex")


# Marks a bundle directory whose `htcondor annex create` has not finished.
CREATING = ".creating"


def current_bundle() -> dict[str, Any] | None:
    """The newest tarball `htcondor annex create` left, or None."""
    root = bundle_dir()
    try:
        # Finished directories only: a create in progress marks its
        # directory with CREATING until the tarball is complete.
        tars = sorted(
            (
                p
                for p in root.glob("[!.]*/annex-*.tar")
                if p.is_file() and not (p.parent / CREATING).exists()
            ),
            key=lambda p: p.stat().st_mtime,
        )
    except OSError:
        return None
    if not tars:
        return None
    tar = tars[-1]
    st = tar.stat()
    return {
        "filename": tar.name,
        "path": str(tar),
        "size": st.st_size,
        "created_at": int(st.st_mtime),
    }


async def _run_text(
    cmd: list[str], timeout: float, cwd: str | None = None
) -> tuple[int | None, str]:
    """Run a command, returning (exit code or None, combined output)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except FileNotFoundError:
        return None, f"{cmd[0]} not found"
    except asyncio.TimeoutError:
        # Reap it: a create left running would still submit its request
        # job and write into a directory about to be removed.
        try:
            proc.kill()
            await proc.wait()
        except (ProcessLookupError, OSError):
            pass
        return None, f"{cmd[0]} timed out after {int(timeout)}s"
    except OSError as e:
        return None, f"{cmd[0]} failed: {e}"
    return proc.returncode, out.decode("utf-8", errors="replace").strip()


def _last_line(text: str, fallback: str) -> str:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return lines[-1][:300] if lines else fallback


async def _request_dirs(name: str) -> set[str] | None:
    """Working directories of this annex's request jobs still in the queue,
    or None when the schedd cannot be asked (then nothing is deleted)."""
    ads, _err = await _run_json([
        "condor_q", "-json",
        "-constraint", f'hpc_annex_name == "{name}"',
        "-attributes", "Iwd",
    ])
    if ads is None:
        return None
    dirs: set[str] = set()
    for ad in ads:
        iwd = _lower_keys(ad).get("iwd")
        if iwd:
            dirs.add(os.path.realpath(str(iwd)))
    return dirs


async def _prune(root: Path, keep: Path, name: str) -> None:
    """Drop old bundle directories, but never one a request job still runs
    in: the schedd restarts a request (a local-universe job) in its working
    directory, and a missing one holds it."""
    in_use = await _request_dirs(name)
    if in_use is None:
        return
    for old in root.iterdir():
        if old == keep or os.path.realpath(old) in in_use:
            continue
        if old.is_symlink():
            old.unlink(missing_ok=True)
        else:
            shutil.rmtree(old, ignore_errors=True)


async def create_bundle() -> dict[str, Any]:
    """Run `htcondor annex create <user>` and keep its tarball.

    The command also submits one annex *request* job to the schedd, a
    local-universe job whose working directory is the directory the
    command ran in, and the schedd restarts it there. So the command runs
    in the directory the bundle is published from, and that directory is
    never renamed or deleted while the request lives. Studio used to stage
    in a temporary directory and rename it on success, and the first live
    annex test's request went on hold on the next restart: "Cannot access
    initial working directory" (#558). A CREATING marker keeps the
    directory out of current_bundle() until the tarball is complete.
    """
    name = annex_name()
    if not _SAFE_NAME.match(name):
        return {"ok": False, "error": f"unusable annex name {name!r}"}
    async with _action_lock:
        root = bundle_dir()
        try:
            root.mkdir(parents=True, exist_ok=True)
            os.chmod(root, 0o700)
        except OSError as e:
            return {"ok": False, "error": f"cannot create {root}: {e}"}
        try:
            work = Path(tempfile.mkdtemp(prefix=time.strftime("%Y%m%d-%H%M%S-"), dir=root))
            (work / CREATING).touch()
        except OSError as e:
            return {"ok": False, "error": f"cannot create a working directory: {e}"}
        rc, out = await _run_text(["htcondor", "annex", "create", name], 120, cwd=str(work))
        tar = work / f"annex-{name}.tar"
        if rc != 0 or not tar.is_file():
            shutil.rmtree(work, ignore_errors=True)
            log.warning("annex_create_failed", name=name, rc=rc, output=out[-500:])
            error = _last_line(out, "htcondor annex create failed")
            if "No jobs for" in out:
                # Upstream's wording points at a CLI the user never sees.
                error = (
                    "Nothing is queued for your annex yet. Run a workflow first; "
                    "the annex can be created once its steps are waiting."
                )
            return {"ok": False, "error": error, "output": out}
        (work / CREATING).unlink(missing_ok=True)
        await _prune(root, work, name)
        _cache["data"] = None  # the request job is new
        log.info("annex_bundle_created", name=name, path=str(tar))
        return {"ok": True, "bundle": current_bundle(), "output": out}


async def shutdown() -> dict[str, Any]:
    """Run `htcondor annex shutdown <user>`: tells the annex's nodes to leave
    and retires its requests. Idle nodes leave on their own as well; this is
    for when the user is done and wants the allocation back now."""
    name = annex_name()
    if not _SAFE_NAME.match(name):
        return {"ok": False, "error": f"unusable annex name {name!r}"}
    async with _action_lock:
        rc, out = await _run_text(["htcondor", "annex", "shutdown", name], 60)
        _cache["data"] = None
        if rc != 0:
            log.warning("annex_shutdown_failed", name=name, rc=rc, output=out[-500:])
            return {"ok": False, "error": _last_line(out, "htcondor annex shutdown failed"), "output": out}
        log.info("annex_shutdown", name=name)
        return {"ok": True, "output": out}


async def close_requests() -> dict[str, Any]:
    """Remove this annex's request jobs from the queue.

    A request whose nodes have all left stays in the queue as "granted"
    (Studio counts it as retired), and one that went on hold stays held,
    and neither has a button that ends it: `htcondor annex shutdown` talks
    to nodes, and there are none (#558). The page offers this only when no
    node is up.
    """
    name = annex_name()
    if not _SAFE_NAME.match(name):
        return {"ok": False, "error": f"unusable annex name {name!r}"}
    async with _action_lock:
        rc, out = await _run_text(
            ["condor_rm", "-constraint", f'hpc_annex_name == "{name}"'], 60
        )
        _cache["data"] = None
        # condor_rm exits non-zero when nothing matched; that is done, too.
        if rc != 0 and "matching" not in out.lower():
            log.warning("annex_close_failed", name=name, rc=rc, output=out[-500:])
            return {"ok": False, "error": _last_line(out, "condor_rm failed"), "output": out}
        log.info("annex_requests_closed", name=name)
        return {"ok": True, "output": out}


def _cache_fresh(force: bool) -> bool:
    return (
        not force
        and _cache["data"] is not None
        and (time.monotonic() - _cache["ts"]) < _CACHE_TTL
    )


async def get_status(force: bool = False) -> dict[str, Any]:
    """The user's annex, cached for _CACHE_TTL seconds.

    ``available`` is False, with ``error`` saying why, when annex is not
    enabled on this access point or the queries fail; the page then shows
    the instructions alone.
    """
    if _cache_fresh(force):
        return _cache["data"]
    async with _cache_lock:
        if _cache_fresh(force):
            return _cache["data"]

        name = annex_name()
        status: dict[str, Any] = {"available": False, "name": name, "checked_at": time.time()}
        if not _SAFE_NAME.match(name):
            status["error"] = f"unusable annex name {name!r}"
        else:
            config = await _condor_config()
            if not config["enabled"]:
                status["error"] = "HPC annex is not enabled on this access point"
            elif not config["collector"]:
                status["error"] = "ANNEX_COLLECTOR is not configured"
            else:
                status.update(await _query(name, config["collector"]))
                status["requires_jobs"] = bool(config.get("require_job"))
        status["bundle"] = current_bundle()
        if not status["available"]:
            log.debug("annex_unavailable", name=name, error=status.get("error"))
        _cache["ts"] = time.monotonic()
        _cache["data"] = status
        return status


async def _query(name: str, collector: str) -> dict[str, Any]:
    quoted = f'"{name}"'
    (requests, req_err), (slots, slot_err), (jobs, job_err) = await asyncio.gather(
        _run_json([
            "condor_q", "-json",
            "-constraint", f"hpc_annex_name == {quoted}",
            "-attributes", "JobStatus,HoldReason,hpc_annex_request_id,hpc_annex_name",
        ]),
        _run_json([
            "condor_status", "-json", "-pool", collector,
            "-constraint", f"AnnexName == {quoted}",
            "-attributes",
            "Name,PartitionableSlot,TotalSlotCpus,ChildCpus,"
            "DaemonStartTime,DaemonStopTime,hpc_annex_request_id",
        ]),
        _run_json([
            "condor_q", "-json",
            # The request jobs carry TargetAnnexName too; they are not work.
            "-constraint", f"TargetAnnexName == {quoted} && hpc_annex_name =?= undefined",
            "-attributes", "JobStatus",
        ]),
    )
    # ANNEX_COLLECTOR is a daemon the access point always runs (it answered
    # "0 nodes" for Mats with no annex up), so a failure there is an outage,
    # not an empty annex: reporting zero would turn every granted request
    # into "retired" and tell the user their nodes are gone.
    if requests is None or jobs is None or slots is None:
        return {"available": False, "error": req_err or job_err or slot_err}
    summary = summarize(name, requests, slots, jobs)
    summary["available"] = True
    return summary
