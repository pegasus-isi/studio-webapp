"""On-demand hold and idle diagnosis from the native ``pegasus-monitor`` (#662).

Pegasus 6.0 ships ``pegasus-monitor``, whose one-shot modes can print a
versioned JSON document (``--json``). Studio asks it two questions it cannot
answer well itself, for one run, when a user asks:

- why is a job **held**: classified from the scheduler's own view, so it is
  known before the stampede DB records it (a clean hold took 35 s to get
  there in a trial, one whose wrapper traps TERM about 600 s);
- why are jobs **idle**: pool capacity, per-job resource fit, user priority.

This is deliberately not where run status, counts or the job roster come from.
Those are read for every run on every list poll, and a process per call does
not fit there; they stay on the stampede reader.

What the trial that produced ``tests/fixtures/pegasus_monitor`` established,
and this module depends on:

- HTCondor queries must stay on. With ``--no-condor`` a held job is not found
  at all until the DB catches up, and then only as ``held_unknown``.
- For roughly half a minute after ``pegasus-plan`` returns there is no
  authoritative snapshot and the tool exits 1 with
  ``error.code == "no_authoritative_snapshot"``. That is a run starting up,
  not a failure, and is reported as such.
- The document's ``stall`` section is not passed on. Its one-shot assessment
  called a healthy job stalled after 60 s and a dead workflow not stalled.

The tool is absent from every image until the upstream merge reaches the
Pegasus development channel, and an older build has no ``--json``; both read
as "unavailable" and nothing here runs.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import signal
import time
from typing import Any

import structlog

log = structlog.get_logger()

TOOL = "pegasus-monitor"
FORMAT = "pegasus-monitor-json"
# The contract keeps format_version across additive changes and increments it
# for a removed or renamed key, so anything but this is a document whose keys
# cannot be assumed.
FORMAT_VERSION = 1

# Each HTCondor query inside the tool times out after 5 s and there are up to
# five of them; an unreachable schedd therefore costs tens of seconds, not
# forever. Measured healthy cost is under half a second.
_TIMEOUT_S = 45.0
_PROBE_TIMEOUT_S = 15.0
# A button pressed twice, or the page and the chat asking together, should be
# one process. Short enough that "ask again" after releasing a hold is fresh.
_CACHE_TTL_S = 10.0
_CACHE_MAX = 64

_MAX_FINDINGS = 50
_MAX_MISMATCHES = 20
_MAX_LINES = 20
_MAX_TEXT = 600

# Source states that mean "this was looked at and answered" or "this mode does
# not look there". Anything else is a source that could not be read, which a
# reader needs in order to tell "no held jobs" from "could not ask".
_SOURCE_OK = frozenset({"healthy", "not_queried"})

_probe: tuple[tuple[str, float], bool] | None = None
_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_locks: dict[str, asyncio.Lock] = {}


def enabled() -> bool:
    """``STUDIO_NATIVE_MONITOR=0`` turns the feature off without a rebuild."""
    return os.environ.get("STUDIO_NATIVE_MONITOR", "1").strip() != "0"


def _clip(value: Any) -> str:
    text = str(value) if value is not None else ""
    return text if len(text) <= _MAX_TEXT else text[:_MAX_TEXT] + "…"


def _lines(values: Any) -> list[str]:
    return [_clip(v) for v in (values or [])[:_MAX_LINES] if v]


async def _communicate(
    proc: asyncio.subprocess.Process, timeout: float
) -> tuple[bytes, bytes]:
    """``proc.communicate()`` with a timeout that stops the tool *and its children*.

    pegasus-monitor runs condor_q and friends as children that inherit its
    stdout. Killing only the tool leaves such a child holding the pipe open,
    and asyncio does not report the process as finished until every pipe
    closes, so the timeout would return only when the child did. The tool is
    therefore started in its own session and the whole group is killed.
    """
    try:
        return await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, signal.SIGKILL)
        with contextlib.suppress(Exception):
            await proc.wait()
        raise


async def supports_json() -> bool:
    """Whether the installed ``pegasus-monitor`` has ``--json``.

    Asked of the tool's own ``--help`` rather than inferred from a Pegasus
    version: both images track an unpinned development channel where every
    build is ``6.0.0.dev0``. Remembered per binary and mtime, so a tool that
    appears or is upgraded under a running server is noticed.
    """
    global _probe
    path = shutil.which(TOOL)
    if not path:
        return False
    try:
        key = (path, os.stat(path).st_mtime)
    except OSError:
        return False
    if _probe is not None and _probe[0] == key:
        return _probe[1]

    supported = False
    try:
        proc = await asyncio.create_subprocess_exec(
            path,
            "--help",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
        )
        stdout, _ = await _communicate(proc, _PROBE_TIMEOUT_S)
        supported = b"--json" in stdout
    except (OSError, asyncio.TimeoutError) as e:
        log.warning("native_monitor_probe_failed", error=str(e))
        return False  # not remembered: a transient failure should be retried
    _probe = (key, supported)
    log.info("native_monitor_probed", path=path, supports_json=supported)
    return supported


def _unavailable(message: str) -> dict[str, Any]:
    return {"state": "unavailable", "message": message}


def _error(code: str, message: str) -> dict[str, Any]:
    return {"state": "error", "code": code, "message": _clip(message)}


def _hold_reason(finding: dict[str, Any]) -> str | None:
    """The scheduler's own hold text, when the finding carries it."""
    for item in finding.get("evidence") or []:
        if not isinstance(item, dict):
            continue
        payload = item.get("payload") or {}
        if item.get("code") == "hold_reason" and payload.get("reason_excerpt"):
            return _clip(payload["reason_excerpt"])
    return None


