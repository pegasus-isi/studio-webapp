"""Telemetry consent — whether this user's prompts may leave the pod.

Step 6 of #367 puts the chat engine's own OTel plugin behind a collector
(`docs/llm-telemetry-design.md`). Two kinds of data come out of it and they are
not equally sensitive:

* **Metrics** — token counts, tool durations, session and message counts,
  model ids. Activity counters, nothing the user typed. On wherever a collector
  is configured.
* **Traces** — the prompt itself, which the plugin puts in the span's
  `input.value` whatever `capturePromptInLogs` says. That is what this setting
  governs, and it is off until the user turns it on.

Its own settings row rather than a field in `UIPreferences`, for two reasons.
`PUT /api/settings/ui` replaces the preferences wholesale, so a consent flag
living there could be flipped back by an unrelated toggle saved from a stale
cache. And `GET /api/settings/ui` deliberately drops individual values that no
longer validate and substitutes defaults — sound for "was the sidebar open",
wrong for a privacy decision, where the default has to be reached by a path
that cannot be mistaken for a successful read.

Every failure resolves to False. A lookup that raises means we do not know
whether the user consented, and "we do not know" must not send prompts.
"""

from __future__ import annotations

import os
from typing import Any

import structlog

log = structlog.get_logger()

_SETTING_KEY = "telemetry_content_capture"


async def content_capture(db: Any) -> bool:
    """Whether this user has opted into sending prompt/response content."""
    try:
        raw = await db.get_setting(_SETTING_KEY, "0")
    except Exception as e:
        log.warning("telemetry_consent_lookup_failed", error=str(e))
        return False
    return str(raw).lower() in ("1", "true", "yes", "on")


async def set_content_capture(db: Any, value: bool) -> None:
    await db.set_setting(_SETTING_KEY, "1" if value else "0")
    log.info("telemetry_consent_updated", content_capture=value)


def collector_configured() -> bool:
    """Whether a collector is configured for this deployment at all.

    The knowledge adapter leaves the plugin out of the generated config when
    `STUDIO_OTLP_ENDPOINT` is unset — Studio also runs on a FABRIC VM and in
    plain `docker run` containers, where there is nothing to export to. The UI
    reads this so the consent control can say that nothing is being collected
    either way, instead of offering a choice that has no effect.
    """
    return bool((os.environ.get("STUDIO_OTLP_ENDPOINT") or "").strip())
