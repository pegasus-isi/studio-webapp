"""Exercise launcher/studio-launch --render-only.

Nothing in CI boots the container, so this is the gate that catches the two
ways the launcher extraction can rot: a template growing an @VAR@ the script
doesn't substitute (the rendered config would reach nginx/supervisord with a
literal placeholder in it), and the env contract silently changing the
container defaults that KubeSpawner depends on (port 8888, /opt/studio-* paths).
Runs the real script under bash with a scratch HOME/runtime dir — no Docker,
no root, no app dirs needed (--render-only touches neither $HOME nor the DB).
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
LAUNCH = REPO_ROOT / "launcher" / "studio-launch"


def _render(tmp_path: Path, env_overrides: dict[str, str] | None = None) -> Path:
    runtime = tmp_path / "runtime"
    env = os.environ.copy()
    # Strip any STUDIO_* from the outer environment so a developer's shell
    # can't leak into the assertions, then apply the test's own overrides.
    for key in [k for k in env if k.startswith("STUDIO_")]:
        del env[key]
    env["HOME"] = str(tmp_path / "home")
    env["STUDIO_RUNTIME_DIR"] = str(runtime)
    env.update(env_overrides or {})
    proc = subprocess.run(
        ["bash", str(LAUNCH), "--render-only"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    return runtime


def test_renders_all_templates(tmp_path: Path):
    runtime = _render(tmp_path)
    rendered = {p.name for p in runtime.iterdir() if p.is_file()}
    expected = {
        p.name.removesuffix(".tmpl")
        for p in (REPO_ROOT / "launcher" / "templates").glob("*.tmpl")
    }
    assert expected, "no templates found — launcher tree moved?"
    assert expected <= rendered, rendered


def test_no_unrendered_placeholders(tmp_path: Path):
    """A leftover @VAR@ means the template and the script's sed list drifted."""
    runtime = _render(tmp_path)
    for conf in runtime.iterdir():
        if not conf.is_file():
            continue
        leftovers = re.findall(r"@[A-Z_]+@", conf.read_text())
        assert not leftovers, f"{conf.name}: {leftovers}"


def test_container_defaults_are_the_k8s_contract(tmp_path: Path):
    """KubeSpawner points at :8888 and the image bakes /opt/studio-* — the
    launcher's defaults must reproduce them exactly or a merge redeploys a
    broken image to live user pods."""
    runtime = _render(tmp_path)
    proxy = (runtime / "studio-proxy.conf").read_text()
    supervisor = (runtime / "supervisord.conf").read_text()
    nginx = (runtime / "nginx.conf").read_text()

    assert "listen 8888;" in proxy
    # The /user/<name> prefix locations must survive rendering untouched.
    assert "location ~ ^/user/[^/]+(?<studio_api_path>/api(?:/.*)?)$" in proxy
    assert "directory=/opt/studio-api" in supervisor
    assert "directory=/opt/studio-web" in supervisor
    assert f"pid {tmp_path}/runtime/nginx.pid;" in nginx
    # The rendered nginx.conf must include only our server block, never the
    # host's conf.d/sites-enabled (a lab host's own vhosts must not leak in).
    includes = re.findall(r"^\s*include\s+([^;]+);", nginx, re.M)
    assert not any("conf.d" in i or "sites-enabled" in i for i in includes), includes
    assert f"{tmp_path}/runtime/studio-proxy.conf" in includes


def test_env_overrides_reach_the_rendered_configs(tmp_path: Path):
    runtime = _render(
        tmp_path,
        {
            "STUDIO_PORT": "9999",
            "STUDIO_API_DIR": "/srv/dist/studio-api",
            "STUDIO_WEB_DIR": "/srv/dist/studio-web",
        },
    )
    assert "listen 9999;" in (runtime / "studio-proxy.conf").read_text()
    supervisor = (runtime / "supervisord.conf").read_text()
    assert "directory=/srv/dist/studio-api" in supervisor
    assert "directory=/srv/dist/studio-web" in supervisor


def test_render_only_writes_nothing_outside_runtime_dir(tmp_path: Path):
    """--render-only is the testing/preflight mode; it must not provision
    $HOME (no ~/.studio, no marketplace clone, no DB init)."""
    _render(tmp_path)
    home = tmp_path / "home"
    assert not home.exists() or not any(home.iterdir())


def test_supervisord_runs_the_launcher_jupyter_script(tmp_path: Path):
    """jupyter.sh moved from docker/supervisor/scripts into the launcher tree;
    the rendered program command must point at the shipped copy."""
    runtime = _render(tmp_path)
    supervisor = (runtime / "supervisord.conf").read_text()
    jupyter_sh = REPO_ROOT / "launcher" / "scripts" / "jupyter.sh"
    assert jupyter_sh.exists()
    assert f"command={jupyter_sh}" in supervisor


def test_refuses_to_wipe_a_dir_it_did_not_create(tmp_path: Path):
    """STUDIO_RUNTIME_DIR is rm -rf'd on every start; a mis-set value
    (someone's $HOME, say) must fail loudly instead of being deleted."""
    precious = tmp_path / "precious"
    precious.mkdir()
    (precious / "thesis.tex").write_text("years of work\n")
    env = os.environ.copy()
    env["HOME"] = str(tmp_path / "home")
    env["STUDIO_RUNTIME_DIR"] = str(precious)
    proc = subprocess.run(
        ["bash", str(LAUNCH), "--render-only"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc.returncode != 0
    assert "refusing to wipe" in proc.stderr
    assert (precious / "thesis.tex").exists()


def test_rerender_replaces_stale_state(tmp_path: Path):
    """The runtime dir is wiped per start: a config left by an older template
    version (or a different port) must not survive into the new session."""
    runtime = _render(tmp_path)
    stale = runtime / "stale-from-last-session.conf"
    stale.write_text("listen 1234;\n")
    _render(tmp_path)
    assert not stale.exists()


@pytest.mark.parametrize("missing_ok", [True])
def test_launcher_is_executable_bash(missing_ok: bool):
    """chmod +x is applied in the Dockerfile, but the repo copy should carry
    the bit too so a native checkout can run it directly."""
    assert LAUNCH.exists()
    assert os.access(LAUNCH, os.X_OK), "launcher/studio-launch lost its +x bit"
    first = LAUNCH.read_text().splitlines()[0]
    assert first.startswith("#!"), first
