"""Tests for the compute-backend abstraction (services/backends)."""

from __future__ import annotations

import asyncio
import os

import pytest


def test_registry_has_local_backend():
    from services.backends import all_backends, get_backend

    assert [b.id for b in all_backends()] == ["local"]
    assert get_backend("local") is not None
    assert get_backend("nope") is None


def test_active_backend_defaults_to_local_and_survives_bad_env(monkeypatch):
    from services.backends import active_backend

    monkeypatch.delenv("STUDIO_EXECUTION_BACKEND", raising=False)
    assert active_backend().id == "local"
    # An unknown id must fall back to local, not fail every workflow call —
    # the same tolerance the chat-engine default has for a bad env value.
    monkeypatch.setenv("STUDIO_EXECUTION_BACKEND", "does-not-exist")
    assert active_backend().id == "local"


def test_local_roots_recompute_per_call(monkeypatch):
    """The frozen-at-import SCAN_ROOT bug class must stay dead: changing the
    env between calls changes the answer, with no reimport."""
    from services.backends import active_backend

    b = active_backend()
    monkeypatch.setenv("STUDIO_SCAN_DIR", "/tmp/scan-one")
    monkeypatch.setenv("STUDIO_WORKSPACE_DIR", "/tmp/ws-one")
    assert b.scan_root() == "/tmp/scan-one"
    assert b.workspace_root() == "/tmp/ws-one"
    assert b.workflows_dir() == os.path.join("/tmp/ws-one", "workflows")
    monkeypatch.setenv("STUDIO_SCAN_DIR", "/tmp/scan-two")
    assert b.scan_root() == "/tmp/scan-two"


def test_scanner_roots_resolve_through_backend(monkeypatch):
    """workflow_scanner's module-level helpers defer to the active backend."""
    from services import workflow_scanner

    monkeypatch.setenv("STUDIO_SCAN_DIR", "/tmp/backend-root")
    monkeypatch.setenv("STUDIO_WORKSPACE_DIR", "/tmp/backend-ws")
    assert workflow_scanner.scan_root() == "/tmp/backend-root"
    assert workflow_scanner.workspace_root() == "/tmp/backend-ws"
    assert workflow_scanner.workflows_dir() == os.path.join(
        "/tmp/backend-ws", "workflows"
    )


def test_local_path_translation_is_identity():
    from services.backends import active_backend

    b = active_backend()
    assert b.to_remote("/a/b") == "/a/b"
    assert b.to_local("/a/b") == "/a/b"


def test_local_exec_runs_and_captures(tmp_path):
    from services.backends import active_backend

    result = asyncio.run(
        active_backend().exec(
            ["/bin/sh", "-c", "pwd && echo oops >&2"],
            cwd=str(tmp_path),
            timeout=10,
        )
    )
    assert result.returncode == 0
    assert result.stdout.decode().strip() == os.path.realpath(str(tmp_path))
    assert b"oops" in result.stderr


def test_local_exec_preserves_exception_contract():
    """Callers keep their existing ``except FileNotFoundError`` handling
    ("pegasus-plan not found"), so exec must raise it, not wrap it."""
    from services.backends import active_backend

    with pytest.raises(FileNotFoundError):
        asyncio.run(active_backend().exec(["definitely-not-a-binary-xyz"], timeout=5))


def test_local_exec_sync_runs():
    from services.backends import active_backend

    result = active_backend().exec_sync(["/bin/echo", "hi"], timeout=5)
    assert result.returncode == 0
    assert result.stdout.strip() == b"hi"


def test_local_status_shape():
    """The condor_pool-style {available, error} shape the UI already renders."""
    from services.backends import active_backend

    s = active_backend().status()
    assert s["available"] is True
    assert "error" in s
