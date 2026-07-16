"""HTCondor pool inventory (supply) and queue demand snapshots.

Feeds the Provisioning page, the dashboard resources panel, and the
notification engine's resource-pressure checks. One cached snapshot serves
them all: ``get_status()`` runs ``condor_status -json`` / ``condor_q -json``
at most once per TTL window, so per-run callers (the 10s notification scan)
don't hammer the schedd/collector.

Degrades gracefully when HTCondor is absent: every summary carries an
``available`` flag and the reason, so the UI can render a "no pool" state
instead of erroring (groundwork for the Condor-optional deployment mode).

Pool grouping heuristic (read-only v1, per the provisioning plan):

* ``annex``   — slots carrying an ``AnnexName`` ClassAd (HTCondor Annex
  provisioned capacity).
* ``glidein`` — slots carrying ``GLIDEIN_*`` attributes (OSPool-style
  glideins).
* ``base``    — everything else (the local/static pool).
"""

from __future__ import annotations

import asyncio
import getpass
import json
import os
import time
from typing import Any

import structlog

log = structlog.get_logger()

_CMD_TIMEOUT = 20

# One condor_status/condor_q round at most per TTL seconds.
_CACHE_TTL = float(os.environ.get("STUDIO_POOL_CACHE_TTL", "15"))

# An idle job waiting longer than this (seconds) counts as resource pressure.
WAIT_THRESHOLD = int(os.environ.get("STUDIO_RESOURCE_WAIT_SECONDS", "300"))

# OSPool (OSG) central manager. condor_status -pool <cm> pulls its slot
# inventory. It's a large, remote pool, so it gets a longer timeout and its
# own longer cache. Set STUDIO_OSPOOL_CM="" to disable the OSPool panel.
OSPOOL_CM = os.environ.get("STUDIO_OSPOOL_CM", "cm-1.ospool.osg-htc.org")
_OSPOOL_CMD_TIMEOUT = int(os.environ.get("STUDIO_OSPOOL_CMD_TIMEOUT", "45"))
_OSPOOL_CACHE_TTL = float(os.environ.get("STUDIO_OSPOOL_CACHE_TTL", "120"))

# Only the attributes summarize_slots() reads. OSPool is ~38k slots: fetching
# full ClassAds never completes (times out with an empty result), but this
# projection returns in ~2s. `name` backs the machine count; a `glidein_*`
# attribute is needed so _slot_group() can tag glidein slots.
_SLOT_ATTRS = [
    "SlotType", "State", "Cpus", "GPUs", "Memory",
    "TotalSlotCpus", "TotalSlotGPUs", "TotalSlotMemory",
    "Machine", "Name", "AnnexName", "GLIDEIN_Site",
]

_POOL_LABELS = {
    "base": "Base Condor pool",
    "annex": "Provisioned (Annex)",
    "glidein": "OS pool / glideins",
}

# JobStatus codes from the HTCondor job ClassAd.
_JOB_IDLE = 1
_JOB_RUNNING = 2
_JOB_HELD = 5


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _lower_keys(ad: dict[str, Any]) -> dict[str, Any]:
    """ClassAd attribute names are case-insensitive; normalize for lookups."""
    return {str(k).lower(): v for k, v in ad.items()}


