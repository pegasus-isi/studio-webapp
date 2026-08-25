"""Tests for the SSH compute backend, registry file, and selection endpoints."""

from __future__ import annotations

import asyncio
import json
import os

import pytest


def _make_backend(tmp_path, **overrides):
    from services.backends.ssh import SshBackend, SshBackendConfig

    cfg = {
        "id": "fabric-test",
        "host": "2001:db8::1",
        "user": "ubuntu",
        "identity_file": "~/.ssh/sliver",
        "proxy_jump": "user0001@bastion.example:22",
        "proxy_identity_file": "~/.ssh/bastion",
        "mirror_root": str(tmp_path / "mirror"),
    }
    cfg.update(overrides)
    return SshBackend(SshBackendConfig.from_dict(cfg))


class _FakeProc:
    def __init__(self, rc=0, out=b"", err=b""):
        self.returncode = rc
        self._out = out
        self._err = err

    async def communicate(self):
        return (self._out, self._err)


def test_path_translation_roundtrip(tmp_path):
    b = _make_backend(tmp_path)
    mirror = str(tmp_path / "mirror")
    local = os.path.join(mirror, "work", "workflows", "p", "run0001")
    assert b.to_remote(local) == "/home/ubuntu/work/workflows/p/run0001"
    assert b.to_local("/home/ubuntu/work/workflows/p/run0001") == local
    # Paths outside either prefix pass through untouched.
    assert b.to_remote("/etc/hosts") == "/etc/hosts"
    assert b.to_local("/etc/hosts") == "/etc/hosts"


def test_remote_command_translates_argv_and_cwd(tmp_path):
    b = _make_backend(tmp_path)
    mirror = str(tmp_path / "mirror")
    proj = os.path.join(mirror, "work", "workflows", "p")
    cmd = b._remote_command(
        ["pegasus-plan", "--submit", os.path.join(proj, "workflow.yml")],
        cwd=proj,
    )
    assert "cd /home/ubuntu/work/workflows/p &&" in cmd
    assert "/home/ubuntu/work/workflows/p/workflow.yml" in cmd
    assert mirror not in cmd
    # A cwd outside the mirror falls back to the remote home.
    assert b._remote_command(["true"], cwd="/somewhere/else").startswith(
        "cd /home/ubuntu &&"
    )


