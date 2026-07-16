"""Tests for tool PATH / npm-prefix resolution and install error surfacing."""

from __future__ import annotations

import os


def test_npm_prefix_falls_back_to_home_when_root_prefix_unwritable(
    tmp_path, monkeypatch
):
    from services import tool_env

    # Simulate the pod: NPM_CONFIG_PREFIX points at a dir whose node_modules
    # exists but is NOT writable by us.
    root_prefix = tmp_path / "opt" / "npm-global"
    (root_prefix / "lib" / "node_modules").mkdir(parents=True)
    os.chmod(root_prefix / "lib" / "node_modules", 0o555)
    monkeypatch.setenv("NPM_CONFIG_PREFIX", str(root_prefix))
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    assert tool_env.npm_prefix() == str(home / ".npm-global")


def test_npm_prefix_honors_writable_env_prefix(tmp_path, monkeypatch):
    from services import tool_env

    prefix = tmp_path / "writable-prefix"
    (prefix / "lib" / "node_modules").mkdir(parents=True)
    monkeypatch.setenv("NPM_CONFIG_PREFIX", str(prefix))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    assert tool_env.npm_prefix() == str(prefix)


def test_with_tool_paths_prepends_and_is_idempotent(tmp_path, monkeypatch):
    from services import tool_env

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("NPM_CONFIG_PREFIX", raising=False)

    env = {"PATH": "/usr/bin"}
    out = tool_env.with_tool_paths(env)
    parts = out["PATH"].split(os.pathsep)
    assert parts[0] == str(home / ".npm-global" / "bin")
    assert str(home / ".local" / "bin") in parts
    assert tool_env.IMAGE_NPM_PREFIX + "/bin" in parts
    assert "/usr/bin" in parts

    # Idempotent: re-applying doesn't duplicate entries.
    again = tool_env.with_tool_paths(out)
    assert again["PATH"] == out["PATH"]
    # Original env is not mutated.
    assert env["PATH"] == "/usr/bin"


def test_install_error_adds_eacces_hint():
    from services.installer import _install_error

    err = b"npm ERR! code EACCES\nnpm ERR! syscall mkdir\nnpm ERR! path /opt/npm-global/lib"
    msg = _install_error("codex-cli", "/home/u/.npm-global", err)
    assert "codex-cli" in msg
    assert "EACCES" in msg
    assert "HOME-writable" in msg  # actionable hint present


def test_install_error_keeps_tail_without_hint():
    from services.installer import _install_error

    msg = _install_error("x", "/p", b"some transient network blip")
    assert "network blip" in msg
    assert "Hint:" not in msg


def test_antigravity_binary_is_agy():
    """Registry must match the installed binary name (`agy`, not `antigravity`)."""
    from services.installer import get_tool_info

    info = get_tool_info("antigravity")
    assert info is not None
    assert info["binary"] == "agy"
    assert info["version_command"].startswith("agy")
