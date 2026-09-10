"""Compute-backend registry and selection.

Follows the lazy-registry idiom from ``knowledge/adapters.py``. The local
backend always exists; remote (SSH) backends are declared in a JSON file —
``STUDIO_BACKENDS_FILE``, default ``~/.studio/backends.json`` — shaped as

    {"backends": [{"id": "fabric-pegasus-htcondor", "type": "ssh",
                   "host": "...", "user": "ubuntu", ...}]}

(see ``SshBackendConfig`` for the fields). A malformed entry is logged and
skipped rather than failing startup.

Selection mirrors the chat-engine dispatch: the ``execution_backend`` DB
setting (applied at startup and by ``PUT /api/settings/execution-backend``
via :func:`set_active_backend_id`) wins, then ``STUDIO_EXECUTION_BACKEND``,
then local. An unknown id falls back to local rather than failing every
workflow call. ``active_backend()`` is safe from sync code (thread-pool
scanner calls included) — selection is a module variable, never a DB read.
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from contextvars import ContextVar

import structlog

from services.backends.base import ComputeBackend, ExecResult, RunRef

__all__ = [
    "ComputeBackend",
    "ExecResult",
    "RunRef",
    "active_backend",
    "backend_for_path",
    "using_backend",
    "all_backends",
    "get_backend",
    "refresh_registry",
    "set_active_backend_id",
]

log = structlog.get_logger()

_REGISTRY: dict[str, ComputeBackend] | None = None
_ACTIVE_ID: str | None = None
_BOUND: ContextVar[ComputeBackend | None] = ContextVar("compute_backend", default=None)


def _backends_file() -> str:
    return os.environ.get("STUDIO_BACKENDS_FILE") or os.path.expanduser(
        os.path.join("~", ".studio", "backends.json")
    )


def _load_configured() -> list[ComputeBackend]:
    path = _backends_file()
    if not os.path.isfile(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        log.warning("backends_file_unreadable", path=path, error=str(e))
        return []
    entries = data.get("backends") if isinstance(data, dict) else data
    if not isinstance(entries, list):
        log.warning("backends_file_bad_shape", path=path)
        return []

    from services.backends.ssh import SshBackend, SshBackendConfig

    backends: list[ComputeBackend] = []
    for entry in entries:
        try:
            if not isinstance(entry, dict):
                raise ValueError("entry is not an object")
            kind = entry.get("type", "ssh")
            if kind != "ssh":
                raise ValueError(f"unknown backend type {kind!r}")
            if entry.get("id") == "local":
                raise ValueError('"local" is reserved')
            backends.append(SshBackend(SshBackendConfig.from_dict(entry)))
        except Exception as e:
            log.warning(
                "backend_entry_skipped",
                path=path,
                entry_id=entry.get("id") if isinstance(entry, dict) else None,
                error=str(e),
            )
    return backends


def _build_registry() -> dict[str, ComputeBackend]:
    # Imports inside the function to keep import order flexible, like
    # knowledge/adapters.py does.
    from services.backends.local import LocalBackend

    local = LocalBackend()
    registry = {local.id: local}
    for backend in _load_configured():
        registry[backend.id] = backend
    return registry


def _registry() -> dict[str, ComputeBackend]:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = _build_registry()
    return _REGISTRY


def refresh_registry() -> None:
    """Drop the cached registry so the backends file is re-read.

    Running backends are kept: entries whose id survives the reload carry
    their instance over, so an active sync loop isn't orphaned by an
    unrelated edit to the file.
    """
    global _REGISTRY
    old = _REGISTRY or {}
    _REGISTRY = None
    fresh = _registry()
    for bid, backend in old.items():
        if bid in fresh and type(fresh[bid]) is type(backend):
            fresh[bid] = backend


def all_backends() -> list[ComputeBackend]:
    return list(_registry().values())


def get_backend(backend_id: str) -> ComputeBackend | None:
    return _registry().get(backend_id)


def set_active_backend_id(backend_id: str | None) -> None:
    """Apply a selection (from the DB setting or the settings endpoint)."""
    global _ACTIVE_ID
    _ACTIVE_ID = backend_id or None


def active_backend() -> ComputeBackend:
    """The backend workflow operations run against, resolved per call."""
    if bound := _BOUND.get():
        return bound
    wanted = _ACTIVE_ID or os.environ.get("STUDIO_EXECUTION_BACKEND") or "local"
    backend = get_backend(wanted)
    if backend is None:
        backend = _registry()["local"]
    return backend


@contextmanager
def using_backend(backend: ComputeBackend):
    """Bind an operation and its child tasks to their original backend."""
    token = _BOUND.set(backend)
    try:
        yield
    finally:
        _BOUND.reset(token)


def backend_for_path(path: str) -> ComputeBackend:
    """Resolve run ownership from its mirror, never from the selected tab."""
    real = os.path.realpath(path)
    candidates = [b for b in all_backends() if b.is_remote]
    if active_backend().is_remote:
        candidates.append(active_backend())
    for backend in sorted(candidates, key=lambda b: len(b.scan_root()), reverse=True):
        root = os.path.realpath(backend.scan_root())
        if real == root or real.startswith(root + os.sep):
            return backend
    # An unconfigured mirror must not be treated as a local submit directory.
    mirrors = os.path.realpath(os.path.expanduser("~/.studio/mirrors"))
    if real.startswith(mirrors + os.sep):
        raise ValueError("This run's compute backend is no longer configured")
    return _registry()["local"]
