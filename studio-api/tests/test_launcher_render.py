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
    # Internal listener defaults — the pod network namespace depends on these.
    assert "--port 8080" in supervisor
    assert 'PORT="3000"' in supervisor
    assert "server 127.0.0.1:8080;" in proxy
    assert "server 127.0.0.1:3000;" in proxy
    assert "proxy_pass http://127.0.0.1:8889/jupyter/;" in proxy
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
            "STUDIO_API_PORT": "9080",
            "STUDIO_WEB_PORT": "9300",
            "STUDIO_JUPYTER_PORT": "9889",
            "STUDIO_API_DIR": "/srv/dist/studio-api",
            "STUDIO_WEB_DIR": "/srv/dist/studio-web",
        },
    )
    proxy = (runtime / "studio-proxy.conf").read_text()
    assert "listen 9999;" in proxy
    assert "server 127.0.0.1:9080;" in proxy
    assert "server 127.0.0.1:9300;" in proxy
    assert "proxy_pass http://127.0.0.1:9889/jupyter/;" in proxy
    supervisor = (runtime / "supervisord.conf").read_text()
    assert "directory=/srv/dist/studio-api" in supervisor
    assert "directory=/srv/dist/studio-web" in supervisor
    assert "--port 9080" in supervisor
    assert 'PORT="9300"' in supervisor


def _rendered_ports(runtime: Path) -> dict[str, int]:
    """Ports as they landed in the rendered configs, keyed by role."""
    proxy = (runtime / "studio-proxy.conf").read_text()
    supervisor = (runtime / "supervisord.conf").read_text()
    api_up, web_up = re.findall(r"server 127\.0\.0\.1:(\d+);", proxy)
    (jupyter,) = set(re.findall(r"proxy_pass http://127\.0\.0\.1:(\d+)", proxy))
    (api_cmd,) = re.findall(r"--port (\d+)", supervisor)
    (web_env,) = re.findall(r'PORT="(\d+)"', supervisor)
    return {
        "api_upstream": int(api_up),
        "web_upstream": int(web_up),
        "jupyter": int(jupyter),
        "api_command": int(api_cmd),
        "web_env": int(web_env),
    }


def test_alloc_ports_picks_free_consistent_ports(tmp_path: Path):
    """STUDIO_ALLOC_PORTS=1 is how a shared native host avoids two users
    colliding on 8080/3000/8889: every internal listener gets a free port,
    and the proxy upstreams must agree with what the services were told."""
    runtime = _render(tmp_path, {"STUDIO_ALLOC_PORTS": "1"})
    ports = _rendered_ports(runtime)
    # Proxy and supervisord must have received the SAME allocation.
    assert ports["api_upstream"] == ports["api_command"]
    assert ports["web_upstream"] == ports["web_env"]
    # All listeners distinct — one python process holds the sockets together
    # during allocation precisely so the kernel can't hand a port out twice.
    listener_ports = {ports["api_upstream"], ports["web_upstream"], ports["jupyter"]}
    assert len(listener_ports) == 3, ports


def test_alloc_ports_respects_explicit_values(tmp_path: Path):
    """Explicit env always beats allocation — a site pinning one port must
    not have it silently replaced."""
    runtime = _render(
        tmp_path, {"STUDIO_ALLOC_PORTS": "1", "STUDIO_API_PORT": "18080"}
    )
    ports = _rendered_ports(runtime)
    assert ports["api_upstream"] == 18080
    assert ports["api_command"] == 18080
    assert ports["web_upstream"] != 3000  # still allocated


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


def test_no_prefix_renders_empty_prefix_locations(tmp_path: Path):
    """Default (K8s/container) path: the include target must exist but carry
    no location blocks — the /user regexes in studio-proxy.conf do that job."""
    runtime = _render(tmp_path)
    prefix_conf = (runtime / "prefix-locations.conf").read_text()
    assert not re.findall(r"^\s*location\b", prefix_conf, re.M), prefix_conf
    assert f"{tmp_path}/runtime/prefix-locations.conf" in (
        runtime / "studio-proxy.conf"
    ).read_text()


def test_url_prefix_renders_literal_locations(tmp_path: Path):
    runtime = _render(
        tmp_path,
        {"STUDIO_URL_PREFIX": "/node/host1/23456", "STUDIO_JUPYTER_PORT": "9889"},
    )
    conf = (runtime / "prefix-locations.conf").read_text()
    # Bare prefix redirects to trailing slash, like /user/<name> does.
    assert "location = /node/host1/23456 {" in conf
    # Strip-at-proxy via prefix location + URI proxy_pass — by construction
    # immune to the regex/rewrite truncation bug the /user blocks document.
    assert "location /node/host1/23456/api/ {" in conf
    assert "proxy_pass http://studio_api/api/;" in conf
    assert "location /node/host1/23456/ws/ {" in conf
    # Jupyter serves AT the prefix on its per-session port: full path, no URI.
    assert "location /node/host1/23456/jupyter/ {" in conf
    assert "proxy_pass http://127.0.0.1:9889;" in conf
    # The app learns its prefix from this header (app/layout.tsx base href).
    assert "proxy_set_header X-Forwarded-Prefix /node/host1/23456;" in conf
    assert "sub_filter 'href=\"/_next/' 'href=\"/node/host1/23456/_next/';" in conf
    # And nothing regex-shaped: these are literal prefix locations only.
    assert "location ~" not in conf


def test_url_prefix_trailing_slash_is_normalized(tmp_path: Path):
    a = _render(tmp_path / "a", {"STUDIO_URL_PREFIX": "/node/h/1"})
    b = _render(tmp_path / "b", {"STUDIO_URL_PREFIX": "/node/h/1/"})
    assert (a / "prefix-locations.conf").read_text() == (
        b / "prefix-locations.conf"
    ).read_text()


def test_url_prefix_must_be_absolute(tmp_path: Path):
    env = os.environ.copy()
    env["HOME"] = str(tmp_path / "home")
    env["STUDIO_RUNTIME_DIR"] = str(tmp_path / "runtime")
    env["STUDIO_URL_PREFIX"] = "node/h/1"
    proc = subprocess.run(
        ["bash", str(LAUNCH), "--render-only"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc.returncode != 0
    assert "must start with" in proc.stderr


def test_user_prefix_regexes_survive_alongside_url_prefix(tmp_path: Path):
    """The K8s /user/<name> path and an OOD prefix are not either/or: the
    regex locations must remain whatever STUDIO_URL_PREFIX says."""
    runtime = _render(tmp_path, {"STUDIO_URL_PREFIX": "/node/h/1"})
    proxy = (runtime / "studio-proxy.conf").read_text()
    assert "location ~ ^/user/[^/]+(?<studio_api_path>/api(?:/.*)?)$" in proxy


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
