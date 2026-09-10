"""Every listener is configurable, and the rendered nginx config proves it.

The container used to own its ports outright: one pod per user, a private
network namespace, fixed 8888/8080/3000/8889/4096. Under Open OnDemand the same
image runs on a shared host — with Apptainer it does not even get its own
network namespace by default — so two sessions would bind the same ports and
the second would lose.

These tests run the *real* `render_template` out of `docker/entrypoint.sh`
against the *real* templates, rather than reimplementing the substitution. A
test that reimplemented it would pass while the entrypoint's own sed list drifts
away from the placeholders the templates use, which is precisely the failure
worth catching: nginx reports an unsubstituted `@STUDIO_API_PORT@` as a parse
error naming a line number, not a cause.
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

_PLACEHOLDER = re.compile(r"@STUDIO_[A-Z_]+@")

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None, reason="bash is required to run the renderer"
)


def _render(tmp_path, template: str, **env: str) -> str:
    """Render one template with entrypoint.sh's own render_template()."""
    with open(_ENTRYPOINT, encoding="utf-8") as fh:
        script = fh.read()

    # Take the function verbatim from the entrypoint. Anchored on the closing
    # brace at column zero, so it stops at the end of the function and not at
    # the first nested one.
    match = re.search(
        r"^render_template\(\) \{\n(.*?)^\}$", script, re.MULTILINE | re.DOTALL
    )
    assert match, "render_template() not found in docker/entrypoint.sh"
    body = match.group(0)

    dst = str(tmp_path / "rendered.conf")
    src = os.path.join(_TEMPLATES, template)
    proc = subprocess.run(
        ["bash", "-c", f'set -euo pipefail\n{body}\nrender_template "$1" "$2"',
         "_", src, dst],
        capture_output=True,
        text=True,
        env={**os.environ, **GATE_OFF, **env},
    )
    assert proc.returncode == 0, proc.stderr
    with open(dst, encoding="utf-8") as fh:
        return fh.read()


# The variables render_template substitutes. The gate ones are empty here —
# these tests are about ports, and the entrypoint always defines all of them
# before calling the function, so extracting it alone has to supply them or
# `set -u` aborts. The gate's own rendering is covered in test_session_gate.py.
GATE_OFF = {
    "STUDIO_AUTH_TOKEN": "",
    "STUDIO_URL_PREFIX": "",
    "STUDIO_JUPYTER_TOKEN": "",
    "STUDIO_INTERNAL_SECRET": "",
}

# Deliberately not superstrings of the defaults: "18888" contains "8888", so a
# template that never got substituted would still satisfy the stale-port check.
ALL_PORTS = {
    "STUDIO_PORT": "21111",
    "STUDIO_API_PORT": "22222",
    "STUDIO_WEB_PORT": "23333",
    "STUDIO_JUPYTER_PORT": "24444",
    "STUDIO_OPENCODE_PORT": "25555",
    "STUDIO_RUNTIME_DIR": "/tmp/studio-runtime-4242",
}


def test_proxy_template_takes_every_port(tmp_path):
    out = _render(tmp_path, "studio-proxy.conf.tmpl", **ALL_PORTS)

    assert "listen 21111;" in out
    assert "server 127.0.0.1:22222;" in out  # upstream studio_api
    assert "server 127.0.0.1:23333;" in out  # upstream studio_web
    assert "proxy_pass http://127.0.0.1:24444;" in out
    assert "proxy_pass http://127.0.0.1:24444/jupyter/;" in out

    # The old fixed ports must be gone from the directives — a half-rendered
    # config binds the new front port and proxies to a backend nobody started.
    # Comments are excluded: the template's own header names the defaults.
    directives = "\n".join(
        ln for ln in out.splitlines() if not ln.lstrip().startswith("#")
    )
    for stale in ("8888", ":8080", ":3000", ":8889"):
        assert stale not in directives, f"{stale} survived rendering"


def test_nginx_template_keeps_runtime_state_per_session(tmp_path):
    out = _render(tmp_path, "nginx.conf.tmpl", **ALL_PORTS)

    runtime = ALL_PORTS["STUDIO_RUNTIME_DIR"]
    assert f"pid {runtime}/nginx.pid;" in out
    # Every temp path moves with it: two users sharing /tmp on an OOD host
    # would otherwise write each other's spool directories.
    for temp in ("client-body", "proxy", "fastcgi", "uwsgi", "scgi"):
        assert f"{runtime}/nginx-{temp}" in out

    includes = re.findall(r"^\s*include\s+(\S+);", out, re.MULTILINE)
    assert f"{runtime}/studio-proxy.conf" in includes
    # The distro's own vhosts must not be pulled in: on a shared host they are
    # someone else's, and they listen on :80.
    assert not any("sites-enabled" in i or "conf.d" in i for i in includes), includes


