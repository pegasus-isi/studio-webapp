"""The supervised `opencode serve` process that backs the chat panel (#367).

Everything that needs to *reach* the agent server goes through here: its
address, the per-user password the entrypoint generated, a health probe, and
the restart the LLM propagator needs.

Deliberately thin. Studio does not parse OpenCode's payloads anywhere — that is
the whole point of the design in #367 — so this module knows how to connect and
nothing about what comes back.
"""

from __future__ import annotations

import asyncio
import base64
import os
import socket

import httpx
import structlog

log = structlog.get_logger()

DEFAULT_PORT = 4096
# Supervisord's program name, and the config it was started with. Restart goes
# through supervisorctl rather than a signal because supervisord owns the
# process and would restart it anyway — this way the two agree on state.
PROGRAM = "opencode"
SUPERVISOR_CONF = "/etc/supervisor/supervisord.conf"
_RESTART_TIMEOUT_S = 20.0
_HEALTH_TIMEOUT_S = 2.0

# The username is fixed by OpenCode; only the password is ours.
USERNAME = "opencode"


def port() -> int:
    """The loopback port the agent server listens on."""
    raw = os.environ.get("STUDIO_OPENCODE_PORT", "")
    try:
        return int(raw) if raw else DEFAULT_PORT
    except ValueError:
        log.warning("opencode_port_invalid", value=raw, fallback=DEFAULT_PORT)
        return DEFAULT_PORT


def base_url() -> str:
    """Base URL of the agent server. Loopback only, never routable."""
    return f"http://127.0.0.1:{port()}"


def password_file() -> str:
    return os.path.join(os.environ.get("HOME", "/tmp"), ".studio", "opencode-password")


def server_password() -> str | None:
    """The per-user server password, or None if there isn't one yet.

    The environment wins so a hand-started server in local dev is reachable
    without a file, but the file is what survives: studio-api and the wrapper
    script are different processes and only one of them inherits the
    entrypoint's environment reliably.
    """
    from_env = os.environ.get("OPENCODE_SERVER_PASSWORD")
    if from_env:
        return from_env
    try:
        with open(password_file()) as f:
            value = f.read().strip()
        return value or None
    except OSError:
        return None


def auth_header() -> dict[str, str]:
    """Basic-auth header for the agent server, empty if we have no password.

    An empty dict rather than an exception: the proxy turns a missing password
    into one clear 503 for the browser, which is more useful than a 500 per
    request.
    """
    pw = server_password()
    if not pw:
        return {}
    token = base64.b64encode(f"{USERNAME}:{pw}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def listening() -> bool:
    """True if something is accepting connections on the agent server's port.

    Same idiom as routers/jupyter.py: cheap, and true even while the server is
    still loading its config, which is why the status endpoint also asks
    /global/health.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port())) == 0


async def health() -> dict[str, object]:
    """Probe the agent server, reporting why it is unusable when it is.

    Never raises. The chat panel renders this, so "no password yet" and "not
    listening" have to be distinguishable from "up".
    """
    headers = auth_header()
    if not headers:
        return {
            "running": False,
            "reason": "no_password",
            "detail": f"No server password at {password_file()}",
        }
    if not await asyncio.to_thread(listening):
        # Off the event loop: connect_ex blocks, and on loopback that is
        # instant only while packets are being refused rather than dropped.
        return {"running": False, "reason": "not_listening", "detail": None}
    try:
        async with httpx.AsyncClient(timeout=_HEALTH_TIMEOUT_S) as client:
            res = await client.get(f"{base_url()}/global/health", headers=headers)
        if res.status_code == 401:
            # The file changed under a running server: it read the old password
            # at startup and ours is newer. A restart is the fix, and saying so
            # beats "unauthorized" from a server nobody logged into.
            return {
                "running": False,
                "reason": "unauthorized",
                "detail": "Password mismatch — restart the chat engine",
            }
        res.raise_for_status()
        body = res.json()
        return {
            "running": bool(body.get("healthy", True)),
            "reason": None,
            "version": body.get("version"),
        }
    except (httpx.HTTPError, ValueError) as e:
        return {"running": False, "reason": "unreachable", "detail": str(e)}


async def restart() -> bool:
    """Restart the agent server so it re-reads its config.

    OpenCode reads opencode.json once at startup, and neither
    /global/dispose nor /instance/dispose reloads it (verified in the #367
    spike, see docs/OPENCODE-CHAT-SPIKE.md) — so a provider change that only
    rewrote the file left the running server answering with the old model.
    That is what the previous adapter surfaced as "Model not found".

    Best-effort: outside the container there is no supervisord, and a failure
    here must never take down a settings save.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "supervisorctl",
            "-c",
            SUPERVISOR_CONF,
            "restart",
            PROGRAM,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except (OSError, ValueError) as e:
        log.info("opencode_restart_unavailable", error=str(e))
        return False
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=_RESTART_TIMEOUT_S)
    except asyncio.TimeoutError:
        proc.kill()
        # Reaped so the child does not linger as a zombie. Note that killing
        # supervisorctl does not cancel the restart — supervisord owns it and
        # will finish — so this reports "we stopped waiting", not "it failed".
        await proc.wait()
        log.warning("opencode_restart_timeout", timeout=_RESTART_TIMEOUT_S)
        return False
    ok = proc.returncode == 0
    log.info(
        "opencode_restarted" if ok else "opencode_restart_failed",
        returncode=proc.returncode,
        output=(out or b"").decode(errors="replace").strip()[:500],
    )
    return ok
