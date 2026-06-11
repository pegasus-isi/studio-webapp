"""Built-in PegasusAI chat — SSE streaming with tool use.

Ported from pegasus-ai-extension/handlers.py (Tornado → FastAPI).
Supports both Anthropic Messages API and OpenAI-compatible endpoints.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from typing import Any, AsyncGenerator

import httpx
import structlog
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from models import ChatHistoryResponse, ChatMessage

log = structlog.get_logger()

router = APIRouter(prefix="/api/chat", tags=["chat"])

KNOWLEDGE_ROOT = os.environ.get(
    "KNOWLEDGE_ROOT", "/opt/pegasus-ai/knowledge"
)

# Active streaming clients tracked by request_id
_active_clients: dict[str, httpx.AsyncClient] = {}

# Base URLs discovered to require the OpenAI *Responses* API (e.g. gpt-5.5 on
# the FABRIC/RENCI proxy) rather than Chat Completions. Populated on first 400.
_RESPONSES_ENDPOINTS: set[str] = set()
_RESPONSES_HINTS = (
    "moved to 'input'",
    "Responses API",
    "Unsupported parameter: 'messages'",
)

# Max tool-calling rounds
_MAX_TOOL_ROUNDS = 50

# Tool definitions for the built-in chat (file ops + Pegasus commands)
TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write content to a file in ~/work/. Creates parent directories.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path relative to ~/work/"},
                    "content": {"type": "string", "description": "Content to write"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file from ~/work/ (max 100KB).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path relative to ~/work/"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": "List files and directories in ~/work/.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Directory path (default: ~/work/)"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Execute a shell command in ~/work/ (5 min timeout).",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to execute"},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_directory",
            "description": "Create a directory in ~/work/.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Directory path"},
                },
                "required": ["path"],
            },
        },
    },
]

# Anthropic-format tool definitions
TOOL_DEFINITIONS_ANTHROPIC = [
    {
        "name": t["function"]["name"],
        "description": t["function"]["description"],
        "input_schema": t["function"]["parameters"],
    }
    for t in TOOL_DEFINITIONS
]

WORKSPACE_ROOT = os.environ.get(
    "STUDIO_WORKSPACE_DIR",
    os.path.join(os.environ.get("HOME", "/tmp"), "work"),
)

_MAX_READ_BYTES = 100 * 1024
_MAX_OUTPUT_BYTES = 8 * 1024
_CMD_TIMEOUT = 300


# --- Tool execution ---

def _safe_path(path: str) -> str:
    """Resolve and validate path inside workspace."""
    if not path:
        return WORKSPACE_ROOT
    if not os.path.isabs(path):
        path = os.path.join(WORKSPACE_ROOT, path)
    resolved = os.path.realpath(os.path.expanduser(path))
    root = os.path.realpath(WORKSPACE_ROOT)
    if not resolved.startswith(root + os.sep) and resolved != root:
        raise ValueError(f"Access denied: path must be inside {WORKSPACE_ROOT}")
    return resolved


async def _execute_tool(name: str, arguments: dict[str, Any]) -> str:
    """Execute a tool and return result string."""
    import asyncio
    import shutil

    try:
        if name == "write_file":
            path = _safe_path(arguments["path"])
            content = arguments["content"]
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                f.write(content)
            return f"Wrote {len(content)} bytes to {path}"

        elif name == "read_file":
            path = _safe_path(arguments["path"])
            with open(path) as f:
                return f.read(_MAX_READ_BYTES)

        elif name == "list_directory":
            path = _safe_path(arguments.get("path", ""))
            entries = sorted(os.listdir(path))
            result = []
            for entry in entries:
                full = os.path.join(path, entry)
                kind = "dir" if os.path.isdir(full) else "file"
                size = os.path.getsize(full) if os.path.isfile(full) else ""
                result.append(f"{kind}\t{entry}\t{size}")
            return "\n".join(result) if result else "(empty directory)"

        elif name == "create_directory":
            path = _safe_path(arguments["path"])
            os.makedirs(path, exist_ok=True)
            return f"Created directory: {path}"

        elif name == "run_command":
            command = arguments["command"]
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=WORKSPACE_ROOT,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=_CMD_TIMEOUT
            )
            out = stdout.decode("utf-8", errors="replace")[:_MAX_OUTPUT_BYTES]
            err = stderr.decode("utf-8", errors="replace")[:_MAX_OUTPUT_BYTES]
            parts = []
            if out:
                parts.append(f"stdout:\n{out}")
            if err:
                parts.append(f"stderr:\n{err}")
            parts.append(f"exit code: {proc.returncode}")
            return "\n".join(parts)

        return f"Unknown tool: {name}"

    except ValueError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error executing {name}: {e}"


# --- System prompt ---

def _load_system_prompt(agent_id: str | None = None) -> str:
    """Build system prompt from PEGASUS_AI.md + tool instructions + agent persona."""
    parts = []

    # Load shared context
    shared_path = os.path.join(KNOWLEDGE_ROOT, "references", "PEGASUS_AI.md")
    if os.path.isfile(shared_path):
        try:
            with open(shared_path) as f:
                parts.append(f.read())
        except Exception:
            pass

    parts.append(
        "\n\n## Tool Use Instructions\n\n"
        "You have access to tools for file operations and shell commands. "
        "Use them to help the user create, run, and debug Pegasus workflows.\n\n"
        "WORKSPACE RULES:\n"
        f"- All files MUST be created under {WORKSPACE_ROOT}\n"
        "- Use kebab-case directory names (e.g., csv-summary-workflow/)\n"
    )

    # Agent persona
    if agent_id and agent_id != "general":
        agent_path = os.path.join(KNOWLEDGE_ROOT, "agents", f"{agent_id}.md")
        if os.path.isfile(agent_path):
            try:
                with open(agent_path) as f:
                    content = f.read()
                # Strip YAML frontmatter
                fm_match = re.match(r"^---\s*\n.*?\n---\s*\n(.*)$", content, re.DOTALL)
                if fm_match:
                    content = fm_match.group(1).strip()
                parts.append(f"\n\n## Agent Persona\n\n{content}")
            except Exception:
                pass

    return "\n".join(parts) if parts else "You are a helpful Pegasus WMS workflow assistant."


# --- LLM request helpers ---

async def _anthropic_request(
    client: httpx.AsyncClient,
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict],
    system_prompt: str,
) -> dict[str, Any]:
    """Non-streaming request to Anthropic Messages API."""
    url = f"{base_url}/v1/messages"
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    body = {
        "model": model,
        "max_tokens": 8192,
        "system": system_prompt,
        "messages": messages,
        "tools": TOOL_DEFINITIONS_ANTHROPIC,
    }

    resp = await client.post(url, json=body, headers=headers)
    resp.raise_for_status()
    data = resp.json()

    result: dict[str, Any] = {"content": "", "tool_calls": [], "stop_reason": data.get("stop_reason", "")}
    for block in data.get("content", []):
        if block["type"] == "text":
            result["content"] += block["text"]
        elif block["type"] == "tool_use":
            result["tool_calls"].append({
                "id": block["id"],
                "name": block["name"],
                "arguments": block["input"],
            })
    return result


async def _openai_request(
    client: httpx.AsyncClient,
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict],
    system_prompt: str,
) -> dict[str, Any]:
    """Non-streaming request to OpenAI-compatible chat completions."""
    url = f"{base_url}/chat/completions"
    headers: dict[str, str] = {"content-type": "application/json"}
    if api_key:
        headers["authorization"] = f"Bearer {api_key}"

    full_messages = [{"role": "system", "content": system_prompt}] + messages
    body = {
        "model": model,
        "messages": full_messages,
        "tools": TOOL_DEFINITIONS,
        "max_tokens": 8192,
    }

    resp = await client.post(url, json=body, headers=headers)
    resp.raise_for_status()
    data = resp.json()

    choice = data["choices"][0]
    msg = choice["message"]
    result: dict[str, Any] = {
        "content": msg.get("content", "") or "",
        "tool_calls": [],
        "stop_reason": choice.get("finish_reason", ""),
    }
    for tc in msg.get("tool_calls", []) or []:
        fn = tc.get("function", {})
        try:
            args = json.loads(fn.get("arguments", "{}"))
        except json.JSONDecodeError:
            args = {}
        result["tool_calls"].append({
            "id": tc.get("id", str(uuid.uuid4())),
            "name": fn.get("name", ""),
            "arguments": args,
        })
    return result


def _tools_for_responses() -> list[dict[str, Any]]:
    """Convert Chat-Completions tool defs to the flat Responses-API schema."""
    out: list[dict[str, Any]] = []
    for t in TOOL_DEFINITIONS:
        fn = t.get("function", t)
        out.append({
            "type": "function",
            "name": fn.get("name", ""),
            "description": fn.get("description", ""),
            "parameters": fn.get("parameters", {}),
        })
    return out


def _messages_to_responses_input(messages: list[dict]) -> list[dict[str, Any]]:
    """Translate the chat-format conversation into Responses-API input items.

    - user/assistant text   -> {role, content}
    - assistant tool_calls   -> {type: function_call, call_id, name, arguments}
    - tool results           -> {type: function_call_output, call_id, output}
    """
    items: list[dict[str, Any]] = []
    for m in messages:
        role = m.get("role")
        if role == "tool":
            items.append({
                "type": "function_call_output",
                "call_id": m.get("tool_call_id", ""),
                "output": m.get("content", "") or "",
            })
            continue
        tool_calls = m.get("tool_calls")
        if role == "assistant" and tool_calls:
            if m.get("content"):
                items.append({"role": "assistant", "content": m["content"]})
            for tc in tool_calls:
                fn = tc.get("function", {})
                items.append({
                    "type": "function_call",
                    "call_id": tc.get("id", ""),
                    "name": fn.get("name", ""),
                    "arguments": fn.get("arguments", "{}"),
                })
            continue
        content = m.get("content")
        if content:
            items.append({"role": role, "content": content})
    return items


async def _openai_responses_request(
    client: httpx.AsyncClient,
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict],
    system_prompt: str,
) -> dict[str, Any]:
    """Non-streaming request to the OpenAI Responses API (/responses)."""
    url = f"{base_url}/responses"
    headers: dict[str, str] = {"content-type": "application/json"}
    if api_key:
        headers["authorization"] = f"Bearer {api_key}"

    body = {
        "model": model,
        "instructions": system_prompt,
        "input": _messages_to_responses_input(messages),
        "tools": _tools_for_responses(),
        "max_output_tokens": 8192,
    }

    resp = await client.post(url, json=body, headers=headers)
    resp.raise_for_status()
    data = resp.json()

    content_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    for item in data.get("output", []) or []:
        itype = item.get("type")
        if itype == "message":
            for c in item.get("content", []) or []:
                if c.get("type") == "output_text" and c.get("text"):
                    content_parts.append(c["text"])
        elif itype == "function_call":
            try:
                args = json.loads(item.get("arguments", "{}") or "{}")
            except json.JSONDecodeError:
                args = {}
            tool_calls.append({
                "id": item.get("call_id") or item.get("id") or str(uuid.uuid4()),
                "name": item.get("name", ""),
                "arguments": args,
            })

    return {
        "content": "".join(content_parts),
        "tool_calls": tool_calls,
        "stop_reason": data.get("status", ""),
    }


async def _openai_dispatch(
    client: httpx.AsyncClient,
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict],
    system_prompt: str,
) -> dict[str, Any]:
    """Call Chat Completions, transparently falling back to the Responses API.

    Some OpenAI-compatible endpoints (e.g. gpt-5.5 on the FABRIC/RENCI proxy)
    only accept the Responses API and reject ``messages`` with a 400. We detect
    that, remember the endpoint, and retry against /responses.
    """
    if base_url in _RESPONSES_ENDPOINTS:
        return await _openai_responses_request(
            client, base_url, api_key, model, messages, system_prompt
        )
    try:
        return await _openai_request(
            client, base_url, api_key, model, messages, system_prompt
        )
    except httpx.HTTPStatusError as e:
        body = e.response.text if e.response is not None else ""
        if (
            e.response is not None
            and e.response.status_code == 400
            and any(h in body for h in _RESPONSES_HINTS)
        ):
            _RESPONSES_ENDPOINTS.add(base_url)
            return await _openai_responses_request(
                client, base_url, api_key, model, messages, system_prompt
            )
        raise


# --- Slash commands ---

_SKILL_MAP = {
    "scaffold": "scaffold",
    "debug": "debug",
    "review": "review",
    "wrapper": "wrapper",
    "dockerfile": "dockerfile",
    "convert": "convert",
    "help": "help",
    "kiso": "kiso",
}


def _process_slash_command(message: str) -> tuple[str, str | None]:
    """Check for slash commands and inject skill prompt."""
    if not message.startswith("/"):
        return message, None

    parts = message.split(None, 1)
    command = parts[0][1:]
    user_args = parts[1] if len(parts) > 1 else ""

    skill_name = _SKILL_MAP.get(command)
    if not skill_name:
        return message, None

    skill_path = os.path.join(KNOWLEDGE_ROOT, "skills", skill_name, "canonical.md")
    if not os.path.isfile(skill_path):
        return message, None

    try:
        with open(skill_path) as f:
            prompt = f.read()
        # Strip YAML frontmatter
        fm_match = re.match(r"^---\s*\n.*?\n---\s*\n(.*)$", prompt, re.DOTALL)
        if fm_match:
            prompt = fm_match.group(1).strip()
    except Exception:
        return message, None

    return f"[Using /{command} skill]\n\n{prompt}\n\nUser request: {user_args}", skill_name


# --- Chat endpoints ---

@router.post("/stream")
async def chat_stream(request: Request) -> StreamingResponse:
    """SSE-streaming chat with tool-calling loop."""
    try:
        body = await request.json()
    except Exception:
        return _error_sse("Invalid JSON body")

    messages = body.get("messages", [])
    model_override = body.get("model", "")
    provider_override = body.get("provider", "")
    agent_id = body.get("agent", "general")
    request_id = body.get("request_id", str(uuid.uuid4()))

    if not messages:
        return _error_sse("No messages provided")

    # Process slash commands in the last user message
    last_msg = messages[-1]
    if last_msg.get("role") == "user" and isinstance(last_msg.get("content"), str):
        processed, skill = _process_slash_command(last_msg["content"])
        if skill:
            messages[-1] = {**last_msg, "content": processed}

    # Load LLM config
    from main import db

    config = await db.get_llm_config()
    if not config or not config.get("provider"):
        return _error_sse("No LLM provider configured. Go to Settings to configure.")

    provider = provider_override or config["provider"]
    api_key = config.get("api_key", "")
    is_anthropic = provider == "anthropic"

    # If a different provider was requested, look up its saved config
    if provider_override and provider_override != config.get("provider"):
        prov_config = await db.get_provider_config(provider_override)
        if prov_config:
            api_key = prov_config.get("api_key", "") or api_key
            is_anthropic = provider_override == "anthropic"

    # Resolve base_url and default model
    from llm.providers import PROVIDER_DEFAULTS, PROVIDERS

    defaults = PROVIDER_DEFAULTS.get(provider, {})
    model = (
        model_override
        or config.get("model")
        or PROVIDERS.get(provider, {}).get("default_model", "")
    )
    if is_anthropic:
        base_url = "https://api.anthropic.com"
    else:
        base_url = config.get("base_url") or defaults.get("base_url", "")

    if not base_url:
        return _error_sse(f"No base URL configured for provider: {provider}")

    system_prompt = _load_system_prompt(agent_id)

    async def event_generator() -> AsyncGenerator[str, None]:
        client = httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=30.0))
        _active_clients[request_id] = client

        try:
            # Convert messages to provider format
            conv_messages = [
                {"role": m["role"], "content": m["content"]}
                for m in messages
                if m.get("role") in ("user", "assistant")
            ]

            for _ in range(_MAX_TOOL_ROUNDS):
                if request_id not in _active_clients:
                    return

                try:
                    if is_anthropic:
                        result = await _anthropic_request(
                            client, base_url, api_key, model,
                            conv_messages, system_prompt,
                        )
                    else:
                        result = await _openai_dispatch(
                            client, base_url, api_key, model,
                            conv_messages, system_prompt,
                        )
                except httpx.HTTPStatusError as e:
                    error_body = e.response.text[:500] if e.response else str(e)
                    yield f"data: {json.dumps({'error': f'LLM API error ({e.response.status_code}): {error_body}'})}\n\n"
                    return
                except Exception as e:
                    yield f"data: {json.dumps({'error': str(e)})}\n\n"
                    return

                tool_calls = result.get("tool_calls", [])

                if not tool_calls:
                    # Final text response
                    text = result.get("content", "")
                    if text:
                        yield f"data: {json.dumps({'content': text})}\n\n"

                    # Save to chat history
                    try:
                        await db.add_chat_message(
                            "assistant", text, agent_id=agent_id,
                        )
                    except Exception:
                        pass
                    return

                # Stream any prefatory text
                if result.get("content"):
                    yield f"data: {json.dumps({'content': result['content']})}\n\n"

                # Execute tool calls
                if is_anthropic:
                    assistant_content: list[dict] = []
                    if result.get("content"):
                        assistant_content.append({"type": "text", "text": result["content"]})

                    for tc in tool_calls:
                        assistant_content.append({
                            "type": "tool_use",
                            "id": tc["id"],
                            "name": tc["name"],
                            "input": tc["arguments"],
                        })
                    conv_messages.append({"role": "assistant", "content": assistant_content})

                    tool_results = []
                    for tc in tool_calls:
                        yield f"data: {json.dumps({'tool_call': {'id': tc['id'], 'name': tc['name'], 'arguments': tc['arguments']}})}\n\n"
                        tool_result = await _execute_tool(tc["name"], tc["arguments"])
                        yield f"data: {json.dumps({'tool_result': {'id': tc['id'], 'name': tc['name'], 'result': tool_result[:2000]}})}\n\n"
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": tc["id"],
                            "content": tool_result,
                        })
                    conv_messages.append({"role": "user", "content": tool_results})

                else:
                    # OpenAI format
                    assistant_msg: dict[str, Any] = {
                        "role": "assistant",
                        "content": result.get("content") or None,
                    }
                    openai_tool_calls = []
                    for tc in tool_calls:
                        openai_tool_calls.append({
                            "id": tc["id"],
                            "type": "function",
                            "function": {
                                "name": tc["name"],
                                "arguments": json.dumps(tc["arguments"]),
                            },
                        })
                    assistant_msg["tool_calls"] = openai_tool_calls
                    conv_messages.append(assistant_msg)

                    for tc in tool_calls:
                        yield f"data: {json.dumps({'tool_call': {'id': tc['id'], 'name': tc['name'], 'arguments': tc['arguments']}})}\n\n"
                        tool_result = await _execute_tool(tc["name"], tc["arguments"])
                        yield f"data: {json.dumps({'tool_result': {'id': tc['id'], 'name': tc['name'], 'result': tool_result[:2000]}})}\n\n"
                        conv_messages.append({
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": tool_result,
                        })

        finally:
            _active_clients.pop(request_id, None)
            await client.aclose()
            yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/stop")
async def stop_chat(request: Request) -> dict:
    """Cancel an active streaming request."""
    try:
        body = await request.json()
    except Exception:
        body = {}

    request_id = body.get("request_id", "")
    if request_id and request_id in _active_clients:
        client = _active_clients.pop(request_id)
        await client.aclose()
        return {"status": "stopped", "request_id": request_id}

    # Stop all
    for rid, client in list(_active_clients.items()):
        try:
            await client.aclose()
        except Exception:
            pass
    _active_clients.clear()
    return {"status": "stopped", "cleared": True}


@router.get("/agents")
async def list_agents() -> dict:
    """List available agent personas."""
    from routers.knowledge import list_agents

    return await list_agents()


@router.get("/history", response_model=ChatHistoryResponse)
async def get_history(limit: int = 100) -> ChatHistoryResponse:
    """Get chat history from the database."""
    from main import db

    rows = await db.get_chat_history(limit=limit)
    messages = [
        ChatMessage(
            role=r["role"],
            content=r["content"],
            agent_id=r.get("agent_id"),
            tool_calls=r.get("tool_calls"),
            created_at=r.get("created_at"),
        )
        for r in rows
    ]
    return ChatHistoryResponse(messages=messages)


def _error_sse(message: str) -> StreamingResponse:
    """Return an SSE stream with a single error event."""

    async def gen():
        yield f"data: {json.dumps({'error': message})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )
