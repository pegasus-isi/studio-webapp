# JupyterLab server configuration for PegasusAI Studio.
#
# JupyterLab runs on loopback behind the container's nginx at <prefix>/jupyter/
# (<prefix> is /user/<name> under JupyterHub/KubeSpawner). Authentication is
# the gateway's job, so Jupyter's own token/password login is OFF. Everything
# else jupyter_server offers against cross-site abuse stays ON:
#
# - XSRF (double-submit cookie): every POST/PUT/PATCH/DELETE must echo the
#   `_xsrf` cookie (scoped to base_url) in an X-XSRFToken header. JupyterLab's
#   ServerConnection does that for all of its requests and for every
#   labextension that goes through it (jupyterlab-git included). Nothing else
#   in Studio talks to the Jupyter REST API from the browser: studio-web only
#   links to /jupyter/lab, jupyter-pegasusai-chat iframes the Studio /chat
#   page, and the pegasus-bot client calls studio-api (/api/whoami,
#   /api/pegasus-bot). Disabling this would let any page the user visits spawn
#   a terminal or run kernel code with the gateway cookie (PAS-SEC-03).
# - Origin check: API requests and WebSockets whose Origin does not match the
#   Host header are refused unless that origin is listed in allow_origin.
#   Same-origin traffic passes on the Host match alone, which is why
#   allow_origin is empty. This relies on nginx forwarding the browser's Host
#   WITH its port (`proxy_set_header Host $http_host` in default.conf):
#   `$host` strips the port, and Origin http://localhost:8888 != Host localhost
#   would block every Lab POST and kernel WebSocket on a local run.
# - frame-ancestors 'self' (jupyter_server's default CSP): nothing frames
#   JupyterLab. The Lab -> Studio chat iframe is governed by Studio's headers.
#
# Static settings live HERE. docker/supervisor/scripts/jupyter.sh passes only
# what depends on the runtime environment (base_url, root_dir). Do not add
# security flags to the command line: this file is the single source of truth
# and studio-api/tests/test_jupyter_config.py asserts its shape.
#
# Also here: browser terminals, and PageConfig injection for the vendored
# PegasusAI Bot cell-toolbar client. jupyter-ai and RTC/collaboration are
# deliberately NOT installed (see Dockerfile) — don't configure them here.

import os as _os
import re as _re
import sys as _sys
import getpass as _getpass

c = get_config()  # noqa: F821 — Jupyter config magic


def _env_flag(name: str) -> bool:
    return _os.environ.get(name, "").strip().lower() in ("1", "true", "yes")


def _warn(msg: str) -> None:
    # Jupyter's logger is not configured while this file is evaluated;
    # stderr reaches supervisord and the pod log.
    print(f"[studio] jupyter_server_config: {msg}", file=_sys.stderr)


# --- Server basics ---
# Loopback only: nginx in the same pod is the only client. The pod IP is
# reachable from other pods, so 0.0.0.0 would expose a token-less Jupyter to
# the cluster network.
c.ServerApp.ip = "127.0.0.1"
c.ServerApp.port = 8889
c.ServerApp.base_url = "/jupyter/"  # jupyter.sh overrides with the per-user prefix
c.ServerApp.open_browser = False

# The Host header is the public hub name, not localhost, so jupyter_server's
# DNS-rebinding guard (check_host) must be off; the Origin check below is the
# cross-site control.
c.ServerApp.allow_remote_access = True

# --- Authentication: delegated to the gateway ---
# With no token and no password, jupyter_server serves an anonymous user on
# every request (PasswordIdentityProvider.auth_enabled is False) and XSRF +
# Origin are the controls that remain — so they must stay on (below).
# IdentityProvider.token is the non-deprecated spelling of ServerApp.token;
# PasswordIdentityProvider.hashed_password already defaults to "".
c.IdentityProvider.token = ""

# --- XSRF ---
# STUDIO_JUPYTER_DISABLE_XSRF=1 is an emergency rollback knob for
# studio-system (env only, no rebuild). It reopens PAS-SEC-03; it is never
# set by the image and is logged loudly when used.
_disable_xsrf = _env_flag("STUDIO_JUPYTER_DISABLE_XSRF")
if _disable_xsrf:
    _warn(
        "WARNING: STUDIO_JUPYTER_DISABLE_XSRF is set — JupyterLab XSRF "
        "protection is OFF (PAS-SEC-03). Remove this once the cause is fixed."
    )
c.ServerApp.disable_check_xsrf = _disable_xsrf

