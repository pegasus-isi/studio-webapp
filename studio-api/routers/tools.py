"""AI tool marketplace endpoints."""

from __future__ import annotations

import os
import shutil

import structlog
from fastapi import APIRouter, HTTPException

from models import InstallResponse, ToolDetailResponse, ToolInfo, ToolStatus
from services.installer import ToolInstaller, get_all_tools, get_tool_info
from services.process_mgr import find_free_port, process_manager

log = structlog.get_logger()

router = APIRouter(prefix="/api/tools", tags=["tools"])
installer = ToolInstaller()


def _hackathon_mode() -> bool:
    """Whether the hackathon kiosk restrictions apply (STUDIO_HACKATHON_MODE).

    Read at call time so tests can flip it with monkeypatch.setenv. Default
    off: unset means the full tool marketplace.
    """
    return os.environ.get("STUDIO_HACKATHON_MODE", "").strip().lower() in (
        "1", "true", "yes",
    )


def _is_pid_alive(pid: int | None) -> bool:
    """Check if a process is still running."""
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


@router.get("")
async def list_tools() -> dict[str, list[ToolDetailResponse]]:
    """List available tools merged with DB install status.

    Auto-corrects stale 'running' status when the process is dead.
    """
    from main import db

    if _hackathon_mode():
        # Kiosk: Settings shows the Bash Terminal launcher only. Installed
        # rows (if any linger in the DB) stay stored but are not offered.
        return {"tools": []}

    registry = get_all_tools()
    installed = {t["tool_id"]: t for t in await db.list_tools()}

    results = []
    for tool in registry:
        tool_id = tool["id"]
        inst = installed.get(tool_id)

        status = inst["status"] if inst else None
        pid = inst["process_pid"] if inst else None

        # Auto-correct stale "running" status when a tracked process is dead.
        # Only applies to tools that have a real manager process (web tools):
        # terminal tools carry no pid (their PTY is owned by the WS session), so
        # a None pid here means "no process to check", not "process died".
        if status == "running" and pid is not None and not _is_pid_alive(pid):
            await db.update_tool_status(tool_id, "installed")
            status = "installed"
            pid = None
            log.info("tool_status_corrected", tool_id=tool_id, reason="pid_dead")

        results.append(ToolDetailResponse(
            info=ToolInfo(**tool),
            installed=inst is not None,
            status=ToolStatus(status) if status else None,
            process_pid=pid,
            web_port=inst["web_port"] if inst else None,
        ))

    return {"tools": results}


@router.get("/{tool_id}", response_model=ToolDetailResponse)
async def get_tool(tool_id: str) -> ToolDetailResponse:
    if _hackathon_mode():
        raise HTTPException(status_code=404, detail="Tools are disabled for this event")
    from main import db

    info = get_tool_info(tool_id)
    if info is None:
        raise HTTPException(status_code=404, detail=f"Tool not found: {tool_id}")

    inst = await db.get_tool(tool_id)

    status = inst["status"] if inst else None
    pid = inst["process_pid"] if inst else None

    # Auto-correct stale status — but only for tools with a real manager
    # process (web tools). Terminal tools carry no pid, so `_is_pid_alive(None)`
    # is False and would wrongly flip a genuinely-running terminal tool's badge
    # to "installed" (BE-8). Mirror the `pid is not None` guard in list_tools.
    if status == "running" and pid is not None and not _is_pid_alive(pid):
        await db.update_tool_status(tool_id, "installed")
        status = "installed"
        pid = None

    return ToolDetailResponse(
        info=ToolInfo(**info),
        installed=inst is not None,
        status=ToolStatus(status) if status else None,
        process_pid=pid,
        web_port=inst["web_port"] if inst else None,
    )


@router.post("/{tool_id}/install", response_model=InstallResponse)
async def install_tool(tool_id: str) -> InstallResponse:
    if _hackathon_mode():
        # 403, not 404: the tool exists — reinstalling it is what's refused,
        # otherwise opencode comes back with one click.
        raise HTTPException(status_code=403, detail="Installing tools is disabled for this event")
    from main import db

    try:
        status = await installer.install(tool_id, db)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

    return InstallResponse(tool_id=tool_id, status=status)


@router.post("/{tool_id}/uninstall", response_model=InstallResponse)
async def uninstall_tool(tool_id: str) -> InstallResponse:
    from main import db

    # Stop first if running
    process_manager.stop_tool(tool_id)

    status = await installer.uninstall(tool_id, db)
    return InstallResponse(tool_id=tool_id, status=status)


@router.post("/{tool_id}/start")
async def start_tool(tool_id: str) -> dict[str, str | int | None]:
    from main import db

    info = get_tool_info(tool_id)
    if info is None:
        raise HTTPException(status_code=404, detail=f"Tool not found: {tool_id}")

    inst = await db.get_tool(tool_id)
    if inst is None:
        raise HTTPException(status_code=400, detail="Tool not installed")

    # Build environment with LLM config
    llm_config = await db.get_llm_config() or {}
    env: dict[str, str] = {}
    if llm_config.get("api_key"):
        provider = llm_config.get("provider", "")
        key_map = {
            "anthropic": "ANTHROPIC_API_KEY",
            "openai": "OPENAI_API_KEY",
            "google": "GOOGLE_API_KEY",
            "fabric": "FABRIC_AI_API_KEY",
            "nrp": "NRP_API_KEY",
        }
        env_key = key_map.get(provider)
        if env_key:
            env[env_key] = llm_config["api_key"]

    tool_type = info.get("type", "terminal")

    if tool_type == "web":
        web_command = info.get("web_command", "")
        if not web_command:
            raise HTTPException(status_code=400, detail="No web command configured")
        port = find_free_port()
        proc_info = process_manager.start_web_tool(tool_id, web_command, port, env)
        await db.update_tool_status(
            tool_id, "running", process_pid=proc_info.pid, web_port=port,
        )
        return {"status": "running", "port": port, "pid": proc_info.pid}
    else:
        binary = info.get("binary")
        if not binary:
            raise HTTPException(status_code=400, detail="No binary configured")

        # Check if binary exists — warn but still allow opening a terminal
        # (ai_terminal.py will fall back to a bash shell with install instructions)
        binary_found = shutil.which(binary) is not None
        if not binary_found:
            log.warning(
                "tool_binary_not_found",
                tool_id=tool_id,
                binary=binary,
                msg="Binary not in PATH; terminal will show install instructions",
            )

        # Do NOT spawn a process here. A terminal tool's real PTY is owned by
        # the WebSocket session (services/terminal_sessions.py, created via
        # POST /api/terminals when the tab opens). Spawning one here produced a
        # duplicate, unread PTY child that blocked forever once its ~64KB buffer
        # filled — a process + fd leak. /start just flips the UI status.
        await db.update_tool_status(tool_id, "running")
        return {
            "status": "running",
            "pid": None,
            "binary_found": binary_found,
        }


@router.post("/{tool_id}/stop")
async def stop_tool(tool_id: str) -> dict[str, str]:
    from main import db

    # stop_tool handles web tools (and any legacy tracked process). Terminal
    # tools have no tracked process, so always reset the DB status so the "stop"
    # actually clears the UI's running badge.
    stopped = process_manager.stop_tool(tool_id)
    await db.update_tool_status(tool_id, "installed")
    return {"status": "stopped" if stopped else "not_running"}


@router.get("/{tool_id}/status")
async def tool_status(tool_id: str) -> dict[str, str | bool]:
    running = process_manager.is_running(tool_id)
    return {"tool_id": tool_id, "running": running}
