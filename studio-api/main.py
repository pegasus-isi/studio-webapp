"""FastAPI application with lifespan management for PegasusAI Studio."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

import logging_config as _  # noqa: F401 — triggers configure_logging()
import structlog

from db import Database
from routers import (
    ai_terminal,
    chat,
    dashboard,
    files,
    health,
    jupyter,
    knowledge,
    llm,
    marketplace,
    notifications,
    pegasus_bot,
    provisioning,
    settings,
    terminal,
    tools,
    workflows,
)
from services.procutil import communicate_or_kill

__version__ = "0.1.0"
# Pre-release channel shown next to the version in the UI. Set to "" once the
# release is stable so the UI drops the qualifier.
__release_channel__ = "alpha"

log = structlog.get_logger()

db = Database()


async def _seed_default_tools() -> None:
    """Register image-bundled tools (binary already present) as installed.

    Tools baked into the container (e.g. OpenCode, for the chat engine and
    `opencode attach`) have their binary on PATH but no DB row on first boot,
    so the Workbench would show them as uninstalled and their knowledge
    adapter (AGENTS.md, .opencode/…, provider config) would never run.
    installer.install() skips npm when the binary exists, so seeding is just
    adapter + DB row. Override the set with STUDIO_DEFAULT_TOOLS (csv; empty
    disables).
    """
    import os
    import shutil

    from services.installer import get_tool_info
    from routers.tools import installer as tool_installer

    default_tools = [
        t.strip()
        for t in os.environ.get("STUDIO_DEFAULT_TOOLS", "opencode").split(",")
        if t.strip()
    ]
    if not default_tools:
        return
    installed_ids = {t["tool_id"] for t in await db.list_tools()}
    for tool_id in default_tools:
        if tool_id in installed_ids:
            continue
        info = get_tool_info(tool_id)
        if not info or not shutil.which(info.get("binary") or ""):
            continue  # not bundled in this image — stays an on-demand install
        try:
            await tool_installer.install(tool_id, db)
            log.info("default_tool_seeded", tool_id=tool_id)
        except Exception as e:
            log.warning("default_tool_seed_failed", tool_id=tool_id, error=str(e))


async def _reconcile_installed_tools() -> None:
    """Re-install binaries for tools the DB says are installed but are missing."""
    import asyncio
    import os
    import shutil

    from services.installer import get_tool_info

    installed = await db.list_tools()
    for tool in installed:
        tool_id = tool["tool_id"]

        # No tool process survives a container restart, so a persisted "running"
        # status is always stale here. Reset it (terminal tools carry no pid, so
        # the list_tools pid-liveness check can't correct them on its own).
        if tool.get("status") == "running":
            try:
                await db.update_tool_status(tool_id, "installed")
            except Exception:
                pass

        info = get_tool_info(tool_id)
        if not info:
            continue

        binary = info.get("binary")
        install_command = info.get("install_command")
        if not binary or not install_command:
            continue

        if shutil.which(binary):
            continue

        log.info("reconcile_reinstalling", tool_id=tool_id, binary=binary)
        try:
            # Use npm global prefix from env (set in Dockerfile to /opt/npm-global)
            reconcile_env = os.environ.copy()
            npm_prefix = os.environ.get("NPM_CONFIG_PREFIX", "/opt/npm-global")
            reconcile_env["NPM_CONFIG_PREFIX"] = npm_prefix
            npm_bin = os.path.join(npm_prefix, "bin")
            if npm_bin not in reconcile_env.get("PATH", ""):
                reconcile_env["PATH"] = f"{npm_bin}:{reconcile_env.get('PATH', '')}"

            proc = await asyncio.create_subprocess_shell(
                install_command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=reconcile_env,
            )
            _, stderr = await communicate_or_kill(proc, 300)
            if proc.returncode == 0:
                log.info("reconcile_reinstalled", tool_id=tool_id)
                # Ensure npm-global/bin is in PATH for subsequent tool starts
                if npm_bin not in os.environ.get("PATH", ""):
                    os.environ["PATH"] = f"{npm_bin}:{os.environ.get('PATH', '')}"
            else:
                err = stderr.decode("utf-8", errors="replace")[:200]
                log.warning("reconcile_failed", tool_id=tool_id, error=err)
        except Exception as e:
            log.warning("reconcile_failed", tool_id=tool_id, error=str(e))

    # Fix ownership of tool config dirs that npm may have created as root
    home = os.environ.get("HOME", "")
    try:
        uid = os.getuid()
        gid = os.getgid()
        for dirname in (".codex", ".claude", ".antigravity", ".opencode"):
            if not home:
                continue
            dirpath = os.path.join(home, dirname)
            if os.path.isdir(dirpath):
                for root, dirs, files in os.walk(dirpath):
                    try:
                        st = os.stat(root)
                        if st.st_uid != uid:
                            os.chown(root, uid, gid)
                    except OSError:
                        pass
                    for f in files:
                        fp = os.path.join(root, f)
                        try:
                            st = os.stat(fp)
                            if st.st_uid != uid:
                                os.chown(fp, uid, gid)
                        except OSError:
                            pass
    except Exception as e:
        log.warning("reconcile_chown_failed", error=str(e))


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Startup: connect DB, reconcile tools.  Shutdown: close DB + stop processes."""
    await db.connect()
    log.info("studio_api_started", version=__version__)

    # Stamp the onboarding funnel's start the first time this workspace is ever
    # opened (no-op on every later boot). Recorded at startup rather than on
    # first request so the clock starts when the pod becomes usable, not when
    # the user happens to load a page that polls.
    try:
        from services import onboarding_metrics

        await onboarding_metrics.record(db, "first_seen")
    except Exception as e:  # noqa: BLE001 — metrics must never block startup
        log.warning("onboarding_first_seen_failed", error=str(e))

    # Register image-bundled tools (OpenCode) on first boot, then re-install
    # any tools whose binaries are missing (e.g. after container restart).
    try:
        await _seed_default_tools()
    except Exception as e:
        log.warning("default_tool_seed_error", error=str(e))
    try:
        await _reconcile_installed_tools()
    except Exception as e:
        log.warning("reconcile_error", error=str(e))

    # Re-run the knowledge adapters for installed tools, from inside the process
    # that already owns the DB connection.
    #
    # The entrypoint also runs propagate_llm.py, but that is a second process
    # opening the same NFS-backed SQLite while this one is starting, and it
    # loses the race: a pod was found whose propagate_llm.log stopped at
    # "database_connected", killed by its 20s timeout, so no adapter had run —
    # CLAUDE.md was never written and stale per-workspace copies were never
    # pruned, with nothing but that truncated log to say so.
    #
    # Reconcile above only reinstalls binaries; it does not touch tool config.
    try:
        from llm.propagator import LLMPropagator

        await LLMPropagator().propagate(db)
    except Exception as e:
        log.warning("llm_propagate_error", error=str(e))

    import asyncio
    import os

    async def _refresh_knowledge_checkout() -> None:
        """Fast-forward the marketplace checkout, then make the update visible.

        opencode reads skills, agents and config once when it starts and never
        reloads them. The pull used to run from the entrypoint, backgrounded
        alongside the rest of the boot, which made "does the assistant get
        today's skills?" a race with no signal either way: a pod was observed
        winning it by 67 seconds, and losing it looks exactly the same from
        outside — the checkout on disk is current, `git log` says so, and the
        assistant quietly follows the skills of whenever the server started.

        Doing it here means one owner, and a restart when (and only when) the
        checkout actually moved, so the running server picks the new skills up.
        """
        from knowledge import checkout

        try:
            result = await asyncio.to_thread(checkout.update)
        except Exception as e:
            log.warning("knowledge_checkout_update_failed", error=str(e))
            return

        if not result.get("updated"):
            log.info(
                "knowledge_checkout_unchanged",
                sha=result.get("short_sha"),
                reason=result.get("reason"),
            )
            return

        log.info("knowledge_checkout_updated", sha=result.get("short_sha"))

        # Re-run the adapters so generated context/links follow the new tree,
        # then restart opencode so it re-reads them.
        try:
            from llm.propagator import LLMPropagator

            await LLMPropagator().propagate(db)
        except Exception as e:
            log.warning("knowledge_repropagate_failed", error=str(e))
        try:
            from services.opencode_engine import engine as opencode_engine

            await opencode_engine.forget_sessions(db)
            await opencode_engine.restart()
            log.info("opencode_restarted_on_knowledge_update")
        except Exception as e:
            log.warning("opencode_restart_failed", error=str(e))

    knowledge_task = asyncio.create_task(_refresh_knowledge_checkout())

    # Periodically reap terminal sessions nobody has been attached to for a
    # long time (server-held shells survive reloads, not abandonment).

    from services import terminal_sessions

    idle_secs = int(os.environ.get("STUDIO_TERMINAL_IDLE_SECONDS", "86400"))

    chat_max_rows = int(os.environ.get("STUDIO_CHAT_MAX_ROWS", "5000"))
    runs_max_rows = int(os.environ.get("STUDIO_RUNS_MAX_ROWS", "1000"))

    async def _prune_terminals() -> None:
        while True:
            await asyncio.sleep(3600)
            killed = terminal_sessions.prune_idle(idle_secs)
            if killed:
                log.info("terminal_sessions_pruned", count=killed)
            # Also cap the chat_messages / workflow_runs tables so they can't
            # grow unbounded on a long-lived container.
            try:
                deleted = await db.prune_chat_messages(chat_max_rows)
                if deleted:
                    log.info("chat_messages_pruned", count=deleted)
            except Exception as e:
                log.warning("chat_prune_failed", error=str(e))
            try:
                deleted = await db.prune_workflow_runs(runs_max_rows)
                if deleted:
                    log.info("workflow_runs_pruned", count=deleted)
            except Exception as e:
                log.warning("runs_prune_failed", error=str(e))

    prune_task = asyncio.create_task(_prune_terminals())

    # Start the notification engine: watches workflow runs for state changes /
    # failures and fans alerts out to subscribers (SSE). Other code raises
    # alerts via `engine.notify(...)`.
    from services.notification_engine import engine as notification_engine

    # Watch the SAME root the run list scans (STUDIO_SCAN_DIR, else $HOME), not
    # $HOME/work — otherwise a run submitted outside ~/work is listed but never
    # produces a completed/failed notification (BE-8). Compute it here at
    # startup (reading the env now) rather than importing workflow_scanner's
    # SCAN_ROOT, which is frozen at import time: under the test harness that
    # constant can freeze to the real $HOME before HOME is patched, which would
    # point the scan at the developer's entire home directory.
    scan_root = os.environ.get("STUDIO_SCAN_DIR") or os.environ.get("HOME", "/tmp")
    notification_engine.start(db, scan_root)
    await notification_engine.refresh_prefs()

    yield
    # Cleanup terminal sessions and running tool processes
    await notification_engine.stop()
    from services.opencode_engine import engine as opencode_engine

    await opencode_engine.stop()
    prune_task.cancel()
    knowledge_task.cancel()
    for s in terminal_sessions.list_sessions():
        terminal_sessions.kill(s["id"])
    from services.process_mgr import process_manager

    await process_manager.cleanup_all()
    await db.close()
    log.info("studio_api_stopped")