# --- Cross-origin policy ---
# "" = same-origin only (Origin must equal Host). STUDIO_JUPYTER_ALLOW_ORIGIN
# may name exactly one additional origin (scheme://host[:port]) if a Studio UI
# is ever served from a different host than JupyterLab. "*" is refused: it
# switches the origin check off for the API and every WebSocket (CSWSH).
_allow_origin = _os.environ.get("STUDIO_JUPYTER_ALLOW_ORIGIN", "").strip()
if _allow_origin and not _re.fullmatch(r"https?://[A-Za-z0-9.\-]+(:\d+)?", _allow_origin):
    _warn(
        f"ignoring STUDIO_JUPYTER_ALLOW_ORIGIN={_allow_origin!r}: must be a "
        "single scheme://host[:port] origin, never '*'. Using same-origin only."
    )
    _allow_origin = ""
c.ServerApp.allow_origin = _allow_origin

# --- Terminals ---
c.ServerApp.terminals_enabled = True
c.TerminalManager.shell_command = ["/bin/bash"]

# --- Tornado settings ---
# No "headers" override: jupyter_server's default Content-Security-Policy is
# `frame-ancestors 'self'; report-uri <base_url>api/security/csp-report`, which
# is what we want. A custom header would REPLACE it wholesale, which is how the
# old `frame-ancestors 'self' *` got here.
c.ServerApp.tornado_settings = {
    # Keep websocket_ping_timeout <= websocket_ping_interval. Otherwise
    # jupyter_server logs a warning on every kernel connect and clamps the
    # timeout itself ("websocket_ping_timeout (90000) cannot be longer than
    # websocket_ping_interval (30000)").
    "websocket_ping_interval": 30000,
    "websocket_ping_timeout": 30000,
}

# --- Collaborative editing (jupyter-collaboration / RTC) ---
# Disabled: with jupyter-collaboration 4.x behind the nginx proxy, opening a
# document hangs (the /api/sessions POST never returns and the collaboration
# websocket fails), surfacing in the UI as "Unable to reconnect to the server".
# The studio is single-user, so RTC is not needed — disabling it makes
# JupyterLab use the classic (non-RTC) document model, which opens reliably.
try:
    c.YDocExtension.disable_rtc = True
except Exception:
    pass

# --- PegasusAI Bot cell-toolbar client (@gclab/pegasus-ai-client) ---
# Auto-configure the optional TauferLab bot client without a manual form:
# expose the fixed server URL and the environment-derived user to the
# frontend via PageConfig (our configPlugin overlay reads these). Distinct
# from the studio's own jupyter-pegasusai-chat extension. Set
# STUDIO_ENABLE_PEGASUS_BOT=0 to skip injection (the extension then stays
# inert / falls back to its manual settings form).
if _os.environ.get("STUDIO_ENABLE_PEGASUS_BOT", "1").lower() not in (
    "0", "false", "no",
):
    _bot_user = (
        _os.environ.get("JUPYTERHUB_USER")
        or _os.environ.get("STUDIO_USER")
        or _os.environ.get("USER")
        or ""
    )
    if not _bot_user:
        try:
            _bot_user = _getpass.getuser()
        except Exception:
            _bot_user = ""
    # The extension fetches its server from the BROWSER, but the bot server
    # (PEGASUS_BOT_SERVER_URL, e.g. http://server.ai-bot:8000) is a
    # cluster-internal HTTP service — unreachable from the browser (mixed
    # content on the HTTPS origin + internal DNS). So point the extension at a
    # same-origin proxy (studio-api /api/pegasus-bot forwards to the real
    # server from inside the pod). Prefix-aware so it routes to this user's
    # pod under JupyterHub. Set PEGASUS_BOT_CLIENT_URL to override (e.g. a
    # browser-reachable server, skipping the proxy).
    _prefix = _os.environ.get("JUPYTERHUB_SERVICE_PREFIX", "").rstrip("/")
    _bot_client_url = _os.environ.get(
        "PEGASUS_BOT_CLIENT_URL", f"{_prefix}/api/pegasus-bot"
    )
    # LabApp.page_config_data surfaces as PageConfig.getOption(...) in the
    # JupyterLab frontend. Evaluated at server start, so it sees the per-user
    # environment (JUPYTERHUB_USER etc.).
    c.LabApp.page_config_data = {
        "pegasusBotServerUrl": _bot_client_url,
        "pegasusBotUserId": _bot_user,
    }
