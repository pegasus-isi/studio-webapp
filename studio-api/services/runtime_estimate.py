"""Remaining-time estimate from historical per-transformation runtimes.

V1 heuristic (explainable by design): jobs of the same transformation take
about as long as the ones that already finished in this run. Estimated
remaining wall-clock = sum of predicted durations for unfinished jobs,
divided by the currently observed parallelism.

A PegasusOracle-backed predictor can replace this later behind the same
shape.
"""

from __future__ import annotations

from typing import Any

import structlog

from services.event_reader import jobs as event_jobs
from services.event_reader import read_events

log = structlog.get_logger()


def estimate_remaining(run_dir: str) -> dict[str, Any] | None:
    """Estimate seconds of wall-clock left for a run, or None if unknowable.

    Returns ``{"remaining_seconds", "method", "confidence", "coverage",
    "remaining_jobs", "parallelism"}``. Synchronous (reads event files /
    stampede DB) — call via run_in_executor from async code.
    """
    events = read_events(run_dir)
    if not events:
        return None
    job_list = event_jobs(events)
    if not job_list:
        return None

    # Historical mean duration per transformation, from finished jobs.
    sums: dict[str, float] = {}
    counts: dict[str, int] = {}
    all_durations: list[float] = []
    for j in job_list:
        if j.get("status") != "succeeded":
            continue
        duration = j.get("duration")
        if not isinstance(duration, (int, float)) or duration <= 0:
            continue
        tx = str(j.get("transformation") or j.get("type_desc") or "")
        sums[tx] = sums.get(tx, 0.0) + float(duration)
        counts[tx] = counts.get(tx, 0) + 1
        all_durations.append(float(duration))

    remaining = [
        j for j in job_list if j.get("status") in ("pending", "running")
    ]
    if not remaining:
        return {
            "remaining_seconds": 0,
            "method": "historical-mean",
            "confidence": "high",
            "coverage": 1.0,
            "remaining_jobs": 0,
            "parallelism": 0,
        }
    if not all_durations:
        # Nothing has finished yet — no history to extrapolate from.
        return None

    global_mean = sum(all_durations) / len(all_durations)
    covered = 0
    work = 0.0
    for j in remaining:
        tx = str(j.get("transformation") or j.get("type_desc") or "")
        if counts.get(tx):
            work += sums[tx] / counts[tx]
            covered += 1
        else:
            work += global_mean

    # Running jobs bound the achievable parallelism; when everything is still
    # queued assume serial execution (conservative).
    running = sum(1 for j in remaining if j.get("status") == "running")
    parallelism = max(1, running)
    coverage = covered / len(remaining)
    confidence = (
        "high" if coverage >= 0.8 else "medium" if coverage >= 0.4 else "low"
    )

    return {
        "remaining_seconds": int(work / parallelism),
        "method": "historical-mean",
        "confidence": confidence,
        "coverage": round(coverage, 2),
        "remaining_jobs": len(remaining),
        "parallelism": parallelism,
    }