app = FastAPI(
    title="PegasusAI Studio API",
    description="Backend API for PegasusAI Studio",
    version=__version__,
    lifespan=lifespan,
)

# CORS — the frontend, API and WS are same-origin (one container port behind the
# gateway), so same-origin requests need no CORS at all. Never pair a wildcard
# origin with credentials: the gateway's session cookie rides along automatically,
# so reflecting an arbitrary Origin would let any site the user visits drive this
# API in their browser. Extra origins (e.g. a `next dev` server) via
# STUDIO_ALLOWED_ORIGINS.
from security import allowed_origins as _allowed_origins  # noqa: E402

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routers
app.include_router(health.router)
app.include_router(settings.router)
app.include_router(llm.router)
app.include_router(knowledge.router)
app.include_router(tools.router)
app.include_router(files.router)
app.include_router(jupyter.router)
app.include_router(terminal.router)
app.include_router(ai_terminal.router)
app.include_router(workflows.router)
app.include_router(chat.router)
app.include_router(notifications.router)
app.include_router(dashboard.router)
app.include_router(provisioning.router)
app.include_router(pegasus_bot.router)
app.include_router(marketplace.router)


@app.get("/api/whoami")
async def whoami(request: Request) -> dict:
    """Authenticated identity + JupyterHub control URLs for the user menu.

    KubeSpawner injects JUPYTERHUB_USER / JUPYTERHUB_BASE_URL into the pod;
    fall back to the gateway's X-Auth-User header. logout/control URLs point
    at the Hub so the menu can sign out and stop/start the server.
    """
    import os

    # JUPYTERHUB_USER is server-injected and trusted. X-Auth-User is only
    # trustworthy if the gateway strips any client-supplied copy and sets its
    # own; otherwise a client could spoof the *displayed* identity (it's not an
    # auth boundary — the gateway session is — but don't trust it blindly).
    # The header fallback is opt-in (SEC-6): production identity comes from
    # JUPYTERHUB_USER, so the default-off flag costs nothing there; operators
    # fronting the app with a non-Hub gateway that guarantees the header set
    # STUDIO_TRUST_AUTH_HEADER=1 explicitly.
    user = os.environ.get("JUPYTERHUB_USER")
    trust_header = os.environ.get("STUDIO_TRUST_AUTH_HEADER", "0").lower() in (
        "1",
        "true",
        "yes",
    )
    if not user and trust_header:
        user = request.headers.get("x-auth-user", "").strip() or None
    hub_base = os.environ.get("JUPYTERHUB_BASE_URL", "/")
    if not hub_base.endswith("/"):
        hub_base += "/"
    return {
        "user": user,
        "logout_url": f"{hub_base}hub/logout" if user else None,
        "control_url": f"{hub_base}hub/home" if user else None,
    }
