"""The backend must not see the user's site-packages.

``~/.local/lib/pythonX/site-packages`` is on the persistent home and sits on
``/usr/bin/python3``'s path ahead of the image's packages, so anything a user
or the assistant ``pip install --user``s would shadow what studio-api was
built against. The supervisord program shields it; JupyterLab is left alone on
purpose (notebook users install into the user site and kernels inherit it).
"""

from __future__ import annotations

import configparser
import os


def _conf() -> configparser.ConfigParser:
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(os.path.dirname(here), "docker", "supervisor", "supervisord.conf")
    cp = configparser.ConfigParser(interpolation=None)
    cp.read(path)
    return cp


def test_studio_api_ignores_the_user_site():
    env = _conf()["program:studio-api"].get("environment", "")
    assert 'PYTHONNOUSERSITE="1"' in env


def test_jupyterlab_is_deliberately_not_shielded():
    cp = _conf()
    jupyter = [s for s in cp.sections() if s.startswith("program:") and "jupyter" in s]
    assert jupyter, "no jupyter program in supervisord.conf"
    for section in jupyter:
        assert "PYTHONNOUSERSITE" not in cp[section].get("environment", "")