async def _run_json(
    cmd: list[str], timeout: float = _CMD_TIMEOUT
) -> tuple[list[dict[str, Any]] | None, str | None]:
    """Run a condor CLI command with -json output. Returns (ads, error)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
    except FileNotFoundError:
        return None, f"{cmd[0]} not found"
    except asyncio.TimeoutError:
        return None, f"{cmd[0]} timed out"
    except OSError as e:
        return None, f"{cmd[0]} failed: {e}"

    if proc.returncode != 0:
        err = stderr.decode("utf-8", errors="replace").strip()
        return None, err.splitlines()[0][:200] if err else f"{cmd[0]} failed"

    text = stdout.decode("utf-8", errors="replace").strip()
    if not text:
        # condor_status/condor_q print nothing at all for an empty result.
        return [], None
    try:
        ads = json.loads(text)
    except json.JSONDecodeError as e:
        return None, f"unparseable {cmd[0]} output: {e}"
    if not isinstance(ads, list):
        return None, f"unexpected {cmd[0]} output shape"
    return [a for a in ads if isinstance(a, dict)], None


def _slot_group(ad: dict[str, Any]) -> str:
    if ad.get("annexname"):
        return "annex"
    if any(k.startswith("glidein") for k in ad):
        return "glidein"
    return "base"


def _empty_bucket() -> dict[str, Any]:
    return {
        "total_cpus": 0,
        "idle_cpus": 0,
        "total_gpus": 0,
        "idle_gpus": 0,
        "total_memory_mb": 0,
        "idle_memory_mb": 0,
        "machines": set(),
        "slots": 0,
    }


def summarize_slots(ads: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate slot ads into pool totals plus per-group buckets.

    Partitionable slots contribute TotalSlot* as capacity and their remaining
    (unclaimed) resources as idle; their dynamic children are skipped so the
    claimed share isn't double counted. Static slots count whole and are idle
    only while Unclaimed.
    """
    total = _empty_bucket()
    groups: dict[str, dict[str, Any]] = {}

    for raw in ads:
        ad = _lower_keys(raw)
        slot_type = str(ad.get("slottype", "Static")).lower()
        if slot_type == "dynamic":
            continue

        state = str(ad.get("state", "")).lower()
        cpus = _as_int(ad.get("cpus"))
        gpus = _as_int(ad.get("gpus"))
        memory = _as_int(ad.get("memory"))

        if slot_type == "partitionable":
            total_cpus = _as_int(ad.get("totalslotcpus")) or cpus
            total_gpus = _as_int(ad.get("totalslotgpus")) or gpus
            total_memory = _as_int(ad.get("totalslotmemory")) or memory
            idle_cpus, idle_gpus, idle_memory = cpus, gpus, memory
        else:
            total_cpus, total_gpus, total_memory = cpus, gpus, memory
            unclaimed = state == "unclaimed"
            idle_cpus = cpus if unclaimed else 0
            idle_gpus = gpus if unclaimed else 0
            idle_memory = memory if unclaimed else 0

        bucket = groups.setdefault(_slot_group(ad), _empty_bucket())
        for b in (total, bucket):
            b["total_cpus"] += total_cpus
            b["idle_cpus"] += idle_cpus
            b["total_gpus"] += total_gpus
            b["idle_gpus"] += idle_gpus
            b["total_memory_mb"] += total_memory
            b["idle_memory_mb"] += idle_memory
            b["slots"] += 1
            machine = ad.get("machine") or ad.get("name")
            if machine:
                b["machines"].add(str(machine))

    def _finish(bucket: dict[str, Any]) -> dict[str, Any]:
        bucket["machines"] = len(bucket["machines"])
        return bucket

    return {
        "available": True,
        **_finish(total),
        "pools": {
            key: {"label": _POOL_LABELS.get(key, key), **_finish(bucket)}
            for key, bucket in sorted(groups.items())
        },
    }


