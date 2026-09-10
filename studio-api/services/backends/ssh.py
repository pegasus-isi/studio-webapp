"""SSH compute backend — execute on a remote submit host, mirror runs down.

The remote host (e.g. the FABRIC pegasus-htcondor slice's submit node) is a
full Pegasus submit node; this backend is only a transport plus a file
mirror, per docs/fabric-local-studio-plan.md:

- ``exec``/``exec_sync`` run argv remotely over one multiplexed SSH channel,
  translating paths mirror->remote in argv/cwd and remote->mirror in stdout,
  so the studio's run-dir parsing sees local paths that exist.
- A sync loop pulls the remote workspace into the local mirror; every
  file-based reader (scanner glob, event/stampede readers, DAG parsers)
  then works unchanged.
- The mutating tools (pegasus-plan/run/remove) trigger an immediate pull
  before ``exec`` returns, so ``_extract_run_dir``'s isdir check passes the
  moment pegasus-plan's output is parsed — the ordering the Phase 0 shim
  prototype validated against the real slice.

Failure contract: a missing *remote* binary surfaces as ``FileNotFoundError``
(remote shells exit 127 for "command not found"), so call sites keep their
existing "pegasus-plan not found" handling. An unreachable host is an
ordinary nonzero ExecResult whose stderr explains why — the same
degrade-with-error shape the UI already renders. A timeout kills the local
ssh client (procutil's guarantee); the remote command then dies with the
channel (SIGHUP) rather than running unsupervised.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import shutil
import tempfile
import json
import os
from pathlib import Path
import shlex
import subprocess
import time
from dataclasses import asdict, dataclass, fields

import structlog

from services.backends.base import ComputeBackend, ExecResult
from services.procutil import communicate_or_kill

log = structlog.get_logger()

# Tools whose success changes the remote run tree: pull the workspace before
# returning so the studio's post-exec filesystem checks see the new state.
_MUTATING_TOOLS = {"pegasus-plan", "pegasus-run", "pegasus-remove"}

_SYNC_TIMEOUT = 300.0  # rsync of the workspace; generous for first pulls


@dataclass
class SshBackendConfig:
    """One remote submit host, as declared in the backends file."""

    id: str
    host: str
    user: str = "ubuntu"
    label: str = ""
    identity_file: str = ""
    proxy_jump: str = ""  # "user@bastion.example:22" — empty for direct
    proxy_identity_file: str = ""
    remote_home: str = ""  # default /home/<user>
    mirror_root: str = ""  # default ~/.studio/mirrors/<id>
    workspace_subdir: str = "work"
    sync_interval: float = 5.0
    connect_timeout: int = 20
    strict_host_key_checking: str = "accept-new"
    known_hosts_file: str = "~/.ssh/known_hosts"
    default_site: str = "condorpool"
    default_output_site: str = "local"

    @classmethod
    def from_dict(cls, data: dict) -> SshBackendConfig:
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


class SshBackend(ComputeBackend):
    is_remote = True

    def __init__(self, cfg: SshBackendConfig) -> None:
        if cfg.strict_host_key_checking not in ("yes", "accept-new", "no"):
            raise ValueError("strict_host_key_checking must be yes, accept-new, or no")
        if not cfg.id or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-" for c in cfg.id) or cfg.id in (".", ".."):
            raise ValueError("backend id must be a simple name")
        self.id = cfg.id
        self.label = cfg.label or cfg.id
        self.cfg = cfg
        self._sync_task: asyncio.Task | None = None
        self._last_sync: float | None = None
        self._last_error: str = ""
        self._sync_lock = asyncio.Lock()
        self._sync_owner = None
        self._mirror = None

    # -- paths -----------------------------------------------------------

    def remote_home(self) -> str:
        return self.cfg.remote_home or f"/home/{self.cfg.user}"

    def mirror_root(self) -> str:
        root = self.cfg.mirror_root or os.path.join("~", ".studio", "mirrors", self.id)
        return os.path.realpath(os.path.expanduser(root))

    def scan_root(self) -> str:
        return self.mirror_root()

    def plan_defaults(self) -> dict[str, str]:
        return {
            "site": self.cfg.default_site,
            "output_site": self.cfg.default_output_site,
        }

    def workspace_root(self) -> str:
        return os.path.join(self.mirror_root(), self.cfg.workspace_subdir)

    def to_remote(self, path: str) -> str:
        root = self.mirror_root()
        if path == root or path.startswith(root + os.sep):
            return self.remote_home() + path[len(root) :]
        return path

    def to_local(self, path: str) -> str:
        home = self.remote_home()
        if path == home or path.startswith(home + "/"):
            return self.mirror_root() + path[len(home) :]
        return path

    # -- generated ssh config -------------------------------------------

    def ssh_config_path(self) -> str:
        return os.path.expanduser(
            os.path.join("~", ".studio", "ssh", f"{self.id}.conf")
        )

    def _render_ssh_config(self) -> str:
        cfg = self.cfg
        # A changed trust policy must not reuse an older multiplexed channel.
        policy = hashlib.sha256(json.dumps(asdict(cfg), sort_keys=True).encode()).hexdigest()[:8]
        lines = [
            "# Generated by studio-api (services/backends/ssh.py) — edits are",
            "# overwritten; change the backends file instead.",
            f'UserKnownHostsFile "{cfg.known_hosts_file}"',
            f"StrictHostKeyChecking {cfg.strict_host_key_checking}",
            "ServerAliveInterval 30",
            "ServerAliveCountMax 4",
            "",
            f"Host {self.id}",
            f"     HostName {cfg.host}",
            f"     User {cfg.user}",
            f"     ConnectTimeout {cfg.connect_timeout}",
            "     ControlMaster auto",
            # %C hashes (host, port, user) — distinct per backend — and the
            # short ~/.ssh prefix stays under the Unix-socket path limit
            # (~104 bytes on macOS; a longer ~/.studio/... prefix exceeded
            # it and every connection died with "unix_listener: path too
            # long").
            f"     ControlPath ~/.ssh/cm-{policy}-%C",
            "     ControlPersist 10m",
        ]
        if cfg.identity_file:
            lines += [
                f"     IdentityFile {cfg.identity_file}",
                "     IdentitiesOnly yes",
            ]
        if cfg.proxy_jump:
            lines.append(f"     ProxyJump {cfg.proxy_jump}")
            jump = cfg.proxy_jump.rsplit("@", 1)[-1].split(":", 1)[0]
            lines += ["", f"Host {jump}"]
            if "@" in cfg.proxy_jump:
                lines.append(f"     User {cfg.proxy_jump.rsplit('@', 1)[0]}")
            if cfg.proxy_identity_file:
                lines += [
                    f"     IdentityFile {cfg.proxy_identity_file}",
                    "     IdentitiesOnly yes",
                ]
        return "\n".join(lines) + "\n"

    def ensure_ssh_config(self) -> str:
        """Write the generated config if it differs; return its path."""
        path = self.ssh_config_path()
        content = self._render_ssh_config()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        os.makedirs(os.path.expanduser("~/.ssh"), mode=0o700, exist_ok=True)
        try:
            with open(path, encoding="utf-8") as f:
                if f.read() == content:
                    return path
        except OSError:
            pass
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        os.chmod(path, 0o600)
        return path

    def _ssh_argv(self) -> list[str]:
        return [
            "ssh",
            "-F",
            self.ensure_ssh_config(),
            "-o",
            "BatchMode=yes",
            self.id,
        ]

    # -- command transport ----------------------------------------------

    def _remote_command(
        self,
        argv: list[str] | tuple[str, ...],
        cwd: str | None,
        env: dict[str, str] | None = None,
    ) -> str:
        rcwd = self.to_remote(cwd) if cwd else self.remote_home()
        if not rcwd.startswith(self.remote_home()):
            # A cwd outside the mirror has no remote counterpart; run from
            # the remote home rather than failing (matches the shim).
            rcwd = self.remote_home()
        prefix = ""
        if env:
            pairs = " ".join(
                f"{k}={shlex.quote(str(v))}" for k, v in sorted(env.items())
            )
            prefix = f"env {pairs} "
        quoted = " ".join(shlex.quote(self.to_remote(a)) for a in argv)
        return f"cd {shlex.quote(rcwd)} && {prefix}{quoted}"

    def wrap_command(
        self,
        argv: list[str] | tuple[str, ...],
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> tuple[list[str], str | None, dict[str, str] | None]:
        # One ssh argv carrying cwd, env overrides and path translation; the
        # caller's subprocess then needs no cwd/env of its own.
        return (
            [*self._ssh_argv(), self._remote_command(argv, cwd, env)],
            None,
            None,
        )

    async def stream_events(self, run_dir: str):
        """Tail the run's JSONL on the remote host — no mirror-sync latency.

        Probes the accepted filenames in the remote run dir; None when the
        run isn't under this backend's mirror or no JSONL exists (the caller
        then polls the mirrored stampede DB as usual). The stream ends with
        the workflow (or when the ssh channel drops — the client's SSE
        reconnect gets a fresh probe).
        """
        remote_dir = self.to_remote(run_dir)
        if remote_dir == run_dir:
            return None
        probe = (
            "for f in workflow-events.jsonl wfevents.jsonl "
            "monitord-events.jsonl; do "
            f"if [ -f {shlex.quote(remote_dir)}/$f ]; "
            "then echo $f; break; fi; done"
        )
        try:
            result = await self.exec(["sh", "-c", probe], timeout=20)
        except (OSError, asyncio.TimeoutError):
            return None
        name = result.stdout.decode("utf-8", errors="replace").strip()
        if result.returncode != 0 or not name:
            return None
        remote_file = f"{remote_dir}/{name}"

        async def _gen():
            proc = await asyncio.create_subprocess_exec(
                *self._ssh_argv(),
                f"tail -n 0 -F {shlex.quote(remote_file)}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                assert proc.stdout is not None
                while True:
                    line = await proc.stdout.readline()
                    if not line:
                        break
                    text = line.decode("utf-8", errors="replace").strip()
                    if not text:
                        continue
                    try:
                        event = json.loads(text)
                    except json.JSONDecodeError:
                        continue
                    yield event
                    if event.get("event_type") == "workflow_end":
                        break
            finally:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
                with contextlib.suppress(Exception):
                    await proc.wait()

        return _gen()

    def _translate_stdout(self, data: bytes) -> bytes:
        return data.replace(self.remote_home().encode(), self.mirror_root().encode())

    async def _exec(self, argv, *, cwd=None, timeout, env=None) -> ExecResult:
        proc = await asyncio.create_subprocess_exec(
            *self._ssh_argv(), self._remote_command(argv, cwd, env),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await communicate_or_kill(proc, timeout)
        rc = proc.returncode if proc.returncode is not None else -1
        if rc == 127:
            raise FileNotFoundError(argv[0])
        return ExecResult(rc, self._translate_stdout(stdout or b""), stderr or b"")

    async def exec(self, argv, *, cwd=None, timeout, env=None) -> ExecResult:
        if os.path.basename(str(argv[0])) in _MUTATING_TOOLS:
            async with self.project_operation():
                result = await self._exec(argv, cwd=cwd, timeout=timeout, env=env)
                error = await self.pull_workspace()
                if error:
                    raise OSError(f"Command finished but mirror sync failed: {error}")
                if os.path.basename(str(argv[0])) == "pegasus-plan" and cwd:
                    # A concurrent editor save may conflict with files the
                    # planner just wrote. Do not parse an older mirrored run
                    # as the result of this command.
                    await asyncio.to_thread(self._mirror_state().changes, os.path.realpath(cwd))
                return result
        return await self._exec(argv, cwd=cwd, timeout=timeout, env=env)

    def exec_sync(
        self,
        argv: list[str] | tuple[str, ...],
        *,
        cwd: str | None = None,
        timeout: float,
    ) -> ExecResult:
        proc = subprocess.run(
            [*self._ssh_argv(), self._remote_command(argv, cwd)],
            capture_output=True,
            timeout=timeout,
        )
        if proc.returncode == 127:
            raise FileNotFoundError(argv[0])
        return ExecResult(
            returncode=proc.returncode,
            stdout=self._translate_stdout(proc.stdout or b""),
            stderr=proc.stderr or b"",
        )

    # -- mirror sync -----------------------------------------------------

    def _rsync_ssh(self) -> str:
        return f"ssh -F {shlex.quote(self.ensure_ssh_config())} -o BatchMode=yes"

    async def _run_rsync(
        self, src: str, dst: str, extra: list[str] | None = None
    ) -> str:
        """One rsync; returns "" on success, an error string otherwise."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "rsync",
                "-az",
                *(extra or []),
                "-e",
                self._rsync_ssh(),
                src,
                dst,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await communicate_or_kill(proc, _SYNC_TIMEOUT)
        except FileNotFoundError:
            return "rsync not found"
        except asyncio.TimeoutError:
            return "rsync timed out"
        if proc.returncode != 0:
            err = (stderr or b"").decode("utf-8", errors="replace").strip()
            return err.splitlines()[0][:200] if err else "rsync failed"
        return ""

    def _mirror_state(self):
        from services.backends.mirror import MirrorState

        if self._mirror is None:
            identity = json.dumps([self.cfg.host, self.cfg.user, self.remote_home(), self.workspace_root()])
            key = hashlib.sha256(identity.encode()).hexdigest()[:12]
            state_dir = os.path.expanduser(f"~/.studio/backend-sync/{self.id}/{key}")
            self._mirror = MirrorState(self.workspace_root(), state_dir)
        return self._mirror

    @contextlib.asynccontextmanager
    async def project_operation(self):
        # Reentrant only for this task: generate holds the lock across push,
        # exec and refresh, while the background pull waits for the full stage.
        task = asyncio.current_task()
        if self._sync_owner is task:
            async with super().project_operation():
                yield
            return
        async with self._sync_lock:
            self._sync_owner = task
            try:
                async with super().project_operation():
                    yield
            finally:
                self._sync_owner = None

    async def pull_workspace(self) -> str:
        async with self.project_operation():
            state = self._mirror_state()
            os.makedirs(state.snapshot, exist_ok=True)
            remote_ws = self.to_remote(self.workspace_root())
            state.conflicts = []
            error = await self._run_rsync(
                f"{self.id}:{remote_ws}/", str(state.snapshot) + os.sep,
                extra=["--delete", "--exclude", "*.sif"],
            )
            if not error:
                conflicts = await asyncio.to_thread(state.reconcile)
                self._last_sync = time.time()
                if conflicts:
                    error = "Local/remote file conflicts: " + ", ".join(conflicts[:5])
            self._last_error = error
            if error:
                log.warning("backend_pull_failed", backend=self.id, error=error)
            # Conflicts are visible in status and block only the affected
            # project, not unrelated workflows on this backend.
            return "" if not error or state.conflicts else error

    async def refresh_project(self, project_dir: str) -> None:
        error = await self.pull_workspace()
        if error:
            raise RuntimeError(f"Could not refresh generated files: {error}")
        await asyncio.to_thread(self._mirror_state().changes, os.path.realpath(project_dir))

    async def push_project(self, project_dir: str) -> None:
        ws = self.workspace_root()
        real = os.path.realpath(project_dir)
        if real != ws and not real.startswith(ws + os.sep):
            raise ValueError(f"project dir {project_dir} is outside the backend workspace")
        async with self.project_operation():
            mkdir = await self._exec(["mkdir", "-p", self.to_remote(real)], timeout=30)
            if mkdir.returncode:
                raise RuntimeError("Could not create remote workspace")
            error = await self.pull_workspace()
            if error:
                raise RuntimeError(f"Project sync failed: {error}")
            state = self._mirror_state()
            changed, deleted = await asyncio.to_thread(state.changes, real)
            # Freeze the upload separately from the editor's working copy. An
            # edit during rsync must remain dirty for the next operation.
            with tempfile.TemporaryDirectory(dir=state.state_dir, prefix="upload-") as upload:
                def freeze_upload():
                    for name in changed:
                        source = state.workspace / name
                        target = os.path.join(upload, name)
                        os.makedirs(os.path.dirname(target), exist_ok=True)
                        shutil.copy2(source, target, follow_symlinks=False)
                    return state.inventory(Path(upload))

                uploaded = await asyncio.to_thread(freeze_upload)
                if changed:
                    file_list = os.path.join(upload, ".files")
                    with open(file_list, "wb") as stream:
                        stream.write(b"".join(os.fsencode(n) + b"\0" for n in changed))
                    error = await self._run_rsync(
                        upload + os.sep, f"{self.id}:{self.to_remote(ws)}/",
                        extra=["--checksum", "--from0", "--files-from", file_list],
                    )
                    if error:
                        raise RuntimeError(f"Project push failed: {error}")
                if deleted:
                    await self._delete_remote([os.path.join(ws, n) for n in deleted], tree=False)
                await asyncio.to_thread(state.acknowledge, uploaded, deleted)

    async def _delete_remote(self, paths: list[str], *, tree: bool):
        # Resolve on the execution host too: a symlink in a parent must not
        # turn a workspace deletion into deletion of another directory.
        script = """import os, shutil, sys
root = os.path.realpath(sys.argv[1])
for raw in sys.argv[3:]:
    path = os.path.join(os.path.realpath(os.path.dirname(raw)), os.path.basename(raw))
    if not path.startswith(root + os.sep):
        raise ValueError('Deletion is outside the backend workspace')
    if os.path.islink(path):
        os.unlink(path)
    elif sys.argv[2] == 'tree':
        if os.path.isdir(path): shutil.rmtree(path)
    elif os.path.lexists(path):
        os.unlink(path)
"""
        result = await self._exec(
            ["python3", "-c", script, self.to_remote(self.workspace_root()),
             "tree" if tree else "file", *(self.to_remote(p) for p in paths)], timeout=60,
        )
        if result.returncode:
            raise RuntimeError("Remote deletion failed: " + result.stderr.decode(errors="replace")[:300])

    async def deletion_snapshot(self, path: str) -> str:
        real = os.path.realpath(path)
        if not real.startswith(self.workspace_root() + os.sep):
            raise ValueError("Deletion is outside the backend workspace")
        error = await self.pull_workspace()
        if error:
            raise RuntimeError(f"Could not inspect remote tree before deletion: {error}")
        return str(self._mirror_state().snapshot / os.path.relpath(real, self.workspace_root()))

    async def delete_tree(self, path: str):
        real = os.path.realpath(path)
        if not real.startswith(self.workspace_root() + os.sep):
            raise ValueError("Deletion is outside the backend workspace")
        async with self.project_operation():
            await self._delete_remote([real], tree=True)
            if os.path.exists(real):
                await asyncio.to_thread(shutil.rmtree, real)
            state = self._mirror_state()
            cached = state.snapshot / os.path.relpath(real, self.workspace_root())
            if cached.exists():
                await asyncio.to_thread(shutil.rmtree, cached)
            await asyncio.to_thread(state.forget, real)

    async def _sync_loop(self) -> None:
        while True:
            await asyncio.sleep(self.cfg.sync_interval)
            try:
                await self.pull_workspace()
            except Exception as e:  # never let the loop die
                self._last_error = str(e)
                log.warning("backend_sync_error", backend=self.id, error=str(e))

    # -- lifecycle -------------------------------------------------------

    async def start(self) -> None:
        os.makedirs(self.workspace_root(), exist_ok=True)
        self.ensure_ssh_config()
        await self.pull_workspace()  # best-effort; error lands in status()
        if self._sync_task is None or self._sync_task.done():
            self._sync_task = asyncio.create_task(self._sync_loop())
        log.info("backend_started", backend=self.id, host=self.cfg.host)

    async def stop(self) -> None:
        if self._sync_task is not None:
            self._sync_task.cancel()
            try:
                await self._sync_task
            except (asyncio.CancelledError, Exception):
                pass
            self._sync_task = None
        log.info("backend_stopped", backend=self.id)

    async def probe(self) -> str | None:
        """Cheap reachability check used to gate switching to this backend."""
        try:
            result = await self.exec(["true"], timeout=20)
        except FileNotFoundError:
            return "remote shell missing `true` (unexpected)"
        except asyncio.TimeoutError:
            return "connection timed out"
        except OSError as e:
            return str(e)
        if result.returncode != 0:
            err = result.stderr.decode("utf-8", errors="replace").strip()
            return err.splitlines()[0][:200] if err else "ssh failed"
        return None

    def status(self) -> dict:
        syncing = self._sync_task is not None and not self._sync_task.done()
        return {
            "available": syncing and not self._last_error,
            "error": self._last_error or ("" if syncing else "backend not started"),
            "type": "ssh",
            "host": self.cfg.host,
            "last_sync": self._last_sync,
            "sync_interval": self.cfg.sync_interval,
        }
