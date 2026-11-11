"""docker/supervisor/scripts/opencode.sh gives OpenCode a private TMPDIR.

OpenCode's bash tool tells the model to do scratch work in
os.tmpdir()/opencode and says that directory already exists. With the default
TMPDIR that is /tmp/opencode. The image build left it root-owned, and on a
shared host it belongs to whichever user created it first. The model's
`mkdir -p` there failed, its `cd` failed with it, and the test script wrote
its outputs into $HOME. The launcher now points TMPDIR inside the user's
private runtime dir. These tests run the real script against a fake
`opencode` that prints the environment it received.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "docker"
    / "supervisor"
    / "scripts"
    / "opencode.sh"
)

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None, reason="needs bash to run the launcher"
)


def _run(tmp_path: Path, runtime: Path | None) -> tuple[subprocess.CompletedProcess, Path]:
    home = tmp_path / "home"
    fake_bin = home / ".npm-global" / "bin"
    fake_bin.mkdir(parents=True)
    # The launcher puts ~/.npm-global/bin first on PATH, so this stands in for
    # the real binary that `exec opencode serve` would start.
    fake = fake_bin / "opencode"
    fake.write_text('#!/bin/bash\necho "TMPDIR=$TMPDIR"\necho "PWD=$PWD"\n')
    fake.chmod(0o755)

    env = {
        "HOME": str(home),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "OPENCODE_SERVER_PASSWORD": "test-password",
        "TMPDIR": "/tmp",
    }
    if runtime is not None:
        env["STUDIO_RUNTIME_DIR"] = str(runtime)
    proc = subprocess.run(
        ["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=30
    )
    return proc, home


def test_tmpdir_is_private_dir_under_runtime_dir(tmp_path):
    runtime = tmp_path / "runtime"
    runtime.mkdir(mode=0o700)
    proc, home = _run(tmp_path, runtime)

    assert proc.returncode == 0, proc.stderr
    tmpdir = runtime / "tmp"
    assert f"TMPDIR={tmpdir}" in proc.stdout
    # Still rooted at the workspace: the scratch dir must not move the session.
    assert f"PWD={home}" in proc.stdout
    assert tmpdir.is_dir()
    assert stat.S_IMODE(tmpdir.stat().st_mode) == 0o700


def test_tmpdir_is_never_the_shared_system_tmp(tmp_path):
    runtime = tmp_path / "runtime"
    runtime.mkdir(mode=0o700)
    proc, _ = _run(tmp_path, runtime)

    assert proc.returncode == 0, proc.stderr
    line = next(ln for ln in proc.stdout.splitlines() if ln.startswith("TMPDIR="))
    # os.tmpdir()/opencode must not resolve to the shared /tmp/opencode.
    assert line not in ("TMPDIR=/tmp", "TMPDIR=/tmp/")