def summarize_queue(
    ads: list[dict[str, Any]], now: float | None = None
) -> dict[str, Any]:
    """Aggregate job ads into demand totals plus a per-workflow breakdown.

    Pegasus stamps every job it submits with ``pegasus_wf_uuid`` /
    ``pegasus_wf_name`` ClassAds, so demand can be attributed to studio runs.
    """
    now = now if now is not None else time.time()
    demand: dict[str, Any] = {
        "available": True,
        "total_jobs": 0,
        "idle_jobs": 0,
        "running_jobs": 0,
        "held_jobs": 0,
        "idle_cpus_requested": 0,
        "idle_gpus_requested": 0,
        "idle_memory_mb_requested": 0,
        "oldest_idle_seconds": None,
        "held_reasons": [],
        "by_workflow": {},
    }
    held_reasons: list[str] = []

    def _wf_bucket(ad: dict[str, Any]) -> dict[str, Any] | None:
        wf_uuid = ad.get("pegasus_wf_uuid")
        if not wf_uuid:
            return None
        return demand["by_workflow"].setdefault(
            str(wf_uuid),
            {
                "name": ad.get("pegasus_wf_name"),
                "idle_jobs": 0,
                "running_jobs": 0,
                "held_jobs": 0,
                "idle_cpus_requested": 0,
                "idle_gpus_requested": 0,
                "idle_memory_mb_requested": 0,
                "oldest_idle_seconds": None,
                "held_reason": None,
            },
        )

    for raw in ads:
        ad = _lower_keys(raw)
        status = _as_int(ad.get("jobstatus"))
        demand["total_jobs"] += 1
        wf = _wf_bucket(ad)

        if status == _JOB_RUNNING:
            demand["running_jobs"] += 1
            if wf:
                wf["running_jobs"] += 1
        elif status == _JOB_HELD:
            demand["held_jobs"] += 1
            reason = str(ad.get("holdreason") or "").strip()
            if reason:
                held_reasons.append(reason[:200])
            if wf:
                wf["held_jobs"] += 1
                if reason and not wf["held_reason"]:
                    wf["held_reason"] = reason[:200]
        elif status == _JOB_IDLE:
            demand["idle_jobs"] += 1
            cpus = _as_int(ad.get("requestcpus")) or 1
            gpus = _as_int(ad.get("requestgpus"))
            memory = _as_int(ad.get("requestmemory"))
            demand["idle_cpus_requested"] += cpus
            demand["idle_gpus_requested"] += gpus
            demand["idle_memory_mb_requested"] += memory

            wait = None
            qdate = _as_int(ad.get("qdate"))
            if qdate:
                wait = max(0, int(now - qdate))
                if (
                    demand["oldest_idle_seconds"] is None
                    or wait > demand["oldest_idle_seconds"]
                ):
                    demand["oldest_idle_seconds"] = wait
            if wf:
                wf["idle_jobs"] += 1
                wf["idle_cpus_requested"] += cpus
                wf["idle_gpus_requested"] += gpus
                wf["idle_memory_mb_requested"] += memory
                if wait is not None and (
                    wf["oldest_idle_seconds"] is None
                    or wait > wf["oldest_idle_seconds"]
                ):
                    wf["oldest_idle_seconds"] = wait

    # A few distinct hold reasons are enough to explain the pattern.
    seen: set[str] = set()
    for r in held_reasons:
        if r not in seen:
            seen.add(r)
            demand["held_reasons"].append(r)
        if len(demand["held_reasons"]) >= 3:
            break

    return demand


def evaluate_pressure(
    pool: dict[str, Any], demand: dict[str, Any]
) -> list[dict[str, Any]]:
    """Explainable pool-wide pressure findings (deck slide 12: Observe/Decide).

    Threshold-based only — no automation. Each finding carries the numbers
    that justify it so the notification/UI can show *why*.
    """
    findings: list[dict[str, Any]] = []
    if not pool.get("available") or not demand.get("available"):
        return findings

    idle_jobs = demand["idle_jobs"]
    if idle_jobs and demand["idle_gpus_requested"] > pool["idle_gpus"]:
        gap = demand["idle_gpus_requested"] - pool["idle_gpus"]
        findings.append({
            "kind": "gpu_shortage",
            "severity": "warning",
            "title": f"{gap} more GPU(s) needed",
            "detail": (
                f"Idle jobs request {demand['idle_gpus_requested']} GPU(s) "
                f"but only {pool['idle_gpus']} are free."
            ),
            "delta": {"gpus": gap},
        })
    if idle_jobs and demand["idle_cpus_requested"] > pool["idle_cpus"]:
        gap = demand["idle_cpus_requested"] - pool["idle_cpus"]
        findings.append({
            "kind": "cpu_shortage",
            "severity": "warning",
            "title": f"{gap} more CPU(s) needed",
            "detail": (
                f"Idle jobs request {demand['idle_cpus_requested']} CPU(s) "
                f"but only {pool['idle_cpus']} are free."
            ),
            "delta": {"cpus": gap},
        })
    oldest = demand.get("oldest_idle_seconds")
    if oldest is not None and oldest > WAIT_THRESHOLD:
        findings.append({
            "kind": "long_wait",
            "severity": "warning",
            "title": f"Jobs waiting {oldest // 60} min for resources",
            "detail": (
                f"{idle_jobs} idle job(s); the oldest has waited "
                f"{oldest // 60} minutes (threshold {WAIT_THRESHOLD // 60} min)."
            ),
            "delta": {},
        })
    if demand["held_jobs"]:
        reasons = "; ".join(demand.get("held_reasons") or []) or "unknown reason"
        findings.append({
            "kind": "held_jobs",
            "severity": "warning",
            "title": f"{demand['held_jobs']} held job(s)",
            "detail": f"Hold reason(s): {reasons}",
            "delta": {},
        })
    return findings


