"""Notification engine — the event loop and pub/sub hub for studio alerts.

Two halves:

* **Emit / subscribe** — :meth:`NotificationEngine.notify` persists a
  notification and fans it out to every live subscriber (used by the SSE
  endpoint). Any backend code can call ``engine.notify(...)`` to raise an
  alert; this is the generic hook future features plug into.
* **Watch loop** — a background task started at app startup that periodically
  scans workflow runs, diffs their state against the last observation, and
  emits notifications on meaningful transitions (state change, new job
  failures) plus a resource-pressure extension point.

The engine is a process-wide singleton (``engine``); ``start(db)`` is called
from the FastAPI lifespan.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import structlog

from services.workflow_monitor import get_workflow_detail
from services.workflow_scanner import discover_runs, get_run_status

if TYPE_CHECKING:
    from db import Database

log = structlog.get_logger()

# How often the watch loop scans runs for state changes (seconds).
_POLL_INTERVAL = 10

# Terminal statuses — once a run reaches one of these we stop re-notifying.
_TERMINAL = {"succeeded", "failed"}

_SEVERITY_FOR_STATUS = {
    "succeeded": "success",
    "failed": "error",
    "running": "info",
    "planning": "info",
    "planned": "info",
}


class _RunState:
    """Last-observed snapshot of a single run, used to detect transitions."""

    __slots__ = ("status", "failed_jobs", "total_jobs")

    def __init__(self, status: str, failed_jobs: int, total_jobs: int) -> None:
        self.status = status
        self.failed_jobs = failed_jobs
        self.total_jobs = total_jobs


class NotificationEngine:
    """Pub/sub hub plus the background workflow-watching loop."""

    def __init__(self) -> None:
        self._db: Database | None = None
        self._workspace_root: str = ""
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._task: asyncio.Task[None] | None = None
        # run_dir -> last observed state
        self._seen: dict[str, _RunState] = {}
        # Notification kinds the user has muted (suppressed entirely).
        self._muted: set[str] = set()

    _MUTED_SETTING_KEY = "muted_notification_kinds"

    # --- lifecycle ---

    def start(self, db: Database, workspace_root: str) -> None:
        """Begin watching workflows. Idempotent."""
        self._db = db
        self._workspace_root = workspace_root
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._watch_loop())
            log.info("notification_engine_started", interval=_POLL_INTERVAL)

    async def refresh_prefs(self) -> None:
        """Reload muted-kind preferences from the DB into the cache."""
        if self._db is None:
            return
        muted = await self._db.get_setting(self._MUTED_SETTING_KEY, [])
        if isinstance(muted, list):
            self._muted = {str(k) for k in muted}

    def muted_kinds(self) -> list[str]:
        return sorted(self._muted)

    async def set_muted_kinds(self, kinds: list[str]) -> list[str]:
        """Persist and apply the set of muted notification kinds."""
        self._muted = {str(k) for k in kinds}
        if self._db is not None:
            await self._db.set_setting(
                self._MUTED_SETTING_KEY, sorted(self._muted)
            )
        return self.muted_kinds()

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

    # --- pub/sub ---

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        """Register a live subscriber. Caller must :meth:`unsubscribe` when done."""
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=100)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(q)

    async def notify(
        self,
        kind: str,
        title: str,
        body: str | None = None,
        severity: str = "info",
        run_id: str | None = None,
        link: str | None = None,
        dedup_key: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Persist a notification and broadcast it to live subscribers.

        This is the public hook — call it from anywhere in the backend to raise
        an alert. Returns the stored notification (or ``None`` before startup).
        """
        if self._db is None:
            log.warning("notify_before_start", title=title)
            return None
        # Muted categories are recorded as already-read: they still land in
        # history (and stream live to the bell), but raise no toast and don't
        # bump the unread badge.
        muted = kind in self._muted
        notification = await self._db.add_notification(
            kind=kind,
            title=title,
            body=body,
            severity=severity,
            run_id=run_id,
            link=link,
            dedup_key=dedup_key,
            meta=meta,
            read=muted,
        )
        self._broadcast(notification)
        return notification

    def _broadcast(self, notification: dict[str, Any]) -> None:
        for q in list(self._subscribers):
            try:
                q.put_nowait(notification)
            except asyncio.QueueFull:
                # Slow consumer — drop the live push; it still lands in history.
                log.debug("notification_subscriber_full")

    # --- watch loop ---

    async def _watch_loop(self) -> None:
        # Seed current state without notifying, so we don't fire alerts for
        # every pre-existing run the first time the loop runs.
        try:
            await self._scan(seed_only=True)
        except Exception as e:  # pragma: no cover - defensive
            log.warning("notification_seed_failed", error=str(e))

        while True:
            try:
                await asyncio.sleep(_POLL_INTERVAL)
                await self._scan(seed_only=False)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # pragma: no cover - keep the loop alive
                log.warning("notification_scan_failed", error=str(e))

    async def _scan(self, seed_only: bool) -> None:
        runs = discover_runs(self._workspace_root)
        for run in runs:
            run_dir = run["run_dir"]
            run_id = run["run_id"]
            label = run.get("run_name") or run["name"] or run_id

            detail = await get_workflow_detail(run_dir)
            status = detail.get("status") or get_run_status(run_dir)
            failed = int(detail.get("failed_jobs", 0) or 0)
            total = int(detail.get("total_jobs", 0) or 0)

            prev = self._seen.get(run_dir)
            self._seen[run_dir] = _RunState(status, failed, total)

            if seed_only or prev is None:
                # First sighting — record baseline, don't alert.
                continue

            await self._diff(run_id, label, prev, status, failed, total)

            # Resource-pressure extension point (see _check_resources docstring).
            await self._check_resources(run_id, label, detail)

    async def _diff(
        self,
        run_id: str,
        label: str,
        prev: _RunState,
        status: str,
        failed: int,
        total: int,
    ) -> None:
        link = f"/workflows?run={run_id}"

        # 1. Status transition.
        if status != prev.status:
            severity = _SEVERITY_FOR_STATUS.get(status, "info")
            if status == "succeeded":
                title = f"Workflow “{label}” completed"
                body = f"All jobs finished successfully ({total} jobs)."
            elif status == "failed":
                title = f"Workflow “{label}” failed"
                body = (
                    f"{failed} job(s) failed."
                    if failed
                    else "The workflow ended in a failed state."
                )
            else:
                title = f"Workflow “{label}” is now {status}"
                body = None
            await self.notify(
                kind="workflow_state",
                title=title,
                body=body,
                severity=severity,
                run_id=run_id,
                link=link,
                # One state-transition notification per (run, status).
                dedup_key=f"state:{run_id}:{status}",
            )

        # 2. New job failures (independent of overall status, so problems
        #    surface before the whole run flips to failed).
        if failed > prev.failed_jobs:
            new_failures = failed - prev.failed_jobs
            await self.notify(
                kind="job_failure",
                title=f"Job failure in “{label}”",
                body=f"{new_failures} job(s) just failed ({failed} total).",
                severity="warning",
                run_id=run_id,
                link=link,
                # Re-fire as the failure count climbs.
                dedup_key=f"jobfail:{run_id}:{failed}",
            )

    async def _check_resources(
        self, run_id: str, label: str, detail: dict[str, Any]
    ) -> None:
        """Resource-provisioning extension point.

        Intended to flag when a run needs more capacity — e.g. jobs sitting
        idle/held in HTCondor with no slots to run them. The studio backend
        does not yet expose per-run HTCondor idle/held counts (the
        workflow-monitor `--why-idle` data is the natural source), so this is a
        deliberate stub: wire that signal in here and call ``self.notify(
        kind="resource", severity="warning", ...)`` with a
        ``dedup_key`` such as ``f"resource:{run_id}:idle"``.
        """
        return


# Process-wide singleton.
engine = NotificationEngine()
