# JupyterLab server configuration for PegasusAI Studio.
#
# This config enables:
# - Terminals in the browser
# - Iframe embedding (no X-Frame-Options restriction)
# - Permissive CSP for embedding in Studio UI
# - jupyter-ai integration with LLM providers

c = get_config()  # noqa: F821 — Jupyter config magic

# --- Server basics ---
import os as _os_port

c.ServerApp.ip = _os_port.environ.get("STUDIO_BIND_ADDR", "0.0.0.0")
# jupyter.sh passes --ip/--port on the command line, which wins over this
# file. Both read the same environment so the two cannot disagree, and a
# jupyter started without the wrapper still lands on the session's port.
c.ServerApp.port = int(_os_port.environ.get("STUDIO_JUPYTER_PORT") or 8889)
c.ServerApp.base_url = "/jupyter/"
c.ServerApp.token = ""
c.ServerApp.password = ""
c.ServerApp.open_browser = False
c.ServerApp.allow_origin = "*"
c.ServerApp.allow_remote_access = True

# --- Terminals ---
c.ServerApp.terminals_enabled = True
c.TerminalManager.shell_command = ["/bin/bash"]

# --- Iframe embedding ---
# Disable X-Frame-Options so JupyterLab can be embedded in an iframe
c.ServerApp.tornado_settings = {
    "headers": {
        "Content-Security-Policy": (
            "frame-ancestors 'self' * ; "
            "default-src 'self' 'unsafe-inline' 'unsafe-eval' "
            "data: blob: ws: wss: http: https: ;"
        ),
    },
    # Keep websocket_ping_timeout <= websocket_ping_interval. Otherwise
    # jupyter_server logs a warning on every kernel connect and clamps the
    # timeout itself ("websocket_ping_timeout (90000) cannot be longer than
    # websocket_ping_interval (30000)").
    "websocket_ping_interval": 30000,
    "websocket_ping_timeout": 30000,
}

# --- Disable XSRF for API calls from the Studio iframe ---
c.ServerApp.disable_check_xsrf = True

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

# --- jupyter-ai configuration ---
# jupyter-ai reads API keys from environment variables set by the
# Studio API before launching JupyterLab. No static config needed.
# Supported env vars: ANTHROPIC_API_KEY, OPENAI_API_KEY, etc.

# --- PegasusAI Bot cell-toolbar client (@gclab/pegasus-ai-client) ---
# Auto-configure the optional TauferLab bot client without a manual form:
# expose the fixed server URL and the environment-derived user to the
# frontend via PageConfig (our configPlugin overlay reads these). Distinct
# from the studio's own jupyter-pegasusai-chat extension. Set
# STUDIO_ENABLE_PEGASUS_BOT=0 to skip injection (the extension then stays
# inert / falls back to its manual settings form).
import os as _os
import getpass as _getpass

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
