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
