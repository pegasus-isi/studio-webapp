"""The local compute backend — today's behavior, behind the interface."""

from __future__ import annotations

import asyncio
import os
import subprocess

from services.backends.base import ComputeBackend, ExecResult
from services.procutil import communicate_or_kill


class LocalBackend(ComputeBackend):
    """Execute on this host; the run tree is the real filesystem."""

    id = "local"
    label = "This machine"

    async def exec(
        self,
        argv: list[str] | tuple[str, ...],
        *,
        cwd: str | None = None,
        timeout: float,
        env: dict[str, str] | None = None,
    ) -> ExecResult:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env={**os.environ, **env} if env is not None else None,
        )
        stdout, stderr = await communicate_or_kill(proc, timeout)
        return ExecResult(
            returncode=proc.returncode if proc.returncode is not None else -1,
            stdout=stdout or b"",
            stderr=stderr or b"",
        )

    def exec_sync(
        self,
        argv: list[str] | tuple[str, ...],
        *,
        cwd: str | None = None,
        timeout: float,
    ) -> ExecResult:
        proc = subprocess.run(list(argv), capture_output=True, cwd=cwd, timeout=timeout)
        return ExecResult(
            returncode=proc.returncode,
            stdout=proc.stdout or b"",
            stderr=proc.stderr or b"",
        )

    def scan_root(self) -> str:
        # Whole home by default so runs submitted outside ~/work are found.
        return os.environ.get("STUDIO_SCAN_DIR") or os.environ.get("HOME", "/tmp")

    def workspace_root(self) -> str:
        return os.environ.get("STUDIO_WORKSPACE_DIR") or os.path.join(
            os.environ.get("HOME", "/tmp"), "work"
        )
