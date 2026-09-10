"""The Pegasus tools, as an agent Studio does not drive will see them.

The OpenCode engine runs its own loop, so MCP is the only way these tools
reach it (#367). Two properties are worth protecting.

The tool list has **one** source: the schemas in `chat_tools`. A second list
written out in MCP shape would drift, and the drifted copy is always the one
nobody is reading — the agent's.

And a tool that raises is a *result*, not a protocol error. An agent that gets
a JSON-RPC error has been told the transport is broken and can only give up; an
agent that gets `isError` with the message can read what went wrong and try
something else.
"""

from __future__ import annotations

import routers.mcp as mcp_router
from services import chat_tools


def _rpc(client, method: str, params: dict | None = None, request_id: int | None = 1):
    body: dict = {"jsonrpc": "2.0", "method": method}
    if request_id is not None:
        body["id"] = request_id
    if params is not None:
        body["params"] = params
    return client.post("/api/mcp", json=body)


def test_initialize_announces_tools_and_echoes_the_protocol(client):
    res = _rpc(client, "initialize", {"protocolVersion": "2025-06-18"})
    assert res.status_code == 200
    result = res.json()["result"]
    assert result["protocolVersion"] == mcp_router.PROTOCOL_VERSION
    assert "tools" in result["capabilities"]
    assert result["serverInfo"]["name"] == "pegasus"
    # The instructions tell the agent these are read-only and how the ids
    # reach it, which is the difference between a tool it uses and one it
    # guesses at.
    assert "read-only" in result["instructions"].lower()


def test_the_tool_list_is_the_one_in_chat_tools(client):
    """No second list to drift. The agent sees exactly what the loop has."""
    res = _rpc(client, "tools/list", request_id=2)
    served = {t["name"] for t in res.json()["result"]["tools"]}
    declared = {
        entry["function"]["name"] for entry in chat_tools.TOOL_DEFINITIONS
    }
    assert served == declared
    # Six read-only Pegasus tools, and none of the builtin loop's file or shell
    # tools: OpenCode has its own, with its own permission gate.
    assert "run_command" not in served
    assert "write_file" not in served


def test_every_tool_carries_a_description_and_a_schema(client):
    """An MCP tool with no description is a tool the model will not call."""
    tools = _rpc(client, "tools/list", request_id=3).json()["result"]["tools"]
    assert tools
    for tool in tools:
        assert tool["description"].strip(), tool["name"]
        schema = tool["inputSchema"]
        assert schema["type"] == "object", tool["name"]
        assert "properties" in schema, tool["name"]


def test_a_call_returns_the_tools_output_as_text(client, monkeypatch):
    seen = {}

    async def fake_execute(name, args, scope):
        seen["name"] = name
        seen["args"] = args
        seen["scope"] = scope
        return '{"projects": []}'

    monkeypatch.setattr(chat_tools, "execute", fake_execute)

    res = _rpc(
        client,
        "tools/call",
        {"name": "list_workflow_projects", "arguments": {}},
        request_id=4,
    )
    result = res.json()["result"]
    assert result["isError"] is False
    assert result["content"] == [{"type": "text", "text": '{"projects": []}'}]
    assert seen["name"] == "list_workflow_projects"
    # No scope: the builtin loop defaults a run id from the panel's context
    # picker, but an MCP call comes from a loop Studio is not driving. The
    # Active Context block names the ids and the model passes them.
    assert seen["scope"] is None


def test_arguments_are_forwarded(client, monkeypatch):
    seen = {}

    async def fake_execute(name, args, scope):
        seen.update(args)
        return "ok"

    monkeypatch.setattr(chat_tools, "execute", fake_execute)
    _rpc(
        client,
        "tools/call",
        {"name": "get_run_jobs", "arguments": {"run_id": "r1", "status": "failed"}},
        request_id=5,
    )
    assert seen == {"run_id": "r1", "status": "failed"}


def test_a_failing_tool_is_a_result_the_agent_can_read(client, monkeypatch):
    """Not a JSON-RPC error, which would read as "the transport is broken"."""

    async def boom(name, args, scope):
        raise RuntimeError("stampede database is locked")

    monkeypatch.setattr(chat_tools, "execute", boom)

    res = _rpc(
        client, "tools/call", {"name": "diagnose_run", "arguments": {}}, request_id=6
    )
    body = res.json()
    assert "error" not in body
    assert body["result"]["isError"] is True
    assert "stampede database is locked" in body["result"]["content"][0]["text"]


def test_non_object_arguments_are_rejected(client):
    res = _rpc(
        client,
        "tools/call",
        {"name": "get_run_status", "arguments": "r1"},
        request_id=7,
    )
    assert res.json()["error"]["code"] == -32600


def test_a_notification_is_answered_with_202_and_no_body(client):
    """`notifications/initialized` is sent by every client and takes no reply."""
    res = client.post(
        "/api/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}
    )
    assert res.status_code == 202
    assert res.content == b""


def test_unknown_methods_and_bad_bodies_are_jsonrpc_errors(client):
    assert _rpc(client, "tools/delete", request_id=8).json()["error"]["code"] == -32601

    res = client.post(
        "/api/mcp", content=b"not json", headers={"content-type": "application/json"}
    )
    assert res.status_code == 400
    assert res.json()["error"]["code"] == -32700


def test_a_batch_answers_only_the_requests(client):
    res = client.post(
        "/api/mcp",
        json=[
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 9, "method": "ping"},
        ],
    )
    assert res.status_code == 200
    replies = res.json()
    assert len(replies) == 1
    assert replies[0]["id"] == 9


def test_no_server_initiated_stream(client):
    """Streamable HTTP lets a client open a GET; this server never pushes."""
    assert client.get("/api/mcp").status_code == 405
