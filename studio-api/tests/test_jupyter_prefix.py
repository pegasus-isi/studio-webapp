"""JupyterLab under a reverse-proxy prefix, for both KubeSpawner and Open
OnDemand.

Unlike studio-api and studio-web, JupyterLab bakes its URL prefix into its own
route registration and rendered asset links at *startup* (`--ServerApp.
base_url`), not per-request. That's why its nginx location is the one location
that passes the request straight through instead of stripping the prefix (see
prefix-proxy.conf.tmpl and studio-proxy.conf.tmpl) — the backend has to see
the same prefix it was told to expect, or the browser fetches JupyterLab's own
static assets at a URL that never reaches this session (#405-ish: OOD's prefix
isn't KubeSpawner's /user/<name>, and jupyter.sh only knew about the latter,
so JupyterLab always assumed it sat at the bare /jupyter/ under Open OnDemand
and every asset it rendered 404'd one level up).
"""

from __future__ import annotations

import os
import re
import subprocess

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_ENTRYPOINT = os.path.join(_REPO, "docker", "entrypoint.sh")
_TEMPLATES = os.path.join(_REPO, "docker", "nginx")
_JUPYTER_SH = os.path.join(_REPO, "docker", "supervisor", "scripts", "jupyter.sh")

pytestmark = pytest.mark.skipif(
    __import__("shutil").which("bash") is None, reason="bash is required"
)


def _base_url(**env: str) -> str:
    """Run jupyter.sh's BASE_URL computation in isolation and return it.

    Extracts just that block rather than running the real script, which execs
    `jupyter lab` as its last line and never returns.
    """
    with open(_JUPYTER_SH, encoding="utf-8") as fh:
        script = fh.read()
    match = re.search(
        r'^BASE_URL=.*?\nfi\n', script, re.MULTILINE | re.DOTALL
    )
    assert match, "BASE_URL computation not found in jupyter.sh"
    proc = subprocess.run(
        ["bash", "-c", f'set -euo pipefail\n{match.group(0)}\necho "$BASE_URL"'],
        capture_output=True,
        text=True,
        env={**os.environ, **env},
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def test_base_url_defaults_to_bare_jupyter():
    assert _base_url() == "/jupyter/"


def test_base_url_follows_kubespawner_prefix():
    assert (
        _base_url(JUPYTERHUB_SERVICE_PREFIX="/user/alice/")
        == "/user/alice/jupyter/"
    )


def test_base_url_follows_open_ondemand_prefix():
    """The gap this test guards: STUDIO_URL_PREFIX used to be ignored here
    entirely, so an OOD session's JupyterLab always rendered asset links as
    if it were unprefixed."""
    assert (
        _base_url(STUDIO_URL_PREFIX="/node/compute-1/20153")
        == "/node/compute-1/20153/jupyter/"
    )


def test_kubespawner_prefix_wins_when_both_are_set():
    assert (
        _base_url(
            JUPYTERHUB_SERVICE_PREFIX="/user/alice/",
            STUDIO_URL_PREFIX="/node/compute-1/20153",
        )
        == "/user/alice/jupyter/"
    )


def test_entrypoint_exports_url_prefix_to_supervisord_children():
    """jupyter.sh is a supervisord child with no explicit `environment=` line
    (docker/supervisor/supervisord.conf), so it only ever sees STUDIO_URL_PREFIX
    if the entrypoint exports it. Unlike STUDIO_AUTH_TOKEN, this is not a
    credential, so it's safe to export."""
    with open(_ENTRYPOINT, encoding="utf-8") as fh:
        script = fh.read()
    assert re.search(r"^export\s+STUDIO_URL_PREFIX\s*$", script, re.MULTILINE)


def _render_prefix_proxy(tmp_path, **env: str) -> str:
    with open(_ENTRYPOINT, encoding="utf-8") as fh:
        body = re.search(
            r"^render_template\(\) \{\n(.*?)^\}$",
            fh.read(),
            re.MULTILINE | re.DOTALL,
        ).group(0)
    src = os.path.join(_TEMPLATES, "prefix-proxy.conf.tmpl")
    dst = str(tmp_path / "prefix-proxy.conf")
    base = {
        "STUDIO_PORT": "8888", "STUDIO_AUTH_TOKEN": "", "STUDIO_JUPYTER_TOKEN": "",
        "STUDIO_INTERNAL_SECRET": "", "STUDIO_API_PORT": "8080",
        "STUDIO_WEB_PORT": "3000", "STUDIO_JUPYTER_PORT": "8889",
        "STUDIO_OPENCODE_PORT": "4096", "STUDIO_RUNTIME_DIR": str(tmp_path),
    }
    proc = subprocess.run(
        ["bash", "-c", f'set -euo pipefail\n{body}\nrender_template "$1" "$2"',
         "_", src, dst],
        capture_output=True,
        text=True,
        env={**os.environ, **base, **env},
    )
    assert proc.returncode == 0, proc.stderr
    with open(dst, encoding="utf-8") as fh:
        return fh.read()


def test_prefixed_jupyter_location_passes_the_request_through_unmodified(tmp_path):
    """No URI on proxy_pass here, on purpose: stripping the prefix (like the
    api/ws/web locations do) would send JupyterLab a path shorter than the one
    it was started expecting via --ServerApp.base_url, and every request past
    the first would 404."""
    out = _render_prefix_proxy(tmp_path, STUDIO_URL_PREFIX="/node/compute-1/20153")
    assert "location /node/compute-1/20153/jupyter/ {" in out
    assert "proxy_pass http://127.0.0.1:8889;" in out
    assert "proxy_pass http://127.0.0.1:8889/jupyter/;" not in out