def run_pressure(
    wf_demand: dict[str, Any] | None, pool: dict[str, Any]
) -> dict[str, Any] | None:
    """The single most actionable pressure finding for one workflow's demand.

    Priority: GPU gap > CPU gap > long queue wait > held jobs — the scarcer
    the resource, the more a provisioning action would help.
    """
    if not wf_demand or not pool.get("available"):
        return None

    if wf_demand["idle_jobs"]:
        gpus_needed = wf_demand["idle_gpus_requested"]
        if gpus_needed > pool["idle_gpus"]:
            gap = gpus_needed - pool["idle_gpus"]
            return {
                "kind": "gpu_shortage",
                "body": (
                    f"{wf_demand['idle_jobs']} job(s) idle requesting "
                    f"{gpus_needed} GPU(s); pool has {pool['idle_gpus']} free. "
                    f"Add {gap} GPU(s) to unblock."
                ),
                "delta": {"gpus": gap},
            }
        cpus_needed = wf_demand["idle_cpus_requested"]
        if cpus_needed > pool["idle_cpus"]:
            gap = cpus_needed - pool["idle_cpus"]
            return {
                "kind": "cpu_shortage",
                "body": (
                    f"{wf_demand['idle_jobs']} job(s) idle requesting "
                    f"{cpus_needed} CPU(s); pool has {pool['idle_cpus']} free. "
                    f"Add {gap} CPU(s) to unblock."
                ),
                "delta": {"cpus": gap},
            }
        oldest = wf_demand.get("oldest_idle_seconds")
        if oldest is not None and oldest > WAIT_THRESHOLD:
            return {
                "kind": "long_wait",
                "body": (
                    f"{wf_demand['idle_jobs']} job(s) idle; the oldest has "
                    f"waited {oldest // 60} minutes for matching resources."
                ),
                "delta": {},
            }
    if wf_demand["held_jobs"]:
        reason = wf_demand.get("held_reason") or "unknown reason"
        return {
            "kind": "held_jobs",
            "body": (
                f"{wf_demand['held_jobs']} job(s) held. Reason: {reason}"
            ),
            "delta": {},
        }
    return None


# --- cached combined snapshot ---

_cache: dict[str, Any] = {"ts": 0.0, "data": None}
_cache_lock = asyncio.Lock()

_ospool_cache: dict[str, Any] = {"ts": 0.0, "data": None}
_ospool_lock = asyncio.Lock()


def _ospool_owner() -> str:
    """The RemoteOwner to match for 'my' OSPool slots.

    OSPool is global; on a per-user Resources page we only want the slots running
    *this* user's jobs, plus available capacity — never other users' tasks.
    Defaults to the login user; override with STUDIO_OSPOOL_OWNER if the pool
    maps jobs to a different accounting identity.
    """
    return (
        os.environ.get("STUDIO_OSPOOL_OWNER")
        or os.environ.get("USER")
        or getpass.getuser()
    )


