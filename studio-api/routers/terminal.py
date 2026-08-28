"""Terminal sessions: server-held PTYs that survive browser reloads.

Control plane (HTTP):
    POST   /api/terminals               create a session (type: bash | tool)
    GET    /api/terminals               list live sessions (for reattach)
    DELETE /api/terminals/{id}          kill a session

Data plane (WebSocket):
    WS /ws/terminals/{id}               attach: replay scrollback, then stream.
                                        Disconnect detaches — the shell keeps
                                        running. (Plural path: the singular
                                        /ws/terminal/{tool_id} is the legacy
                                        spawn-on-connect AI-tool endpoint.)

Legacy (kept until all clients migrate):
    WS /ws/terminal                     spawn-on-connect bash, dies with the WS
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil

import structlog
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from security import reject_ws_if_cross_origin
from services import terminal_sessions as ts
from services.installer import get_tool_info

log = structlog.get_logger()

router = APIRouter(tags=["terminal"])

WORKSPACE_ROOT = os.environ.get(
    "STUDIO_WORKSPACE_DIR",
    os.path.join(os.environ.get("HOME", "/tmp"), "work"),
)


class CreateTerminalBody(BaseModel):
    type: str = "bash"  # "bash" | "tool"
    tool_id: str | None = None
    label: str | None = None
    cwd: str | None = None
    # Open the shell on the active remote compute backend's submit host
    # instead of locally. Ignored when the active backend is local.
    remote: bool = False


def _contained_cwd(cwd: str | None) -> str:
    """Resolve a requested cwd, confining it to the workspace.

    A shell can `cd` anywhere once open, so this isn't a hard sandbox — but it
    keeps the boundary consistent with /api/workflows/submit and stops a session
    from being *rooted* outside the workspace (e.g. by a cross-origin caller)."""
    root = os.path.realpath(WORKSPACE_ROOT)
    if not cwd:
        return root
    resolved = os.path.realpath(os.path.expanduser(cwd))
    if resolved == root or resolved.startswith(root + os.sep):
        if os.path.isdir(resolved):
            return resolved
    return root


async def _llm_env() -> dict[str, str]:
    """API-key env overlay from the current LLM config (never on the argv)."""
    from main import db

    env: dict[str, str] = {}
    llm_config = await db.get_llm_config() or {}
    api_key = llm_config.get("api_key", "")
    key_map = {
        "anthropic": "ANTHROPIC_API_KEY",
        "openai": "OPENAI_API_KEY",
        "google": "GOOGLE_API_KEY",
        "fabric": "FABRIC_AI_API_KEY",
        "nrp": "NRP_API_KEY",
    }
    env_key = key_map.get(llm_config.get("provider", ""))
    if env_key and api_key:
        env[env_key] = api_key
    return env


def _tool_command(tool_id: str) -> tuple[list[str], str]:
    """(argv, label) for an AI tool session; falls back to a bash shell with
    install instructions when the binary is missing."""
    info = get_tool_info(tool_id)
    if not info or info.get("type") != "terminal":
        raise HTTPException(
            status_code=400, detail=f"Tool not available as terminal: {tool_id}"
        )
    binary = info.get("binary")
    if not binary:
        raise HTTPException(status_code=400, detail="No binary configured")

    label = info.get("name", tool_id)
    binary_path = shutil.which(binary)
    if binary_path:
        return [binary_path], label

    install_cmd = info.get("install_command", f"npm install -g {binary}")
    log.warning("terminal_tool_binary_missing", tool_id=tool_id, binary=binary)
    return [
        "bash",
        "-c",
        f'echo "\\033[1;31mError: {binary} is not installed.\\033[0m"; '
        f'echo ""; '
        f'echo "Run the following command to install it:"; '
        f'echo "  \\033[1;33m{install_cmd}\\033[0m"; '
        f'echo ""; '
        f"exec bash",
    ], label


@router.post("/api/terminals")
async def create_terminal(body: CreateTerminalBody) -> dict:
    """Create a server-held terminal session and return its metadata."""
    cwd = _contained_cwd(body.cwd)
    if body.type == "bash":
        command: list[str] = ["/bin/bash"]
        label = body.label or "Terminal"
        session_type = "bash"
        if body.remote:
            from services.backends import active_backend

            backend = active_backend()
            if backend.is_remote:
                # A shell on the backend's submit host over the same
                # multiplexed channel the backend uses; -t forces a PTY.
                command = [
                    "ssh",
                    "-t",
                    "-F",
                    backend.ensure_ssh_config(),
                    backend.id,
                ]
                label = body.label or f"{backend.label} (remote)"
                session_type = "remote"
        session = ts.create(
            type=session_type,
            command=command,
            label=label,
            cwd=cwd,
        )
    elif body.type == "tool":
        if not body.tool_id:
            raise HTTPException(status_code=400, detail="tool_id is required")
        command, label = _tool_command(body.tool_id)
        session = ts.create(
            type=f"tool:{body.tool_id}",
            command=command,
            label=body.label or label,
            cwd=cwd,
            env=await _llm_env(),
        )
    else:
        raise HTTPException(
            status_code=400, detail=f"Unsupported terminal type: {body.type!r}"
        )
    return ts.meta(session)


@router.get("/api/terminals")
async def list_terminals() -> list[dict]:
    """List live terminal sessions (so a reloaded client can reattach)."""
    return ts.list_sessions()


@router.delete("/api/terminals/{session_id}")
async def delete_terminal(session_id: str) -> dict:
    """Kill a terminal session (closes the shell for all attached clients)."""
    if not ts.kill(session_id):
        raise HTTPException(status_code=404, detail="No such terminal session")
    return {"ok": True}


@router.websocket("/ws/terminals/{session_id}")
async def attach_terminal(websocket: WebSocket, session_id: str) -> None:
    """Attach to a server-held session: replay scrollback, then stream live.

    Disconnect detaches (the shell keeps running) — never kills. Multiple
    clients attached to one session share a single view."""
    # Reject cross-origin handshakes (CSWSH → shell access). See security.py.
    if await reject_ws_if_cross_origin(websocket):
        return
    session = ts.get(session_id)
    if session is None or session.closed:
        await websocket.close(code=4004, reason="No such terminal session")
        return

    await websocket.accept()
    queue = session.attach()
    log.info("terminal_attached", session_id=session_id, clients=session.attached)
    try:
        # Replay recent output so the (re)attaching client redraws its screen.
        snap = session.snapshot()
        if snap:
            await websocket.send_text(snap)

        async def pump_out() -> None:
            while True:
                chunk = await queue.get()
                if chunk is None:  # session ended (shell exited)
                    try:
                        await websocket.close(code=1000)
                    except Exception:  # noqa: BLE001
                        pass
                    break
                await websocket.send_text(chunk)

        out_task = asyncio.create_task(pump_out())

        while True:
            try:
                msg = await websocket.receive_text()
            except WebSocketDisconnect:
                break
            except Exception:  # noqa: BLE001
                break
            if msg.startswith("{"):
                try:
                    parsed = json.loads(msg)
                except json.JSONDecodeError:
                    session.write(msg)
                    continue
                if parsed.get("type") == "resize":
                    session.resize(
                        int(parsed.get("cols", 80)), int(parsed.get("rows", 24))
                    )
                elif parsed.get("type") == "input":
                    session.write(parsed.get("data", ""))
                else:
                    # Pasted text that merely looks like JSON is still input
                    session.write(msg)
                continue
            session.write(msg)

        out_task.cancel()
    finally:
        session.detach(queue)
        log.info(
            "terminal_detached", session_id=session_id, clients=session.attached
        )


# ---------------------------------------------------------------------------
# Legacy: spawn-on-connect bash whose shell dies with the WebSocket. Kept so
# stale clients (cached SPA bundles) keep working during the transition.
# ---------------------------------------------------------------------------


@router.websocket("/ws/terminal")
async def bash_terminal(websocket: WebSocket) -> None:
    """Legacy WebSocket PTY for a plain bash shell (not reload-safe)."""
    # Reject cross-origin handshakes (CSWSH → shell access). See security.py.
    if await reject_ws_if_cross_origin(websocket):
        return
    await websocket.accept()

    session = ts.create(
        type="bash", command=["/bin/bash"], label="Terminal", cwd=WORKSPACE_ROOT
    )
    queue = session.attach()
    log.info("bash_terminal_opened_legacy", session_id=session.id)
    try:
        async def pump_out() -> None:
            while True:
                chunk = await queue.get()
                if chunk is None:
                    break
                await websocket.send_text(chunk)

        out_task = asyncio.create_task(pump_out())
        while True:
            try:
                data = await websocket.receive_text()
            except (WebSocketDisconnect, Exception):  # noqa: BLE001
                break
            if data.startswith("{"):
                try:
                    msg = json.loads(data)
                    if msg.get("type") == "resize":
                        session.resize(
                            int(msg.get("cols", 80)), int(msg.get("rows", 24))
                        )
                        continue
                except json.JSONDecodeError:
                    pass
            session.write(data)
        out_task.cancel()
    finally:
        session.detach(queue)
        ts.kill(session.id)  # legacy semantics: shell dies with the socket
        log.info("bash_terminal_closed_legacy", session_id=session.id)
