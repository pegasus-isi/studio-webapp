# JupyterLab server configuration for PegasusAI Studio.
#
# This config enables:
# - Terminals in the browser
# - Iframe embedding (no X-Frame-Options restriction)
# - Permissive CSP for embedding in Studio UI
# - jupyter-ai integration with LLM providers

c = get_config()  # noqa: F821 — Jupyter config magic

# --- Server basics ---
c.ServerApp.ip = "0.0.0.0"
c.ServerApp.port = 8889
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
