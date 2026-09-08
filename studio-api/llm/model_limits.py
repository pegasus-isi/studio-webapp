"""What a model will actually let us ask for as an output ceiling.

Only the Anthropic Messages API makes ``max_tokens`` mandatory, so it is the
one transport that cannot leave the ceiling to the model. It used to send a
fixed 8192 — the value every Messages-API model accepts — which quietly
truncated every current model at a sixteenth of its real capacity, and worse on
a reasoning model, whose thinking is billed against the same budget and can be
spent before any visible content appears.

So ask the model. Anthropic's Models API reports each model's own output cap as
``max_tokens``, which means there is no table here to go stale as models ship:
an old model that really does cap at 8192 reports that, and a current one
reports 128000. The lookup is cached, never blocks a turn for long, and falls
back to the old constant when it cannot be answered.
"""

from __future__ import annotations

import asyncio
import time

import httpx
import structlog

log = structlog.get_logger()

# What the Messages API accepted before any of this existed, and what a failed
# lookup falls back to. Deliberately the conservative floor: every
# Messages-API model accepts it, so a turn always goes out.
FLOOR_MAX_TOKENS = 8192

# Long enough to be worth caching across a conversation, short enough that a
# newly released model's real ceiling is picked up the same day.
_TTL_S = 6 * 60 * 60
# The lookup sits in front of a turn the user is waiting on, so it gets one
# short attempt and then gets out of the way.
_TIMEOUT_S = 3.0

# (base_url, model) -> (cap, fetched_at). A negative result is cached too, so a
# provider that does not implement the endpoint is asked once, not once a turn.
_cache: dict[tuple[str, str], tuple[int | None, float]] = {}
_locks: dict[tuple[str, str], asyncio.Lock] = {}


def _cached(key: tuple[str, str]) -> tuple[bool, int | None]:
    entry = _cache.get(key)
    if entry is None:
        return False, None
    cap, fetched_at = entry
    if time.monotonic() - fetched_at > _TTL_S:
        return False, None
    return True, cap


async def _fetch_cap(base_url: str, api_key: str, model: str) -> int | None:
    """Read one model's output cap from the Anthropic Models API."""
    url = f"{base_url.rstrip('/')}/v1/models/{model}"
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        body = resp.json()
    # `max_tokens` is the output cap; `max_input_tokens` is the context window.
    # Older deployments predate both fields, which reads as "cannot answer".
    cap = body.get("max_tokens")
    if isinstance(cap, bool) or not isinstance(cap, int) or cap <= 0:
        return None
    return cap


async def anthropic_max_tokens(base_url: str, api_key: str, model: str) -> int:
    """The output ceiling to send for ``model``, from the model itself.

    Never raises and never leaves a turn without a ceiling: anything that goes
    wrong — no key, an endpoint that predates the field, a timeout, a model id
    the provider does not recognise — returns the floor, which is what this
    code sent unconditionally before.
    """
    if not model:
        return FLOOR_MAX_TOKENS
    key = (base_url or "", model)

    hit, cap = _cached(key)
    if hit:
        return cap or FLOOR_MAX_TOKENS

    # One lookup per model, however many turns start at once.
    lock = _locks.setdefault(key, asyncio.Lock())
    async with lock:
        hit, cap = _cached(key)
        if hit:
            return cap or FLOOR_MAX_TOKENS
        try:
            cap = await _fetch_cap(base_url, api_key, model)
        except Exception as e:
            cap = None
            log.info(
                "anthropic_model_cap_unavailable",
                model=model,
                error=str(e),
                fallback=FLOOR_MAX_TOKENS,
            )
        else:
            log.info("anthropic_model_cap", model=model, max_tokens=cap)
        _cache[key] = (cap, time.monotonic())

    return cap or FLOOR_MAX_TOKENS


def reset_cache() -> None:
    """Forget every looked-up cap. For tests, and for a provider-config change."""
    _cache.clear()
    _locks.clear()
