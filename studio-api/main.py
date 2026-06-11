"""FastAPI application with lifespan management for PegasusAI Studio."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import logging_config as _  # noqa: F401 — triggers configure_logging()
import structlog

from db import Database
from routers import (
    ai_terminal,
    chat,
    files,
    health,
    jupyter,
    knowledge,
    llm,
    settings,
    terminal,
    tools,
    workflows,
)

__version__ = "0.1.0"

log = structlog.get_logger()

db = Database()


async def _reconcile_installed_tools() -> None:
    """Re-install binaries for tools the DB says are installed but are missing."""
    import asyncio
    import os
    import shutil

    from services.installer import get_tool_info

    installed = await db.list_tools()
    for tool in installed:
        tool_id = tool["tool_id"]
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
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
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

    # Re-install any tools whose binaries are missing (e.g. after container restart)
    try:
        await _reconcile_installed_tools()
    except Exception as e:
        log.warning("reconcile_error", error=str(e))

    yield
    # Cleanup running tool processes
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

# CORS — allow all origins for V1 single-user container
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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
