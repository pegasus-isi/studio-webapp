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

    registry = get_all_tools()
    installed = {t["tool_id"]: t for t in await db.list_tools()}

    results = []
    for tool in registry:
        tool_id = tool["id"]
        inst = installed.get(tool_id)

        status = inst["status"] if inst else None
        pid = inst["process_pid"] if inst else None

        # Auto-correct stale "running" status when process is dead
        if status == "running" and not _is_pid_alive(pid):
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
    from main import db

    info = get_tool_info(tool_id)
    if info is None:
        raise HTTPException(status_code=404, detail=f"Tool not found: {tool_id}")

    inst = await db.get_tool(tool_id)

    status = inst["status"] if inst else None
    pid = inst["process_pid"] if inst else None

    # Auto-correct stale status
    if status == "running" and not _is_pid_alive(pid):
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

        proc_info = process_manager.start_terminal_tool(tool_id, binary, env)
        await db.update_tool_status(
            tool_id, "running", process_pid=proc_info.pid,
        )
        return {
            "status": "running",
            "pid": proc_info.pid,
            "binary_found": binary_found,
        }


@router.post("/{tool_id}/stop")
async def stop_tool(tool_id: str) -> dict[str, str]:
    from main import db

    stopped = process_manager.stop_tool(tool_id)
    if stopped:
        await db.update_tool_status(tool_id, "installed")
    return {"status": "stopped" if stopped else "not_running"}


@router.get("/{tool_id}/status")
async def tool_status(tool_id: str) -> dict[str, str | bool]:
    running = process_manager.is_running(tool_id)
    return {"tool_id": tool_id, "running": running}