def _finding(finding: dict[str, Any]) -> dict[str, Any]:
    target = finding.get("target") or {}
    return {
        "job_id": target.get("exec_job_id"),
        "code": finding.get("code"),
        "severity": finding.get("severity"),
        "summary": _clip(finding.get("summary")),
        "suggestions": _lines(finding.get("suggestions")),
        "hold_reason": _hold_reason(finding),
    }


def _idle(why_idle: dict[str, Any] | None) -> dict[str, Any] | None:
    """The why-idle section, reduced; None when the tool could not produce it."""
    if not isinstance(why_idle, dict) or not why_idle.get("available"):
        return None
    fit = why_idle.get("resource_fit") or {}
    mismatches = [
        {
            "job_id": m.get("exec_job_id"),
            "reason": m.get("reason"),
            "requested_cpus": m.get("requested_cpus"),
            "requested_memory_mb": m.get("requested_memory_mb"),
            "requested_disk_kb": m.get("requested_disk_kb"),
            "requested_gpus": m.get("requested_gpus"),
            # `requirements` is left behind on purpose: it is a raw ClassAd
            # expression, hundreds of characters of no use to a reader.
        }
        for m in (fit.get("mismatches") or [])[:_MAX_MISMATCHES]
        if isinstance(m, dict)
    ]
    pool = why_idle.get("pool") or {}
    # Every pool number is null when the pool was not queried; that is "not
    # known", and must not be shown as an empty pool.
    pool_known = any(v is not None for v in pool.values())
    return {
        "queued_jobs": why_idle.get("queued_jobs"),
        "unsubmitted_jobs": why_idle.get("unsubmitted_jobs"),
        "findings": _lines(why_idle.get("findings")),
        "suggestions": _lines(why_idle.get("suggestions")),
        "mismatches": mismatches,
        "pool": pool if pool_known else None,
        "priority": why_idle.get("priority"),
    }


