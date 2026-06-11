"""WebSocket PTY for plain bash terminal sessions."""

from __future__ import annotations

import asyncio
import fcntl
import os
import pty
import signal
import struct
import subprocess
import termios

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

log = structlog.get_logger()

router = APIRouter(tags=["terminal"])

WORKSPACE_ROOT = os.environ.get(
    "STUDIO_WORKSPACE_DIR",
    os.path.join(os.environ.get("HOME", "/tmp"), "work"),
)


@router.websocket("/ws/terminal")
async def bash_terminal(websocket: WebSocket) -> None:
    """WebSocket PTY for a plain bash shell."""
    await websocket.accept()

    master_fd, slave_fd = pty.openpty()

    env = os.environ.copy()
    env["TERM"] = "xterm-256color"

    proc = subprocess.Popen(
        ["/bin/bash"],
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        cwd=WORKSPACE_ROOT,
        env=env,
        preexec_fn=os.setsid,
    )
    os.close(slave_fd)

    log.info("bash_terminal_opened", pid=proc.pid)

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
        log.info("bash_terminal_closed", pid=proc.pid)


async def _ws_to_pty(websocket: WebSocket, master_fd: int) -> None:
    """Relay data from WebSocket to PTY master fd."""
    import json

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
    """Relay data from PTY master fd to WebSocket."""
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