@pytest.mark.parametrize(
    "template", ["studio-proxy.conf.tmpl", "nginx.conf.tmpl"]
)
def test_no_placeholder_survives(tmp_path, template):
    out = _render(tmp_path, template, **ALL_PORTS)
    assert not _PLACEHOLDER.search(out), _PLACEHOLDER.findall(out)


def test_every_template_placeholder_is_substituted():
    """The renderer's sed list must cover what the templates actually use.

    This is the drift the runtime guard catches loudly and late; catching it
    here means a template gaining a placeholder fails in CI instead of on a
    user's first launch.
    """
    with open(_ENTRYPOINT, encoding="utf-8") as fh:
        script = fh.read()
    substituted = set(re.findall(r"s\|(@STUDIO_[A-Z_]+@)\|", script))

    used: set[str] = set()
    for name in os.listdir(_TEMPLATES):
        if not name.endswith(".tmpl"):
            continue
        with open(os.path.join(_TEMPLATES, name), encoding="utf-8") as fh:
            used |= set(_PLACEHOLDER.findall(fh.read()))

    assert used <= substituted, f"unsubstituted: {sorted(used - substituted)}"


def test_renderer_refuses_to_emit_an_unsubstituted_config(tmp_path):
    """The guard itself works — otherwise every test above is vacuous."""
    bogus = tmp_path / "bogus.conf.tmpl"
    bogus.write_text("listen @STUDIO_NOT_A_REAL_PORT@;\n", encoding="utf-8")

    with open(_ENTRYPOINT, encoding="utf-8") as fh:
        body = re.search(
            r"^render_template\(\) \{\n(.*?)^\}$",
            fh.read(),
            re.MULTILINE | re.DOTALL,
        ).group(0)

    proc = subprocess.run(
        ["bash", "-c", f'set -euo pipefail\n{body}\nrender_template "$1" "$2"',
         "_", str(bogus), str(tmp_path / "out.conf")],
        capture_output=True,
        text=True,
        env={**os.environ, **GATE_OFF, **ALL_PORTS},
    )
    assert proc.returncode != 0
    assert "unsubstituted placeholder" in proc.stderr


# --- The Python side of the same contract ------------------------------------
#
# routers/jupyter.py reports the port the browser should open. It used to be a
# module constant, which is the shape that silently disagrees with the launcher
# once the launcher is allowed to pick.


def test_jupyter_port_follows_the_environment(monkeypatch):
    from routers import jupyter

    monkeypatch.setenv("STUDIO_JUPYTER_PORT", "24444")
    assert jupyter.jupyter_port() == 24444

    # Read per call, not frozen at import: the same process must see a change.
    monkeypatch.setenv("STUDIO_JUPYTER_PORT", "25555")
    assert jupyter.jupyter_port() == 25555


def test_jupyter_port_defaults_when_unset(monkeypatch):
    from routers import jupyter

    monkeypatch.delenv("STUDIO_JUPYTER_PORT", raising=False)
    assert jupyter.jupyter_port() == jupyter.DEFAULT_JUPYTER_PORT == 8889


@pytest.mark.parametrize("bad", ["", "   ", "not-a-port"])
def test_jupyter_port_falls_back_rather_than_raising(monkeypatch, bad):
    """A bad value must not take the status endpoint down with a ValueError."""
    from routers import jupyter

    monkeypatch.setenv("STUDIO_JUPYTER_PORT", bad)
    assert jupyter.jupyter_port() == jupyter.DEFAULT_JUPYTER_PORT


# --- supervisord ------------------------------------------------------------
#
# The ports reach uvicorn, Next.js and nginx through supervisord's own
# %(ENV_VAR)s expansion. There is no default syntax for those, so a typo in a
# variable name is not a wrong port — it is a supervisord that refuses to start,
# taking every service with it.


