"""Chat reply ratings, out to the cluster's telemetry.

A 👍/👎 is stored in the user's own studio.db (`message_feedback`), which is
the record. Nothing outside the pod saw it, so ratings were invisible next to
the token counts in Grafana and the traces in Langfuse. This sends them where
the rest of the chat telemetry already goes: the OTLP collector named by
`STUDIO_OTLP_ENDPOINT`, which pods reach without a credential.

Two signals, governed like the engine's own:

* **A gauge, always (when a collector exists):** `studio.chat.reply_ratings`,
  the current number of rated replies by rating, model and agent, read from
  the DB and re-sent every minute. A count, not text, like the engine's
  token metrics. Re-sent because the collector's Prometheus exporter drops a
  series it hasn't heard from in five minutes, and because the DB is what
  survives a pod restart: an in-process counter would start again at zero.
  A gauge rather than a counter because a changed or cleared vote makes the
  number go down. Grafana reads it with `max_over_time`, so a stopped
  server's last count still counts.

* **One span per vote, only for users who agreed to share chat text:**
  Langfuse has no way to create a score over OTLP (its score API needs the
  project keys, which by design are never in a pod). It does map
  `session.id`, and the engine's spans carry OpenCode's session id there, so
  a span with the same `session.id` lands in the same Langfuse session,
  beside the turns it rates. It holds no text either, but Langfuse only holds
  data for users who opted in, so these follow the same rule as traces.

Failures are logged and dropped: telemetry must never fail a rating.
"""

from __future__ import annotations

import asyncio
import os
import re
import secrets
import time
from typing import Any

import structlog

log = structlog.get_logger()

METRIC = "studio.chat.reply_ratings"
SPAN = "chat.reply_rating"
INTERVAL_SECONDS = 60
_TIMEOUT = 5
_ATTR_SAFE = re.compile(r"[^A-Za-z0-9._@-]")


def endpoint() -> str:
    return (os.environ.get("STUDIO_OTLP_ENDPOINT") or "").strip().rstrip("/")


def _user() -> str:
    raw = (
        os.environ.get("JUPYTERHUB_USER")
        or os.environ.get("USER")
        or os.environ.get("LOGNAME")
        or ""
    )
    return _ATTR_SAFE.sub("_", raw.strip())


def _kv(key: str, value: Any) -> dict[str, Any]:
    if isinstance(value, bool):
        return {"key": key, "value": {"boolValue": value}}
    if isinstance(value, int):
        return {"key": key, "value": {"intValue": str(value)}}
    return {"key": key, "value": {"stringValue": str(value)}}


def _resource(content_capture: bool) -> dict[str, Any]:
    """The same resource attributes the engine's plugin sends, so the
    rating series join the engine's on `enduser_id` and the collector's
    redaction rule reads `studio.content_capture` the same way."""
    attrs = [
        _kv("service.name", "studio-api"),
        _kv("service.namespace", "studio"),
        _kv("deployment.environment", os.environ.get("STUDIO_DEPLOYMENT_ENV") or "unknown"),
        _kv("studio.content_capture", "true" if content_capture else "false"),
    ]
    user = _user()
    if user:
        attrs.append(_kv("enduser.id", user))
    return {"attributes": attrs}


def _label(value: str | None) -> str:
    return (value or "unknown")[:128]


def metrics_payload(counts: list[dict[str, Any]], now_ns: int) -> dict[str, Any]:
    """OTLP/JSON for the rating gauge. Every (rating, model, agent) the user
    has ever rated with appears; the two ratings always do, so an empty
    dashboard says "no ratings" rather than "no data"."""
    points: dict[tuple[str, str, str], int] = {}
    for row in counts:
        key = (
            "up" if row["rating"] > 0 else "down",
            _label(row.get("model")),
            _label(row.get("agent")),
        )
        points[key] = points.get(key, 0) + int(row["n"])
    for rating in ("up", "down"):
        if not any(k[0] == rating for k in points):
            points[(rating, "unknown", "unknown")] = 0
    data_points = [
        {
            "attributes": [_kv("rating", r), _kv("model", m), _kv("agent", a)],
            "timeUnixNano": str(now_ns),
            "asInt": str(n),
        }
        for (r, m, a), n in sorted(points.items())
    ]
    return {
        "resourceMetrics": [{
            "resource": _resource(False),
            "scopeMetrics": [{
                "scope": {"name": "studio-api"},
                "metrics": [{
                    "name": METRIC,
                    "description": "Rated chat replies in this workspace, by rating, model and agent.",
                    "gauge": {"dataPoints": data_points},
                }],
            }],
        }]
    }


def span_payload(
    *,
    session_id: str,
    message_id: str,
    rating: int,
    model: str | None,
    agent: str | None,
    now_ns: int,
) -> dict[str, Any]:
    """OTLP/JSON for one vote, as a span Langfuse files under the session."""
    user = _user()
    attrs = [
        _kv("session.id", session_id),
        _kv("langfuse.trace.name", SPAN),
        _kv("studio.rating", rating),
        _kv("studio.rating.label", "up" if rating > 0 else "down"),
        _kv("studio.message_id", message_id),
        _kv("llm.model_name", _label(model)),
        _kv("agent.name", _label(agent)),
    ]
    if user:
        attrs.append(_kv("user.id", user))
    return {
        "resourceSpans": [{
            "resource": _resource(True),
            "scopeSpans": [{
                "scope": {"name": "studio-api"},
                "spans": [{
                    "traceId": secrets.token_hex(16),
                    "spanId": secrets.token_hex(8),
                    "name": SPAN,
                    "kind": 1,
                    "startTimeUnixNano": str(now_ns),
                    "endTimeUnixNano": str(now_ns),
                    "attributes": attrs,
                }],
            }],
        }]
    }


async def _post(path: str, payload: dict[str, Any]) -> bool:
    import httpx

    url = endpoint()
    if not url:
        return False
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.post(f"{url}{path}", json=payload)
        if r.status_code >= 300:
            log.warning("rating_telemetry_rejected", path=path, status=r.status_code)
            return False
        return True
    except Exception as e:  # noqa: BLE001 — telemetry never fails a rating
        log.warning("rating_telemetry_failed", path=path, error=str(e))
        return False


async def export_counts(db: Any) -> bool:
    """Send the current gauge. No-op without a collector."""
    if not endpoint():
        return False
    try:
        counts = await db.message_feedback_counts()
    except Exception as e:  # noqa: BLE001
        log.warning("rating_counts_failed", error=str(e))
        return False
    return await _post("/v1/metrics", metrics_payload(counts, time.time_ns()))


async def export_vote(
    db: Any,
    *,
    session_id: str,
    message_id: str,
    rating: int,
    model: str | None,
    agent: str | None,
) -> None:
    """After a vote: refresh the gauge now, and send the vote's span if the
    user agreed to share chat text."""
    if not endpoint():
        return
    await export_counts(db)
    if rating == 0:
        return
    from services import telemetry

    if not await telemetry.content_capture(db):
        return
    await _post("/v1/traces", span_payload(
        session_id=session_id, message_id=message_id, rating=rating,
        model=model, agent=agent, now_ns=time.time_ns(),
    ))


async def run_forever(db: Any) -> None:
    """Re-send the gauge every minute while the server runs."""
    if not endpoint():
        return
    while True:
        await export_counts(db)
        await asyncio.sleep(INTERVAL_SECONDS)
