"""The session token gate, and the backend hardening that has to come with it.

#218. Studio has no auth of its own. In Kubernetes that is fine — one pod per
user, its own network namespace, and the gateway decides who reaches the front
port. On a shared host (Open OnDemand, where Apptainer gives the container the
*host* network namespace by default) it is not fine at all: every other local
user can reach the ports directly, and binding loopback does not help, because
loopback separates hosts, not users.

So the gate is four things that switch on together, and the reason they are one
switch is that any one of them alone is a gate with a door beside it:

1. the proxy refuses anything without the session cookie;
2. the internal services bind loopback;
3. JupyterLab gets a real token (empty means terminals for any local user);
4. studio-api requires the proxy-stamped ``X-Studio-Internal``.

These tests cover 4 (which is Python) and the rendering of 1 and 3 (which is
nginx config, checked as text here and parsed by ``nginx -t`` at image build).
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

TOKEN = "test-token_ABC123"


# --- InternalSecretMiddleware -------------------------------------------------


def test_no_secret_is_pure_passthrough(client, monkeypatch):
    """The K8s and plain-container path. A deployment that does not opt in must
    see no change whatsoever."""
    monkeypatch.delenv("STUDIO_INTERNAL_SECRET", raising=False)
    assert client.get("/api/health").status_code == 200


def test_request_without_the_secret_is_refused(client, monkeypatch):
    monkeypatch.setenv("STUDIO_INTERNAL_SECRET", "s3cr3t")
    res = client.get("/api/health")
    assert res.status_code == 403
    assert res.json()["detail"] == "request did not come through the session proxy"


def test_request_with_the_secret_passes(client, monkeypatch):
    monkeypatch.setenv("STUDIO_INTERNAL_SECRET", "s3cr3t")
    res = client.get("/api/health", headers={"X-Studio-Internal": "s3cr3t"})
    assert res.status_code == 200


def test_wrong_secret_is_refused(client, monkeypatch):
    monkeypatch.setenv("STUDIO_INTERNAL_SECRET", "s3cr3t")
    res = client.get("/api/health", headers={"X-Studio-Internal": "not-it"})
    assert res.status_code == 403


def test_the_secret_is_read_per_request(client, monkeypatch):
    """Not captured at import. The entrypoint exports it before uvicorn starts,
    but a constant frozen at import time is how the same value ends up right in
    one process and wrong in another."""
    monkeypatch.delenv("STUDIO_INTERNAL_SECRET", raising=False)
    assert client.get("/api/health").status_code == 200
    monkeypatch.setenv("STUDIO_INTERNAL_SECRET", "later")
    assert client.get("/api/health").status_code == 403


def test_it_guards_websockets_too(client, monkeypatch):
    """The terminal PTYs hand out an interactive shell as the session owner, and
    are the first thing a local attacker would reach for. `@app.middleware`
    never sees a websocket scope, which is why this middleware is raw ASGI."""
    from starlette.websockets import WebSocketDisconnect

    monkeypatch.setenv("STUDIO_INTERNAL_SECRET", "s3cr3t")
    with pytest.raises(WebSocketDisconnect) as excinfo:
        with client.websocket_connect("/ws/terminal") as ws:
            ws.receive_text()
    assert excinfo.value.code == 4403


def test_comparison_is_constant_time():
    """A secret compared with `==` leaks its answer through timing."""
    import inspect

    import security

    src = inspect.getsource(security.InternalSecretMiddleware)
    assert "compare_digest" in src


# --- Rendering ----------------------------------------------------------------


def _render(tmp_path, **env: str) -> dict[str, str]:
    """Run the real entrypoint in --render-only mode; return the rendered files."""
    tmp_path.mkdir(parents=True, exist_ok=True)  # callers may pass a subdir
    script = tmp_path / "entrypoint.sh"
    with open(_ENTRYPOINT, encoding="utf-8") as fh:
        script.write_text(
            fh.read().replace("/opt/pegasus-ai/templates", _TEMPLATES),
            encoding="utf-8",
        )
    runtime = tmp_path / "runtime"
    base = {k: v for k, v in os.environ.items() if not k.startswith("STUDIO_")}
    proc = subprocess.run(
        ["bash", str(script), "--render-only"],
        capture_output=True,
        text=True,
        env={
            **base,
            "HOME": str(tmp_path / "home"),
            "STUDIO_RUNTIME_DIR": str(runtime),
            **env,
        },
    )
    assert proc.returncode == 0, proc.stderr
    return {
        p.name: p.read_text(encoding="utf-8")
        for p in runtime.iterdir()
        if p.is_file()
    }


GATE_FILES = [
    "gate-map.conf",
    "gate-server.conf",
    "gate-api-headers.conf",
    "gate-jupyter-headers.conf",
]


def test_gate_off_renders_every_fragment_inert(tmp_path):
    """The default. The includes that load these are unconditional — an include
    of a missing file is a startup failure — so each must exist and do nothing.
    """
    files = _render(tmp_path)
    for name in GATE_FILES:
        assert name in files, sorted(files)
        directives = [
            ln for ln in files[name].splitlines()
            if ln.strip() and not ln.lstrip().startswith("#")
        ]
        assert directives == [], (name, directives)


def test_gate_off_leaves_jupyter_and_the_api_untouched(tmp_path):
    """No stamped header, no token: byte-for-byte the behaviour shipped today."""
    files = _render(tmp_path)
    assert "X-Studio-Internal" not in files["gate-api-headers.conf"]
    assert "Authorization" not in files["gate-jupyter-headers.conf"]


def test_gate_on_denies_by_default_and_opens_exactly_one_door(tmp_path):
    files = _render(tmp_path, STUDIO_AUTH_TOKEN=TOKEN)
    server = files["gate-server.conf"]

    assert "if ($studio_auth_ok = 0) { set $studio_deny 1; }" in server
    assert "if ($studio_deny) { return 403; }" in server
    # /auth is the one path reachable without the cookie, and it checks the
    # token itself before handing one out.
    assert "/auth$" in server
    assert f'if ($arg_token != "{TOKEN}")' in server
    assert "return 403" in server


def test_gate_on_sets_an_httponly_cookie(tmp_path):
    """HttpOnly so script cannot read the session token; SameSite=Lax so a
    cross-site POST does not carry it."""
    server = _render(tmp_path, STUDIO_AUTH_TOKEN=TOKEN)["gate-server.conf"]
    cookie = re.search(r'Set-Cookie "([^"]+)"', server).group(1)
    assert "HttpOnly" in cookie
    assert "SameSite=Lax" in cookie
    assert cookie.startswith(f"studio_session={TOKEN}")


def test_gate_on_stamps_the_api_and_tokens_jupyter(tmp_path):
    files = _render(tmp_path, STUDIO_AUTH_TOKEN=TOKEN)

    api = files["gate-api-headers.conf"]
    assert api.strip().startswith("proxy_set_header X-Studio-Internal") or (
        "proxy_set_header X-Studio-Internal" in api
    )
    secret = re.search(r'X-Studio-Internal "([^"]+)"', api).group(1)
    assert len(secret) >= 32, secret
    # Not the session token — a single shared value would mean the browser's
    # cookie is also the backend credential.
    assert secret != TOKEN

    jup = files["gate-jupyter-headers.conf"]
    token = re.search(r'Authorization "token ([^"]+)"', jup).group(1)
    assert len(token) >= 32, token
    assert token != TOKEN
    assert token != secret


def test_generated_secrets_differ_between_sessions(tmp_path):
    a = _render(tmp_path / "a", STUDIO_AUTH_TOKEN=TOKEN)
    b = _render(tmp_path / "b", STUDIO_AUTH_TOKEN=TOKEN)
    assert (
        a["gate-api-headers.conf"] != b["gate-api-headers.conf"]
    ), "internal secret must not be reused across sessions"
    assert a["gate-jupyter-headers.conf"] != b["gate-jupyter-headers.conf"]


def test_prefix_is_woven_into_the_auth_location(tmp_path):
    """Under OOD the app is served below /node/<host>/<port>."""
    prefix = "/node/c1/45678"
    server = _render(
        tmp_path, STUDIO_AUTH_TOKEN=TOKEN, STUDIO_URL_PREFIX=prefix
    )["gate-server.conf"]
    assert f"^({prefix})?/auth$" in server
    assert f"^(?<studio_auth_prefix>{prefix})?/auth$" in server


def test_a_token_that_could_break_out_of_the_config_is_refused(tmp_path):
    """The token lands in a sed replacement, an nginx regex, an nginx string
    literal and a cookie. A quote in it would produce a config that either
    fails to parse or, worse, parses into something else."""
    script = tmp_path / "entrypoint.sh"
    with open(_ENTRYPOINT, encoding="utf-8") as fh:
        script.write_text(
            fh.read().replace("/opt/pegasus-ai/templates", _TEMPLATES),
            encoding="utf-8",
        )
    base = {k: v for k, v in os.environ.items() if not k.startswith("STUDIO_")}
    for bad in ['a"b', "a;b", "a b", "a$b", "a/b"]:
        proc = subprocess.run(
            ["bash", str(script), "--render-only"],
            capture_output=True,
            text=True,
            env={
                **base,
                "HOME": str(tmp_path / "home"),
                "STUDIO_RUNTIME_DIR": str(tmp_path / "rt"),
                "STUDIO_AUTH_TOKEN": bad,
            },
        )
        assert proc.returncode != 0, f"{bad!r} was accepted"
        assert "STUDIO_AUTH_TOKEN" in proc.stderr


def test_a_relative_url_prefix_is_refused(tmp_path):
    script = tmp_path / "entrypoint.sh"
    with open(_ENTRYPOINT, encoding="utf-8") as fh:
        script.write_text(
            fh.read().replace("/opt/pegasus-ai/templates", _TEMPLATES),
            encoding="utf-8",
        )
    base = {k: v for k, v in os.environ.items() if not k.startswith("STUDIO_")}
    proc = subprocess.run(
        ["bash", str(script), "--render-only"],
        capture_output=True,
        text=True,
        env={
            **base,
            "HOME": str(tmp_path / "home"),
            "STUDIO_RUNTIME_DIR": str(tmp_path / "rt"),
            "STUDIO_AUTH_TOKEN": TOKEN,
            "STUDIO_URL_PREFIX": "node/c1/45678",
        },
    )
    assert proc.returncode != 0
    assert "STUDIO_URL_PREFIX" in proc.stderr


def test_the_proxy_stamps_every_api_location_and_no_web_location(tmp_path):
    """A location that forgets the stamp is a hole straight past the middleware.

    The web upstream is deliberately not stamped — that is the known, accepted
    residual exposure (#229): it serves the UI shell only, no user data, and
    every API call it triggers goes back through a stamped location.
    """
    proxy = _render(tmp_path, STUDIO_AUTH_TOKEN=TOKEN)["studio-proxy.conf"]

    blocks = re.findall(r"location[^\n]*\{(.*?)\n    \}", proxy, re.DOTALL)
    assert blocks, "no location blocks parsed"
    for body in blocks:
        if "studio_api" in body:
            assert "gate-api-headers.conf" in body, body[:200]
        if re.search(r"proxy_pass http://127\.0\.0\.1:\d+", body):
            assert "gate-jupyter-headers.conf" in body, body[:200]
