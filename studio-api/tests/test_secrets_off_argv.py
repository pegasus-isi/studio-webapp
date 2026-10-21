"""Secrets never ride on a command line (#598).

A process's argv is readable by every user on the host (/proc/<pid>/cmdline,
`ps`), and on Open OnDemand that host is shared. JupyterLab used to get its
token as --ServerApp.token=..., and the entrypoint rendered the nginx configs
with sed, whose -e expressions carried the session token, the Jupyter token
and the internal secret.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import types

import pytest

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_ENTRYPOINT = os.path.join(_REPO, "docker", "entrypoint.sh")
_JUPYTER_SH = os.path.join(_REPO, "docker", "supervisor", "scripts", "jupyter.sh")
_JUPYTER_CFG = os.path.join(_REPO, "docker", "jupyter", "jupyter_server_config.py")


def _render_fn() -> str:
    with open(_ENTRYPOINT, encoding="utf-8") as fh:
        script = fh.read()
    match = re.search(r"^render_template\(\) \{\n(.*?)^\}$", script, re.MULTILINE | re.DOTALL)
    assert match, "render_template() not found in docker/entrypoint.sh"
    return match.group(0)


def test_jupyter_sh_passes_no_token():
    with open(_JUPYTER_SH, encoding="utf-8") as fh:
        code = [l for l in fh if not l.lstrip().startswith("#")]
    assert not any("token" in l.lower() for l in code), "a token is on jupyter's command line"


def test_jupyter_config_reads_the_token_from_the_environment(monkeypatch):
    class _Section(types.SimpleNamespace):
        pass

    class _Config:
        def __getattr__(self, name):
            sec = _Section()
            object.__setattr__(self, name, sec)
            return sec

    c = _Config()
    monkeypatch.setenv("STUDIO_JUPYTER_TOKEN", "tok-from-env")
    monkeypatch.setenv("STUDIO_ENABLE_PEGASUS_BOT", "0")
    with open(_JUPYTER_CFG, encoding="utf-8") as fh:
        exec(compile(fh.read(), _JUPYTER_CFG, "exec"), {"get_config": lambda: c})
    assert c.IdentityProvider.token == "tok-from-env"
    assert c.ServerApp.token == "tok-from-env"


def test_render_template_uses_no_external_substitution_tool():
    body = _render_fn()
    code = "\n".join(l for l in body.splitlines() if not l.lstrip().startswith("#"))
    assert not re.search(r"\b(sed|perl|awk)\b", code)


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
def test_render_template_substitutes_awkward_values_exactly(tmp_path):
    # Characters that broke or bent the sed version (`|` ended its expression,
    # `&` meant the match), and that bash 5.2's patsub_replacement would read
    # as the match if the replacement weren't quoted.
    secret = "a&b|c/d\\e$f`g"
    src = tmp_path / "x.conf.tmpl"
    src.write_text("token @STUDIO_AUTH_TOKEN@;\nport @STUDIO_PORT@;\nkeep\n\n", encoding="utf-8")
    dst = tmp_path / "x.conf"
    proc = subprocess.run(
        ["bash", "-c", f'set -euo pipefail\n{_render_fn()}\nrender_template "$1" "$2"',
         "_", str(src), str(dst)],
        capture_output=True, text=True,
        env={**os.environ, "STUDIO_AUTH_TOKEN": secret, "STUDIO_PORT": "8888"},
    )
    assert proc.returncode == 0, proc.stderr
    # Trailing blank line kept: $(...) would have eaten it.
    assert dst.read_text(encoding="utf-8") == f"token {secret};\nport 8888;\nkeep\n\n"
