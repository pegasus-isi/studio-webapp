"""The jupyter status endpoint must probe the port the launcher assigned.

On shared native hosts the launcher allocates the JupyterLab port per session
(STUDIO_ALLOC_PORTS); a hardcoded 8889 here would report "stopped" forever
while JupyterLab runs happily on its allocated port.
"""

from __future__ import annotations

import socket
import threading

from routers.jupyter import _jupyter_port, _port_in_use


def test_port_default(monkeypatch):
    monkeypatch.delenv("STUDIO_JUPYTER_PORT", raising=False)
    assert _jupyter_port() == 8889


def test_port_reads_env_at_call_time(monkeypatch):
    """Not a module-level constant: the value must track the process env the
    launcher exported, even if the module was imported before it changed."""
    monkeypatch.setenv("STUDIO_JUPYTER_PORT", "18889")
    assert _jupyter_port() == 18889


def test_port_probe_sees_a_real_listener(monkeypatch):
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    # Accept in the background so the probe's connect() completes on all
    # platforms (macOS refuses further connects on an un-accepted backlog
    # less readily than Linux, but don't rely on backlog semantics).
    t = threading.Thread(target=lambda: listener.accept(), daemon=True)
    t.start()
    try:
        assert _port_in_use(port) is True
    finally:
        listener.close()


def test_port_probe_reports_a_dead_port():
    # Bind-then-close guarantees the port exists and nothing listens on it.
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    assert _port_in_use(port) is False
