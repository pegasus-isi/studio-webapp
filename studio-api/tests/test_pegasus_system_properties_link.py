"""docker/pegasus/link-system-properties.sh makes the packaged planner read
/etc/pegasus/pegasus.properties.

Pegasus 6.0's venv packaging hands the planner a sysconfdir inside the venv,
so the system-wide properties (JSON monitord encoding, the AMQP workflow
catalog) were silently ignored. The build step asks the planner's own code
where it looks and symlinks the file there. These tests run the real script
against a fake `pegasus-plan` whose shebang interpreter prints a chosen
sysconfdir, the way Pegasus.cli._java.get_system_properties() would.
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
    / "pegasus"
    / "link-system-properties.sh"
)

pytestmark = pytest.mark.skipif(
    shutil.which("sh") is None, reason="needs sh to run the build step"
)


def _executable(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _setup(tmp_path: Path, python_body: str) -> tuple[Path, dict[str, str]]:
    etc = tmp_path / "etc" / "pegasus"
    etc.mkdir(parents=True)
    src = etc / "pegasus.properties"
    src.write_text("pegasus.monitord.encoding = json\n")

    fake_py = tmp_path / "venv" / "bin" / "python3"
    fake_py.parent.mkdir(parents=True)
    _executable(fake_py, python_body)
    plan = tmp_path / "pegasus-plan"
    _executable(plan, f"#!{fake_py}\nimport sys\n")

    env = {
        **os.environ,
        "PEGASUS_PLAN": str(plan),
        "PEGASUS_SYSTEM_PROPERTIES": str(src),
    }
    return src, env


def _run(env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["sh", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=30
    )


def test_links_into_the_venv_sysconfdir(tmp_path: Path) -> None:
    conf = tmp_path / "venv" / "lib" / "python3.13" / "Pegasus" / "data" / "etc"
    conf.mkdir(parents=True)
    src, env = _setup(tmp_path, f"#!/bin/sh\necho {conf}\n")

    result = _run(env)

    assert result.returncode == 0, result.stderr
    dest = conf / "pegasus.properties"
    assert dest.is_symlink()
    assert os.readlink(dest) == str(src)
    assert dest.read_text() == src.read_text()


def test_rerun_is_idempotent(tmp_path: Path) -> None:
    conf = tmp_path / "conf"
    conf.mkdir()
    src, env = _setup(tmp_path, f"#!/bin/sh\necho {conf}\n")

    assert _run(env).returncode == 0
    result = _run(env)

    assert result.returncode == 0, result.stderr
    assert os.readlink(conf / "pegasus.properties") == str(src)


def test_no_link_once_the_planner_reads_etc_pegasus(tmp_path: Path) -> None:
    # The fixed-Pegasus case: linking the file onto itself would make a
    # symlink loop and destroy the real file.
    src, env = _setup(tmp_path, "")
    fake_py = tmp_path / "venv" / "bin" / "python3"
    _executable(fake_py, f"#!/bin/sh\necho {src.parent}/\n")

    result = _run(env)

    assert result.returncode == 0, result.stderr
    assert "no link needed" in result.stdout
    assert not src.is_symlink()
    assert src.read_text() == "pegasus.monitord.encoding = json\n"


def test_does_not_overwrite_a_file_the_package_ships(tmp_path: Path) -> None:
    conf = tmp_path / "conf"
    conf.mkdir()
    shipped = conf / "pegasus.properties"
    shipped.write_text("from the package\n")
    _, env = _setup(tmp_path, f"#!/bin/sh\necho {conf}\n")

    result = _run(env)

    assert result.returncode == 0
    assert "WARNING" in result.stderr
    assert not shipped.is_symlink()
    assert shipped.read_text() == "from the package\n"


def test_warns_but_does_not_fail_the_build_if_the_api_moved(tmp_path: Path) -> None:
    _, env = _setup(tmp_path, "#!/bin/sh\necho 'ImportError' >&2\nexit 1\n")

    result = _run(env)

    assert result.returncode == 0
    assert "NOT linked" in result.stderr


def test_fails_without_the_system_file(tmp_path: Path) -> None:
    src, env = _setup(tmp_path, f"#!/bin/sh\necho {tmp_path}\n")
    src.unlink()

    result = _run(env)

    assert result.returncode != 0
