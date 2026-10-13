"""An Open OnDemand session gives Studio a home of its own (#482).

Everything Studio writes is $HOME-relative, and on a cluster $HOME is the
user's real home, where their own codex/opencode/claude/Pegasus configuration
already lives. The batch-connect app (github.com/pegasus-isi/bc_pegasus_ai_studio)
runs the image with `apptainer --home "$STUDIO_HOME"`; the entrypoint backs that
up for a launch that only set the variable. Also here: the URL prefix OOD
assigns carries the session host, a FQDN on many clusters.

These run the real entrypoint, not a reimplementation.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_ENTRYPOINT = os.path.join(_REPO, "docker", "entrypoint.sh")
_TEMPLATES = os.path.join(_REPO, "docker", "nginx")

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None, reason="bash is required to run the entrypoint"
)


# --- entrypoint fallback ------------------------------------------------------


def _apply_studio_home(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    with open(_ENTRYPOINT, encoding="utf-8") as fh:
        script = fh.read()
    match = re.search(
        r"^apply_studio_home\(\) \{\n.*?^\}$", script, re.MULTILINE | re.DOTALL
    )
    assert match, "apply_studio_home() not found in docker/entrypoint.sh"
    base = {k: v for k, v in os.environ.items() if not k.startswith("STUDIO_")}
    return subprocess.run(
        ["bash", "-c", f'set -euo pipefail\n{match.group(0)}\napply_studio_home\n'
         'bash -c \'printf "%s" "$HOME"\''],
        capture_output=True,
        text=True,
        env={**base, **env},
    )


def test_entrypoint_moves_home_when_only_studio_home_was_set():
    proc = _apply_studio_home({"HOME": "/home/u", "STUDIO_HOME": "/home/u/studio/"})
    assert proc.returncode == 0, proc.stderr
    # Exported, so supervisord and every tool it starts inherit it.
    assert proc.stdout == "/home/u/studio"
    assert "passwd" in proc.stderr


def test_entrypoint_is_quiet_when_apptainer_already_moved_home():
    proc = _apply_studio_home({"HOME": "/home/u/studio", "STUDIO_HOME": "/home/u/studio"})
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "/home/u/studio"
    assert proc.stderr == ""


def test_entrypoint_leaves_home_alone_without_studio_home():
    """KubeSpawner and the plain container: the home is already Studio's."""
    proc = _apply_studio_home({"HOME": "/home/jovyan"})
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "/home/jovyan"


def test_entrypoint_refuses_a_relative_studio_home():
    proc = _apply_studio_home({"HOME": "/home/u", "STUDIO_HOME": "studio"})
    assert proc.returncode != 0
    assert "absolute" in proc.stderr


def _render_only(tmp_path, **env: str) -> subprocess.CompletedProcess[str]:
    script = tmp_path / "entrypoint.sh"
    with open(_ENTRYPOINT, encoding="utf-8") as fh:
        script.write_text(
            fh.read().replace("/opt/pegasus-ai/templates", _TEMPLATES),
            encoding="utf-8",
        )
    base = {k: v for k, v in os.environ.items() if not k.startswith("STUDIO_")}
    return subprocess.run(
        ["bash", str(script), "--render-only"],
        capture_output=True,
        text=True,
        env={
            **base,
            "HOME": str(tmp_path / "home"),
            "STUDIO_RUNTIME_DIR": str(tmp_path / "rt"),
            **env,
        },
    )


def test_render_only_still_touches_no_home(tmp_path):
    """The image build runs --render-only; it must leave no state behind."""
    proc = _render_only(tmp_path, STUDIO_HOME=str(tmp_path / "studio"))
    assert proc.returncode == 0, proc.stderr
    assert not (tmp_path / "studio").exists()
    assert not (tmp_path / "home").exists()


def test_a_fqdn_session_host_in_the_url_prefix_is_accepted(tmp_path):
    """OOD's prefix is /node/<host>/<port>, and <host> is a FQDN on many
    clusters. Refusing the dot killed the session before the proxy started."""
    prefix = "/node/c001.cluster.example.edu/45678"
    proc = _render_only(
        tmp_path, STUDIO_AUTH_TOKEN="tok_en-123", STUDIO_URL_PREFIX=prefix
    )
    assert proc.returncode == 0, proc.stderr
    rendered = (tmp_path / "rt" / "prefix-proxy.conf").read_text(encoding="utf-8")
    assert f"location {prefix}/api/" in rendered


# --- Java's home (#489) -------------------------------------------------------


def _apply_java_home(env: dict[str, str], passwd_home: str) -> subprocess.CompletedProcess[str]:
    with open(_ENTRYPOINT, encoding="utf-8") as fh:
        script = fh.read()
    match = re.search(
        r"^apply_java_home\(\) \{\n.*?^\}$", script, re.MULTILINE | re.DOTALL
    )
    assert match, "apply_java_home() not found in docker/entrypoint.sh"
    # A getent that reports the given passwd home, so the test does not depend
    # on who runs it.
    fake_getent = f'getent() {{ echo "u:x:1000:1000::{passwd_home}:/bin/bash"; }}'
    base = {k: v for k, v in os.environ.items() if k != "JAVA_TOOL_OPTIONS"}
    return subprocess.run(
        ["bash", "-c", f"set -euo pipefail\n{fake_getent}\n{match.group(0)}\n"
         'apply_java_home\nbash -c \'printf "%s" "${JAVA_TOOL_OPTIONS:-}"\''],
        capture_output=True,
        text=True,
        env={**base, **env},
    )


def test_java_is_told_the_studio_home_when_passwd_names_another():
    """Under OOD the app binds the host /etc/passwd, so the JVM's user.home is
    the real home and pegasus-plan read the real ~/.pegasusrc."""
    proc = _apply_java_home({"HOME": "/home/u/pegasus-studio"}, "/home/u")
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "-Duser.home=/home/u/pegasus-studio"


def test_java_is_left_alone_when_passwd_agrees():
    """KubeSpawner: the home is Studio's and passwd says so."""
    proc = _apply_java_home({"HOME": "/home/u/"}, "/home/u")
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == ""


def test_existing_java_options_are_kept_and_not_stacked():
    proc = _apply_java_home(
        {"HOME": "/s", "JAVA_TOOL_OPTIONS": "-Xmx2g"}, "/home/u"
    )
    assert proc.stdout == "-Duser.home=/s -Xmx2g"
    again = _apply_java_home(
        {"HOME": "/s", "JAVA_TOOL_OPTIONS": "-Duser.home=/s -Xmx2g"}, "/home/u"
    )
    assert again.stdout == "-Duser.home=/s -Xmx2g"