def _sum_claimed(ads: list[dict[str, Any]]) -> dict[str, Any]:
    """Sum the resources of claimed slots (my usage). Partitionable parents are
    skipped — a claim lives on the dynamic/static child slot."""
    cpus = gpus = memory = slots = 0
    machines: set[str] = set()
    for raw in ads:
        ad = _lower_keys(raw)
        if str(ad.get("slottype", "")).lower() == "partitionable":
            continue
        cpus += _as_int(ad.get("cpus"))
        gpus += _as_int(ad.get("gpus"))
        memory += _as_int(ad.get("memory"))
        slots += 1
        machine = ad.get("machine") or ad.get("name")
        if machine:
            machines.add(str(machine))
    return {
        "cpus": cpus, "gpus": gpus, "memory_mb": memory,
        "slots": slots, "machines": len(machines),
    }


async def get_ospool_status(force: bool = False) -> dict[str, Any]:
    """User-scoped OSPool view via ``condor_status -pool OSPOOL_CM``.

    OSPool is global (~38k slots, mostly other users' jobs), so we return only
    what's relevant to this user: capacity currently available (idle/unclaimed)
    and the slots running this user's own jobs. Two constrained, projected
    queries — never the full pool, which times out. Cached longer than the local
    pool. Returns ``{"available": False, ...}`` when disabled or unreachable.
    """
    if not OSPOOL_CM:
        return {"available": False, "error": "OSPool disabled", "cm": None}
    async with _ospool_lock:
        age = time.monotonic() - _ospool_cache["ts"]
        if not force and _ospool_cache["data"] is not None and age < _OSPOOL_CACHE_TTL:
            return _ospool_cache["data"]

        attrs = ",".join(_SLOT_ATTRS)
        # Available capacity — idle/unclaimed slots only (excludes others' work).
        avail_ads, err = await _run_json(
            [
                "condor_status", "-pool", OSPOOL_CM, "-json",
                "-attributes", attrs, "-constraint", 'State=="Unclaimed"',
            ],
            timeout=_OSPOOL_CMD_TIMEOUT,
        )
        if avail_ads is None:
            data: dict[str, Any] = {"available": False, "error": err, "cm": OSPOOL_CM}
        else:
            avail = summarize_slots(avail_ads)
            owner = _ospool_owner()
            # Slots claimed by this user's jobs.
            mine_ads, _mine_err = await _run_json(
                [
                    "condor_status", "-pool", OSPOOL_CM, "-json",
                    "-attributes", attrs + ",RemoteOwner",
                    "-constraint", f'regexp("{owner}", RemoteOwner)',
                ],
                timeout=_OSPOOL_CMD_TIMEOUT,
            )
            data = {
                "available": True,
                "cm": OSPOOL_CM,
                "owner": owner,
                "avail_cpus": avail["idle_cpus"],
                "avail_gpus": avail["idle_gpus"],
                "avail_memory_mb": avail["idle_memory_mb"],
                "avail_slots": avail["slots"],
                "mine": _sum_claimed(mine_ads or []),
            }
        _ospool_cache["ts"] = time.monotonic()
        _ospool_cache["data"] = data
        return data


async def get_status(force: bool = False) -> dict[str, Any]:
    """Combined supply/demand/pressure snapshot, cached for _CACHE_TTL seconds."""
    async with _cache_lock:
        age = time.monotonic() - _cache["ts"]
        if not force and _cache["data"] is not None and age < _CACHE_TTL:
            return _cache["data"]

        slot_ads, slot_err = await _run_json(["condor_status", "-json"])
        job_ads, job_err = await _run_json(["condor_q", "-json"])

        if slot_ads is None:
            pool: dict[str, Any] = {"available": False, "error": slot_err}
        else:
            pool = summarize_slots(slot_ads)
        if job_ads is None:
            demand: dict[str, Any] = {"available": False, "error": job_err}
        else:
            demand = summarize_queue(job_ads)

        # OSPool inventory rides its own (longer) cache so the fast local
        # snapshot isn't gated on the slow remote query.
        ospool = await get_ospool_status()

        status = {
            "available": pool.get("available", False),
            "pool": pool,
            "demand": demand,
            "pressure": evaluate_pressure(pool, demand),
            "ospool": ospool,
            "checked_at": time.time(),
        }
        if slot_err or job_err:
            log.debug("condor_pool_unavailable", slots=slot_err, jobs=job_err)
        _cache["ts"] = time.monotonic()
        _cache["data"] = status
        return status
