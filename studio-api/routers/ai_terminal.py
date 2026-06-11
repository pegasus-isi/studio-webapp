"""WebSocket PTY for AI tool terminal sessions.

Ported from LoomAI pattern: pty.openpty(), concurrent ws↔pty relay.
"""

from __future__ import annotations

import asyncio
import fcntl
import json
import os
import shutil
import signal
import struct
import subprocess
import termios

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from services.installer import get_tool_info
from services.process_mgr import process_manager

log = structlog.get_logger()

router = APIRouter(tags=["ai_terminal"])

WORKSPACE_ROOT = os.environ.get(
    "STUDIO_WORKSPACE_DIR",
    os.path.join(os.environ.get("HOME", "/tmp"), "work"),
)


@router.websocket("/ws/terminal/{tool_id}")
async def ai_tool_terminal(websocket: WebSocket, tool_id: str) -> None:
    """WebSocket PTY for a specific AI coding tool."""
    await websocket.accept()

    info = get_tool_info(tool_id)
    if not info or info.get("type") != "terminal":
        await websocket.close(code=4004, reason="Tool not available as terminal")
        return

    binary = info.get("binary")
    if not binary:
        await websocket.close(code=4004, reason="No binary configured")
        return

    # Build environment with LLM config
    from main import db

    env = os.environ.copy()
    env["TERM"] = "xterm-256color"

    llm_config = await db.get_llm_config() or {}
    api_key = llm_config.get("api_key", "")
    provider = llm_config.get("provider", "")
    key_map = {
        "anthropic": "ANTHROPIC_API_KEY",
        "openai": "OPENAI_API_KEY",
        "fabric": "FABRIC_AI_API_KEY",
        "nrp": "NRP_API_KEY",
    }
    env_key = key_map.get(provider)
    if env_key and api_key:
        env[env_key] = api_key

    # Check if binary exists; if not, open a bash shell with an error message
    # and the install command so the user can install manually
    binary_path = shutil.which(binary)
    if binary_path:
        shell_cmd = [binary_path]
    else:
        install_cmd = info.get("install_command", f"npm install -g {binary}")
        shell_cmd = [
            "bash", "-c",
            f'echo "\\033[1;31mError: {binary} is not installed.\\033[0m"; '
            f'echo ""; '
            f'echo "Run the following command to install it:"; '
            f'echo "  \\033[1;33m{install_cmd}\\033[0m"; '
            f'echo ""; '
            f'exec bash',
        ]
        log.warning("ai_terminal_binary_missing", tool_id=tool_id, binary=binary)

    # Create PTY
    import pty

    master_fd, slave_fd = pty.openpty()

    proc = subprocess.Popen(
        shell_cmd,
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        cwd=WORKSPACE_ROOT,
        env=env,
        preexec_fn=os.setsid,
    )
    os.close(slave_fd)

    log.info("ai_terminal_opened", tool_id=tool_id, pid=proc.pid)

    try:
        await asyncio.gather(
            _ws_to_pty(websocket, master_fd),
            _pty_to_ws(master_fd, websocket),
        )
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        os.close(master_fd)
        if proc.poll() is None:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
        log.info("ai_terminal_closed", tool_id=tool_id, pid=proc.pid)


async def _ws_to_pty(websocket: WebSocket, master_fd: int) -> None:
    """Relay data from WebSocket to PTY."""
    try:
        while True:
            data = await websocket.receive_text()

            # Handle resize messages (JSON with "type":"resize")
            if data.startswith("{"):
                try:
                    msg = json.loads(data)
                    if msg.get("type") == "resize":
                        cols = msg.get("cols", 80)
                        rows = msg.get("rows", 24)
                        winsize = struct.pack("HHHH", rows, cols, 0, 0)
                        fcntl.ioctl(master_fd, termios.TIOCSWINSZ, winsize)
                        continue
                except (json.JSONDecodeError, OSError):
                    pass

            os.write(master_fd, data.encode("utf-8"))
    except (WebSocketDisconnect, Exception):
        pass


async def _pty_to_ws(master_fd: int, websocket: WebSocket) -> None:
    """Relay data from PTY to WebSocket."""
    loop = asyncio.get_event_loop()
    try:
        while True:
            data = await loop.run_in_executor(None, _read_pty, master_fd)
            if not data:
                break
            await websocket.send_text(data.decode("utf-8", errors="replace"))
    except (WebSocketDisconnect, Exception):
        pass


def _read_pty(master_fd: int) -> bytes:
    """Read from PTY, blocking in executor."""
    try:
        return os.read(master_fd, 4096)
    except OSError:
        return b""
