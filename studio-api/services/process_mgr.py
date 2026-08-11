"""Process manager for AI tool subprocesses and PTY sessions."""

from __future__ import annotations

import asyncio
import os
import signal
import socket
import subprocess
from dataclasses import dataclass, field
from typing import Any

import structlog

log = structlog.get_logger()

WORKSPACE_ROOT = os.environ.get(
    "STUDIO_WORKSPACE_DIR",
    os.path.join(os.environ.get("HOME", "/tmp"), "work"),
)


@dataclass
class ProcessInfo:
    tool_id: str
    pid: int
    process: subprocess.Popen[bytes] | None = None
    master_fd: int | None = None
    web_port: int | None = None
    kind: str = "terminal"  # "terminal" or "web"


class ProcessManager:
    """Manages AI tool subprocesses."""

    def __init__(self) -> None:
        self._processes: dict[str, ProcessInfo] = {}

    def get(self, tool_id: str) -> ProcessInfo | None:
        return self._processes.get(tool_id)

    def is_running(self, tool_id: str) -> bool:
        info = self._processes.get(tool_id)
        if info is None:
            return False
        if info.process is None:
            return False
        return info.process.poll() is None

    def start_terminal_tool(
        self,
        tool_id: str,
        binary: str,
        env: dict[str, str] | None = None,
    ) -> ProcessInfo:
        """Start a terminal-based AI tool with a PTY.

        Returns ProcessInfo with master_fd for WebSocket relay.

        Deprecated / unused: terminal tools' PTYs are owned by the WebSocket
        session (services/terminal_sessions.py). /api/tools/{id}/start no longer
        calls this — it produced a duplicate, unread PTY child that blocked once
        its buffer filled. Kept only for reference.
        """
        import pty

        if self.is_running(tool_id):
            return self._processes[tool_id]

        master_fd, slave_fd = pty.openpty()

        from services.tool_env import with_tool_paths

        tool_env = with_tool_paths(os.environ.copy())
        if env:
            tool_env.update(env)
        tool_env["TERM"] = "xterm-256color"

        proc = subprocess.Popen(
            [binary],
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            cwd=WORKSPACE_ROOT,
            env=tool_env,
            preexec_fn=os.setsid,
        )
        os.close(slave_fd)

        info = ProcessInfo(
            tool_id=tool_id,
            pid=proc.pid,
            process=proc,
            master_fd=master_fd,
            kind="terminal",
        )
        self._processes[tool_id] = info
        log.info("terminal_tool_started", tool_id=tool_id, pid=proc.pid)
        return info

    def start_web_tool(
        self,
        tool_id: str,
        command: str,
        port: int,
        env: dict[str, str] | None = None,
    ) -> ProcessInfo:
        """Start a web-based AI tool subprocess on a given port."""
        if self.is_running(tool_id):
            return self._processes[tool_id]

        tool_env = os.environ.copy()
        if env:
            tool_env.update(env)

        # Substitute {port} in command
        cmd = command.replace("{port}", str(port))

        proc = subprocess.Popen(
            cmd.split(),
            cwd=WORKSPACE_ROOT,
            env=tool_env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            preexec_fn=os.setsid,
        )

        info = ProcessInfo(
            tool_id=tool_id,
            pid=proc.pid,
            process=proc,
            web_port=port,
            kind="web",
        )
        self._processes[tool_id] = info
        log.info("web_tool_started", tool_id=tool_id, pid=proc.pid, port=port)
        return info

    def stop_tool(self, tool_id: str) -> bool:
        """Stop a running tool process."""
        info = self._processes.pop(tool_id, None)
        if info is None:
            return False

        if info.master_fd is not None:
            try:
                os.close(info.master_fd)
            except OSError:
                pass

        if info.process and info.process.poll() is None:
            try:
                os.killpg(os.getpgid(info.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
            try:
                info.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(info.pid), signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass

        log.info("tool_stopped", tool_id=tool_id)
        return True

    async def cleanup_all(self) -> None:
        """Stop all running tool processes. Called during lifespan shutdown."""
        for tool_id in list(self._processes):
            self.stop_tool(tool_id)
        log.info("all_tools_cleaned_up")


def find_free_port() -> int:
    """Find a free TCP port (probe on loopback — tools bind 127.0.0.1)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# Module-level singleton
process_manager = ProcessManager()