def test_supervisord_config_expands_the_port_environment(tmp_path, monkeypatch):
    options = pytest.importorskip(
        "supervisor.options", reason="supervisor is not installed"
    )

    runtime = tmp_path / "runtime"
    runtime.mkdir()
    monkeypatch.setenv("STUDIO_RUNTIME_DIR", str(runtime))
    monkeypatch.setenv("STUDIO_BIND_ADDR", "127.0.0.1")
    monkeypatch.setenv("STUDIO_API_PORT", "22222")
    monkeypatch.setenv("STUDIO_WEB_PORT", "23333")
    # Referenced by the studio-web program; supervisord has no default syntax
    # for %(ENV_...)s, so it must be defined even when empty.
    monkeypatch.setenv("STUDIO_INTERNAL_SECRET", "")

    opts = options.ServerOptions()
    opts.configfile = os.path.join(_REPO, "docker", "supervisor", "supervisord.conf")
    opts.process_config(do_usage=False)

    commands = {
        pc.name: pc
        for group in opts.process_group_configs
        for pc in group.process_configs
    }

    assert commands["studio-api"].command == (
        "uvicorn main:app --host 127.0.0.1 --port 22222"
    )
    # Next.js standalone takes its listener from the environment, not argv.
    assert commands["studio-web"].environment["PORT"] == "23333"
    assert commands["studio-web"].environment["HOSTNAME"] == "127.0.0.1"
    # And it must be told where studio-api is. Server components fetch it
    # directly (lib/server/ui-prefs.ts, the app-root redirect) and fall back to
    # a literal localhost:8080 — which, once the API port is allocated, is a
    # port nothing is listening on. That module's failure path returns "show
    # the welcome page", so the symptom would be every user stuck on the
    # welcome screen with nothing logged.
    assert (
        commands["studio-web"].environment["API_URL"] == "http://127.0.0.1:22222"
    )
    # Named explicitly rather than left to inherit: server components call
    # studio-api on loopback, so on a gated session they must present the
    # header the front proxy would otherwise have stamped (#391).
    assert "STUDIO_INTERNAL_SECRET" in commands["studio-web"].environment
    # nginx must be pointed at the rendered config; the baked one is gone.
    assert f"-c {runtime}/nginx.conf" in commands["nginx"].command

    # Runtime state is per session: on a shared host these are what two users
    # would otherwise collide on.
    assert opts.pidfile == f"{runtime}/supervisord.pid"


# --- The entrypoint end to end ----------------------------------------------


def _run_entrypoint(tmp_path, **env: str):
    """Run entrypoint.sh --render-only against the real templates.

    The image keeps templates at /opt/pegasus-ai/templates, which does not exist
    on a developer machine, so the copy under test is redirected at the repo's
    own docker/nginx. Everything else — port defaults, allocation, rendering,
    the placeholder guard — is the real script.
    """
    script = tmp_path / "entrypoint.sh"
    with open(_ENTRYPOINT, encoding="utf-8") as fh:
        script.write_text(
            fh.read().replace("/opt/pegasus-ai/templates", _TEMPLATES),
            encoding="utf-8",
        )

    home = tmp_path / "home"  # deliberately NOT created
    runtime = tmp_path / "runtime"
    base = {
        k: v for k, v in os.environ.items()
        if not k.startswith("STUDIO_")
    }
    return subprocess.run(
        ["bash", str(script), "--render-only"],
        capture_output=True,
        text=True,
        env={
            **base,
            "HOME": str(home),
            "STUDIO_RUNTIME_DIR": str(runtime),
            **env,
        },
    ), home, runtime


def test_defaults_reproduce_the_historical_container_ports(tmp_path):
    """No env set must give exactly the ports the image always used."""
    proc, _, runtime = _run_entrypoint(tmp_path)
    assert proc.returncode == 0, proc.stderr

    assert "front=8888" in proc.stdout
    assert "api=8080" in proc.stdout
    assert "web=3000" in proc.stdout
    assert "jupyter=8889" in proc.stdout
    assert "opencode=4096" in proc.stdout

    proxy = (runtime / "studio-proxy.conf").read_text(encoding="utf-8")
    assert "listen 8888;" in proxy
    assert "server 127.0.0.1:8080;" in proxy


def test_bind_addr_defaults_to_loopback(tmp_path):
    """#229: uvicorn and Next must not land on the pod's cluster IP, nor on a
    shared host's interfaces, just because nobody set the variable."""
    proc, _, _ = _run_entrypoint(tmp_path)
    assert "bind=127.0.0.1" in proc.stdout


def test_alloc_ports_gives_four_distinct_non_default_ports(tmp_path):
    proc, _, _ = _run_entrypoint(tmp_path, STUDIO_ALLOC_PORTS="1")
    assert proc.returncode == 0, proc.stderr

    # Only the Ports: line — the next line of output is the render message.
    line = proc.stdout.split("Ports: ")[1].splitlines()[0]
    ports = dict(kv.split("=", 1) for kv in line.split())
    internal = [ports[k] for k in ("api", "web", "jupyter", "opencode")]
    assert len(set(internal)) == 4, internal
    assert all(p not in ("8080", "3000", "8889", "4096") for p in internal)
    # The front port is never auto-allocated — nothing would route to a guess.
    assert ports["front"] == "8888"


def test_explicit_port_wins_over_allocation(tmp_path):
    proc, _, _ = _run_entrypoint(
        tmp_path, STUDIO_ALLOC_PORTS="1", STUDIO_API_PORT="21999"
    )
    assert "api=21999" in proc.stdout


def test_render_only_leaves_home_alone(tmp_path):
    """The image build runs this as root; it must create nothing under $HOME."""
    proc, home, _ = _run_entrypoint(tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert not home.exists(), sorted(p.name for p in home.iterdir())
