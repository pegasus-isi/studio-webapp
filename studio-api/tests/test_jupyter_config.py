"""Guard docker/jupyter/jupyter_server_config.py (PAS-SEC-03).

JupyterLab runs with no token/password because the gateway authenticates, so
jupyter_server's XSRF check and Origin check are the only things standing
between a malicious page and `POST /api/terminals` with the gateway cookie.
They were both switched off (disable_check_xsrf=True, allow_origin="*") and
the CSP allowed any site to frame the terminal UI. Nothing in the Python suite
can run jupyter_server, so this executes the config file against a stub
`get_config()` and asserts the resulting traits, and pins jupyter.sh so no
security flag creeps back onto the command line. (The matching nginx
requirement — Host forwarded WITH its port to Jupyter — is asserted in
tests/test_nginx_hardening.py.)
"""

from __future__ import annotations

import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONF = os.path.join(ROOT, "docker", "jupyter", "jupyter_server_config.py")
JUPYTER_SH = os.path.join(ROOT, "docker", "supervisor", "scripts", "jupyter.sh")

# Cleared before every load, so a stray value in the developer's shell cannot
# turn a default green.
KNOBS = ("STUDIO_JUPYTER_DISABLE_XSRF", "STUDIO_JUPYTER_ALLOW_ORIGIN",
         "STUDIO_ENABLE_PEGASUS_BOT", "JUPYTERHUB_SERVICE_PREFIX")


class _Section(dict):
    """Attribute bag standing in for a traitlets Config section (nests on demand)."""

    def __getattr__(self, name):
        return self.setdefault(name, _Section())

    def __setattr__(self, name, value):
        self[name] = value


def _load(monkeypatch, env: dict[str, str] | None = None) -> _Section:
    """Execute the config with a stub `get_config()` and return what it set."""
    for k in KNOBS:
        monkeypatch.delenv(k, raising=False)
    for k, v in (env or {}).items():
        monkeypatch.setenv(k, v)
    c = _Section()
    with open(CONF) as f:
        src = f.read()
    exec(compile(src, CONF, "exec"), {"get_config": lambda: c, "__name__": "jupyter_config"})
    return c


def test_xsrf_enabled_by_default(monkeypatch):
    c = _load(monkeypatch)
    assert c["ServerApp"].get("disable_check_xsrf") is False


def test_xsrf_rollback_knob_is_opt_in_only(monkeypatch):
    assert _load(monkeypatch, {"STUDIO_JUPYTER_DISABLE_XSRF": "0"})["ServerApp"]["disable_check_xsrf"] is False
    assert _load(monkeypatch, {"STUDIO_JUPYTER_DISABLE_XSRF": "1"})["ServerApp"]["disable_check_xsrf"] is True


def test_no_token_means_xsrf_must_be_on(monkeypatch):
    """Auth is delegated to the gateway; XSRF is then the only CSRF control."""
    c = _load(monkeypatch)
    assert c["IdentityProvider"].get("token") == ""
    assert "token" not in c["ServerApp"], "use IdentityProvider.token (ServerApp.token is deprecated)"
    assert c["ServerApp"]["disable_check_xsrf"] is False


def test_allow_origin_is_same_origin_by_default(monkeypatch):
    assert _load(monkeypatch)["ServerApp"].get("allow_origin") == ""


@pytest.mark.parametrize("value,expected", [
    ("https://studio.example.org", "https://studio.example.org"),
    ("http://localhost:8888", "http://localhost:8888"),
    ("*", ""),
    ("https://a.example.org/path", ""),
    ("studio.example.org", ""),
])
def test_allow_origin_env_accepts_one_exact_origin(monkeypatch, value, expected):
    c = _load(monkeypatch, {"STUDIO_JUPYTER_ALLOW_ORIGIN": value})
    assert c["ServerApp"]["allow_origin"] == expected


def test_no_custom_csp(monkeypatch):
    """A headers override REPLACES jupyter_server's `frame-ancestors 'self'`."""
    c = _load(monkeypatch)
    headers = c["ServerApp"]["tornado_settings"].get("headers", {})
    assert "Content-Security-Policy" not in headers, headers
    text = open(CONF).read()
    assert not re.search(r"frame-ancestors[^;\"']*\*", text)


def test_loopback_bind_and_remote_access(monkeypatch):
    c = _load(monkeypatch)
    assert c["ServerApp"]["ip"] == "127.0.0.1"
    assert c["ServerApp"]["allow_remote_access"] is True  # Host is the public hub name


def test_jupyter_sh_passes_only_runtime_paths():
    sh = open(JUPYTER_SH).read()
    for flag in ("--ServerApp.token", "disable_check_xsrf", "allow_origin", "--ip", "--port"):
        assert flag not in sh, f"{flag} belongs in jupyter_server_config.py, not the command line"
    assert "--ServerApp.base_url=" in sh and "--ServerApp.root_dir=" in sh