def test_ssh_config_render(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    b = _make_backend(tmp_path)
    path = b.ensure_ssh_config()
    content = open(path).read()
    assert "Host fabric-test" in content
    assert "HostName 2001:db8::1" in content
    assert "ProxyJump user0001@bastion.example:22" in content
    assert "Host bastion.example" in content
    assert "ControlMaster auto" in content
    # Idempotent: a second call leaves the same content.
    assert b.ensure_ssh_config() == path
    assert open(path).read() == content


def test_exec_translates_output_and_builds_ssh_argv(tmp_path, monkeypatch):
    from services.backends import ssh as ssh_mod

    monkeypatch.setenv("HOME", str(tmp_path))
    b = _make_backend(tmp_path)
    captured = {}

    async def fake_exec(*argv, **kw):
        captured["argv"] = argv
        return _FakeProc(rc=0, out=b"run dir /home/ubuntu/work/x/run0001\n")

    monkeypatch.setattr(ssh_mod.asyncio, "create_subprocess_exec", fake_exec)
    result = asyncio.run(b.exec(["pegasus-status", "-l", "x"], timeout=10))
    assert result.returncode == 0
    assert str(tmp_path / "mirror").encode() in result.stdout
    assert b"/home/ubuntu" not in result.stdout
    assert captured["argv"][0] == "ssh"
    assert "fabric-test" in captured["argv"]


def test_exec_maps_remote_127_to_filenotfound(tmp_path, monkeypatch):
    from services.backends import ssh as ssh_mod

    monkeypatch.setenv("HOME", str(tmp_path))
    b = _make_backend(tmp_path)

    async def fake_exec(*argv, **kw):
        return _FakeProc(rc=127, err=b"bash: pegasus-plan: command not found")

    monkeypatch.setattr(ssh_mod.asyncio, "create_subprocess_exec", fake_exec)
    with pytest.raises(FileNotFoundError):
        asyncio.run(b.exec(["pegasus-plan", "--submit"], timeout=10))


def test_exec_mutating_tool_pulls_workspace(tmp_path, monkeypatch):
    from services.backends import ssh as ssh_mod

    monkeypatch.setenv("HOME", str(tmp_path))
    b = _make_backend(tmp_path)
    pulled = {"n": 0}

    async def fake_exec(*argv, **kw):
        return _FakeProc(rc=0, out=b"ok")

    async def fake_pull():
        pulled["n"] += 1
        return ""

    monkeypatch.setattr(ssh_mod.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(b, "pull_workspace", fake_pull)
    asyncio.run(b.exec(["pegasus-plan", "--submit"], timeout=10))
    assert pulled["n"] == 1
    asyncio.run(b.exec(["pegasus-status", "-l", "x"], timeout=10))
    assert pulled["n"] == 1  # non-mutating tool: no pull


def test_exec_sync_maps_127(tmp_path, monkeypatch):
    from services.backends import ssh as ssh_mod

    monkeypatch.setenv("HOME", str(tmp_path))
    b = _make_backend(tmp_path)

    class _P:
        returncode = 127
        stdout = b""
        stderr = b""

    monkeypatch.setattr(ssh_mod.subprocess, "run", lambda *a, **k: _P())
    with pytest.raises(FileNotFoundError):
        b.exec_sync(["pegasus-status", "-l", "x"], timeout=5)


def test_push_project_rejects_paths_outside_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    b = _make_backend(tmp_path)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    with pytest.raises(ValueError):
        asyncio.run(b.push_project(str(outside)))


@pytest.fixture
def tmp_backends(tmp_path, monkeypatch):
    """Registry loaded from a temp backends file; fully undone afterwards."""
    from services import backends

    f = tmp_path / "backends.json"
    f.write_text(
        json.dumps(
            {
                "backends": [
                    {
                        "id": "fabric-test",
                        "type": "ssh",
                        "host": "2001:db8::1",
                        "mirror_root": str(tmp_path / "mirror"),
                    },
                    {"id": "local", "type": "ssh", "host": "x"},  # reserved
                    {"id": "weird", "type": "slurm", "host": "y"},  # unknown
                    "not-an-object",
                ]
            }
        )
    )
    monkeypatch.setenv("STUDIO_BACKENDS_FILE", str(f))
    backends.refresh_registry()
    yield backends
    backends.set_active_backend_id(None)
    os.environ.pop("STUDIO_BACKENDS_FILE", None)
    backends.refresh_registry()


def test_registry_loads_backends_file(tmp_backends):
    ids = [b.id for b in tmp_backends.all_backends()]
    assert "local" in ids
    assert "fabric-test" in ids
    # Reserved / unknown-type / malformed entries are skipped, not fatal.
    assert "weird" not in ids
    assert type(tmp_backends.get_backend("local")).__name__ == "LocalBackend"


def test_active_backend_selection(tmp_backends, monkeypatch):
    assert tmp_backends.active_backend().id == "local"
    monkeypatch.setenv("STUDIO_EXECUTION_BACKEND", "fabric-test")
    assert tmp_backends.active_backend().id == "fabric-test"
    # The applied selection (DB setting via set_active_backend_id) wins.
    tmp_backends.set_active_backend_id("local")
    assert tmp_backends.active_backend().id == "local"
    tmp_backends.set_active_backend_id(None)
    assert tmp_backends.active_backend().id == "fabric-test"
    # Unknown ids fall back to local instead of failing every call.
    tmp_backends.set_active_backend_id("gone")
    assert tmp_backends.active_backend().id == "local"


def test_execution_backend_endpoints(client, tmp_backends, monkeypatch):
    from services.backends.ssh import SshBackend

    resp = client.get("/api/settings/execution-backend")
    assert resp.status_code == 200
    data = resp.json()
    assert data["backend"] == "local"
    assert {b["id"] for b in data["backends"]} >= {"local", "fabric-test"}

    assert (
        client.put(
            "/api/settings/execution-backend", json={"backend": "nope"}
        ).status_code
        == 404
    )

    # A target failing its probe is refused (409), selection unchanged.
    async def probe_fail(self):
        return "connection timed out"

    monkeypatch.setattr(SshBackend, "probe", probe_fail)
    resp = client.put(
        "/api/settings/execution-backend", json={"backend": "fabric-test"}
    )
    assert resp.status_code == 409
    assert client.get("/api/settings/execution-backend").json()["backend"] == ("local")

    # A healthy target switches; start/stop stubbed so no real ssh runs.
    async def probe_ok(self):
        return None

    async def lifecycle_noop(self):
        return None

    monkeypatch.setattr(SshBackend, "probe", probe_ok)
    monkeypatch.setattr(SshBackend, "start", lifecycle_noop)
    monkeypatch.setattr(SshBackend, "stop", lifecycle_noop)
    resp = client.put(
        "/api/settings/execution-backend", json={"backend": "fabric-test"}
    )
    assert resp.status_code == 200
    assert resp.json()["backend"] == "fabric-test"

    # Switch back to local (also proves LocalBackend probe passes).
    resp = client.put("/api/settings/execution-backend", json={"backend": "local"})
    assert resp.status_code == 200
    assert resp.json()["backend"] == "local"