def reduce(doc: Any) -> dict[str, Any]:
    """Turn one ``pegasus-monitor --json`` document into what Studio shows.

    Pure, so it is tested against real documents. Never raises on a document
    it does not understand; it says so instead.
    """
    if not isinstance(doc, dict) or doc.get("format") != FORMAT:
        return _error(
            "not_a_monitor_document", "pegasus-monitor did not return its JSON document"
        )
    version = doc.get("format_version")
    if version != FORMAT_VERSION:
        return _unavailable(
            f"pegasus-monitor JSON format {version!r} is not one this Studio reads"
            f" (expects {FORMAT_VERSION})"
        )

    error = doc.get("error")
    if isinstance(error, dict):
        code = str(error.get("code") or "monitor_error")
        if code == "no_authoritative_snapshot":
            return {
                "state": "starting",
                "message": (
                    "The run has not recorded any workflow state yet. This is"
                    " normal for the first half minute or so after it is"
                    " submitted; ask again shortly."
                ),
            }
        return _error(code, str(error.get("message") or code))

    summary = doc.get("summary") or {}
    diagnostics = doc.get("diagnostics") or {}
    findings = [f for f in (diagnostics.get("findings") or []) if isinstance(f, dict)]
    held = [_finding(f) for f in findings if f.get("category") == "held"]
    failed = [_finding(f) for f in findings if f.get("category") == "failed"]

    not_checked = [
        {
            "source": s.get("source"),
            "state": s.get("state"),
            "reason": s.get("error_code"),
        }
        for s in (doc.get("sources") or [])
        if isinstance(s, dict) and s.get("state") not in _SOURCE_OK
    ]

    return {
        "state": "ok",
        "generated_at": doc.get("generated_at"),
        "workflow_state": summary.get("workflow_state"),
        "counts": summary.get("job_counts") or {},
        "held": held[:_MAX_FINDINGS],
        "failed": failed[:_MAX_FINDINGS],
        "findings_omitted": max(0, len(held) - _MAX_FINDINGS)
        + max(0, len(failed) - _MAX_FINDINGS),
        # False means the analysis did not run, not that it found nothing.
        "diagnosis_available": bool(diagnostics.get("available")),
        "idle": _idle(doc.get("why_idle")),
        "not_checked": not_checked,
    }


async def _run(path: str, run_dir: str) -> dict[str, Any]:
    try:
        proc = await asyncio.create_subprocess_exec(
            path,
            "--json",
            "--diagnose",
            "--why-idle",
            run_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            # A terminal on stdin makes the tool's non-JSON modes interactive;
            # never let it find one.
            stdin=asyncio.subprocess.DEVNULL,
            start_new_session=True,
        )
        stdout, stderr = await _communicate(proc, _TIMEOUT_S)
    except asyncio.TimeoutError:
        return _error(
            "timeout", f"pegasus-monitor did not answer within {int(_TIMEOUT_S)} s"
        )
    except OSError as e:
        return _error("not_runnable", str(e))

    # The tool writes a document on failure too (exit 1, error filled in), so
    # the exit code is not consulted before parsing.
    try:
        doc = json.loads(stdout.decode("utf-8", errors="replace"))
    except ValueError:
        detail = stderr.decode("utf-8", errors="replace").strip() or "no output"
        log.warning("native_monitor_bad_output", run_dir=run_dir, rc=proc.returncode)
        return _error("bad_output", detail)
    return reduce(doc)


def _prune(now: float) -> None:
    for key in [k for k, (t, _) in _cache.items() if now - t > _CACHE_TTL_S]:
        _cache.pop(key, None)
        _locks.pop(key, None)
    while len(_cache) > _CACHE_MAX:
        oldest = min(_cache, key=lambda k: _cache[k][0])
        _cache.pop(oldest, None)
        _locks.pop(oldest, None)


async def explain(run_dir: str) -> dict[str, Any]:
    """Why this run's jobs are held or idle. Always answers; ``state`` says how.

    ``state`` is one of ``ok``, ``starting`` (no workflow state recorded yet),
    ``unavailable`` (no usable ``pegasus-monitor`` here) or ``error``.
    """
    if not enabled():
        return _unavailable(
            "Native monitoring is turned off (STUDIO_NATIVE_MONITOR=0)."
        )
    if not await supports_json():
        return _unavailable(
            "This Studio's Pegasus has no pegasus-monitor with --json output."
        )
    path = shutil.which(TOOL)
    if not path:
        return _unavailable("pegasus-monitor is not installed.")

    now = time.monotonic()
    _prune(now)
    hit = _cache.get(run_dir)
    if hit is not None and now - hit[0] <= _CACHE_TTL_S:
        return hit[1]

    lock = _locks.setdefault(run_dir, asyncio.Lock())
    async with lock:
        hit = _cache.get(run_dir)
        if hit is not None and time.monotonic() - hit[0] <= _CACHE_TTL_S:
            return hit[1]
        result = await _run(path, run_dir)
        _cache[run_dir] = (time.monotonic(), result)
        return result


def reset_for_tests() -> None:
    global _probe
    _probe = None
    _cache.clear()
    _locks.clear()
