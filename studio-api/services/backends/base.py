"""Compute backend abstraction — where and how workflow commands execute.

The studio's entire coupling to the execution environment reduces to two
primitives (see docs/fabric-local-studio-plan.md):

1. a command transport — how ``pegasus-*`` / ``condor_*`` argv runs, and
2. a run-tree provider — where the watched file tree (braindump.yml, event
   logs, stampede DBs, kickstart outputs) lives on the *local* filesystem.

Everything else (event parsing, DAG rendering, status resolution) reads local
files and stays backend-agnostic. A remote backend therefore does not
reimplement monitoring: it executes commands elsewhere and mirrors the remote
run tree into ``scan_root()``, and the existing readers work unchanged.

Exception contract for ``exec``/``exec_sync``: callers keep their existing
``except FileNotFoundError`` ("binary not found") and timeout handling, so
implementations must surface those *same* exception types rather than
inventing an error-result shape — a remote backend maps its own failure modes
(exit 127, dropped connection) onto them.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from contextlib import asynccontextmanager


@dataclass
class ExecResult:
    """Outcome of a completed (not timed-out) backend command."""

    returncode: int
    stdout: bytes
    stderr: bytes


class ComputeBackend(ABC):
    """One place workflows can execute (this machine, an SSH submit host, …)."""

    id: str = ""
    label: str = ""
    # True when commands execute somewhere other than this host — call sites
    # that touch the local filesystem around a command (verify a built image,
    # report free disk) branch on this.
    is_remote: bool = False

    # -- primitive 1: command transport ---------------------------------

    @abstractmethod
    async def exec(
        self,
        argv: list[str] | tuple[str, ...],
        *,
        cwd: str | None = None,
        timeout: float,
        env: dict[str, str] | None = None,
    ) -> ExecResult:
        """Run ``argv`` where this backend executes workflows.

        ``env`` overlays only the supplied variables on the execution host's
        environment; it must not contain a copy of the API server's environment.

        Raises ``FileNotFoundError`` if the binary is absent and
        ``asyncio.TimeoutError`` on timeout (with the work actually stopped,
        per ``procutil.communicate_or_kill``'s guarantee).
        """

    @abstractmethod
    def exec_sync(
        self,
        argv: list[str] | tuple[str, ...],
        *,
        cwd: str | None = None,
        timeout: float,
    ) -> ExecResult:
        """Blocking variant for sync call paths (run in a thread executor).

        Raises ``FileNotFoundError``, ``subprocess.TimeoutExpired`` or
        ``OSError`` — the set ``_pegasus_status`` has always handled.
        """

    def wrap_command(
        self,
        argv: list[str] | tuple[str, ...],
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> tuple[list[str], str | None, dict[str, str] | None]:
        """Rewrite (argv, cwd, env-overrides) for a caller-owned subprocess.

        For call sites that need line-by-line streaming (the image builder)
        and therefore spawn their own subprocess instead of using ``exec``.
        Local: identity — the caller merges the overrides over os.environ.
        Remote: a single ssh argv embedding cwd, env and path translation,
        with ``(cwd=None, env=None)`` since the wrapping already carries
        them.
        """
        return list(argv), cwd, dict(env) if env else None

    def plan_defaults(self) -> dict[str, str]:
        """Default pegasus-plan sites for this backend's Plan/Run forms.

        Which sites exist is the project's site catalog's business; these are
        just the form's starting values, so a backend whose pool uses a
        differently-named site doesn't make every Plan start out wrong.
        """
        return {"site": "condorpool", "output_site": "local"}

    async def stream_events(self, run_dir: str):
        """Live event stream for a run, or ``None`` to use local file logic.

        A remote backend can tail the run's JSONL where monitord writes it,
        skipping mirror-sync latency. ``None`` means "no live source here —
        tail/poll the local (mirror) files as always"; otherwise an async
        generator of event dicts that ends when the workflow does.
        """
        return None

    # -- primitive 2: run-tree provider ---------------------------------

    @abstractmethod
    def scan_root(self) -> str:
        """Local directory under which this backend's run dirs appear.

        Recomputed per call — never cache at import time (the frozen
        ``SCAN_ROOT`` constant this replaces broke under the test harness;
        see the note that used to live in main.py's lifespan).
        """

    @abstractmethod
    def workspace_root(self) -> str:
        """Local root of this backend's project workspace (…/workflows)."""

    def workflows_dir(self) -> str:
        import os

        return os.path.join(self.workspace_root(), "workflows")

    def to_remote(self, path: str) -> str:
        """Translate a local (mirror) path to the backend's own path."""
        return path

    def to_local(self, path: str) -> str:
        """Translate a backend path to its local (mirror) equivalent."""
        return path

    # -- sync + lifecycle (no-ops for a local backend) -------------------

    async def push_project(self, project_dir: str) -> None:
        """Make a local project directory available where commands run."""

    async def refresh_project(self, project_dir: str) -> None:
        """Make files generated on the backend visible before the next stage."""

    async def deletion_snapshot(self, path: str) -> str:
        """Current files on the execution host, for cancellation before deletion."""
        return path

    async def delete_tree(self, path: str) -> None:
        """Remove a remote tree and its mirror; local deletion uses NFS retries."""
        raise NotImplementedError

    @asynccontextmanager
    async def project_operation(self):
        """Keep selection stable for one operation; SSH also serializes sync."""
        from services.backends import using_backend

        with using_backend(self):
            yield

    async def start(self) -> None:
        """Open transports / start sync loops."""

    async def stop(self) -> None:
        """Tear down whatever ``start`` opened."""

    async def probe(self) -> str | None:
        """Active reachability check used before switching to this backend.

        ``None`` means healthy; a short error string otherwise. The local
        backend is trivially healthy.
        """
        return None

    def status(self) -> dict:
        """Reachability in the same ``{available, error}`` shape
        ``condor_pool`` summaries use, so the UI needs nothing new."""
        return {"available": True, "error": ""}


@dataclass
class RunRef:
    """A resolved run: which backend owns it and its local run directory.

    Ownership is independent of the current Settings selection.
    """

    backend: ComputeBackend
    run_dir: str
