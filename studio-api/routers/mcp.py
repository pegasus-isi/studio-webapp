"""The Pegasus tools, served to the chat engine over MCP (#367).

The OpenCode engine runs its own agent loop, so it cannot call the tool
functions in ``services/chat_tools.py`` directly. MCP is how a tool reaches an
agent Studio does not own, and this endpoint is the
Pegasus half of what the chat knows: the user's workflow projects, their runs,
live job status, a failure diagnosis and a run's outputs.

Two decisions worth stating.

**One source of truth for the tools.** The schemas are read from
``chat_tools.TOOL_DEFINITIONS`` (OpenAI function shape, a holdover from the
builtin loop removed in #664) and translated to MCP shape here. Add a Pegasus
tool there and it is served here.

**Read-only, and only the Pegasus tools.** Generic file and shell tools are
deliberately absent: OpenCode has its own,
with its own permission system, and a second `run_command` reachable from the
same session would be a second gate to keep honest. Nothing here writes.

The transport is MCP's Streamable HTTP, stateless: one JSON-RPC request per
POST, answered with JSON. No session id is issued because there is no
per-session state to key — every call is resolved against the user's own
filesystem and database at request time.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from services import chat_tools

log = structlog.get_logger()

router = APIRouter(prefix="/api/mcp", tags=["mcp"])

# The revision of the MCP spec this endpoint implements. Echoed back on
# initialize so a client can decide whether it can talk to us.
PROTOCOL_VERSION = "2025-06-18"
SERVER_NAME = "pegasus"

# JSON-RPC 2.0 error codes used here.
_PARSE_ERROR = -32700
_INVALID_REQUEST = -32600
_METHOD_NOT_FOUND = -32601
_INTERNAL_ERROR = -32603


def _tools() -> list[dict[str, Any]]:
    """The Pegasus tools in MCP's shape, from the one place they are defined.

    ``chat_tools`` declares them in OpenAI's function-calling form, which
    differs from MCP only in nesting and in calling the schema
    ``parameters`` rather than ``inputSchema``.
    """
    out: list[dict[str, Any]] = []
    for entry in chat_tools.TOOL_DEFINITIONS:
        fn = entry.get("function") or {}
        name = fn.get("name")
        if not name:
            continue
        out.append({
            "name": name,
            "description": fn.get("description", ""),
            "inputSchema": fn.get("parameters")
            or {"type": "object", "properties": {}},
        })
    return out


def _result(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


async def _handle(message: dict[str, Any]) -> dict[str, Any] | None:
    """Answer one JSON-RPC message, or None for a notification."""
    method = message.get("method")
    request_id = message.get("id")
    params = message.get("params") or {}

    # A notification has no id and takes no reply — `notifications/initialized`
    # is the one every client sends.
    if request_id is None:
        return None

    if method == "initialize":
        return _result(
            request_id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": "1"},
                "instructions": (
                    "Pegasus workflow tools for this Studio session: list the"
                    " user's workflow projects and runs, read a run's live"
                    " status, jobs and outputs, and diagnose a failed run."
                    " Read-only. Pass the workflow or run id named in the"
                    " conversation's Active Context when there is one."
                ),
            },
        )

    if method == "ping":
        return _result(request_id, {})

    if method == "tools/list":
        return _result(request_id, {"tools": _tools()})

    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            return _error(request_id, _INVALID_REQUEST, "arguments must be an object")
        try:
            # scope=None on purpose: an MCP call arrives from an agent Studio
            # is not driving, so there is no context picker to default a
            # tool's run/project id from. The Active Context
            # block names the ids instead, and the model passes them.
            output = await chat_tools.execute(str(name), arguments, None)
        except Exception as e:
            log.warning("mcp_tool_failed", tool=name, error=str(e))
            # A failed tool is a result, not a protocol error: the agent should
            # see what went wrong and be able to try something else, which a
            # JSON-RPC error would deny it.
            return _result(
                request_id,
                {
                    "content": [{"type": "text", "text": f"Tool failed: {e}"}],
                    "isError": True,
                },
            )
        log.info("mcp_tool_called", tool=name)
        return _result(
            request_id,
            {"content": [{"type": "text", "text": output}], "isError": False},
        )

    return _error(request_id, _METHOD_NOT_FOUND, f"Unknown method: {method}")


@router.post("")
@router.post("/")
async def endpoint(request: Request) -> Response:
    """One MCP JSON-RPC request (or a batch of them)."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            _error(None, _PARSE_ERROR, "Body is not valid JSON"), status_code=400
        )

    # A batch is a list. Notifications drop out, so a batch of only
    # notifications answers 202 with no body, as the spec requires.
    if isinstance(body, list):
        replies = [r for r in [await _handle(m) for m in body if isinstance(m, dict)] if r]
        if not replies:
            return Response(status_code=202)
        return JSONResponse(replies)

    if not isinstance(body, dict):
        return JSONResponse(
            _error(None, _INVALID_REQUEST, "Expected a JSON-RPC object"),
            status_code=400,
        )

    reply = await _handle(body)
    if reply is None:
        return Response(status_code=202)
    return JSONResponse(reply)


@router.get("")
@router.get("/")
async def unsupported_stream() -> Response:
    """No server-initiated stream.

    Streamable HTTP allows a client to open a GET for messages the server
    starts. This server never does — it has no subscriptions and nothing to
    push — and 405 is how the spec says to tell a client that.
    """
    return Response(status_code=405)
