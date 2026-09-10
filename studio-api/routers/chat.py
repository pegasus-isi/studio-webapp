"""Built-in PegasusAI chat — SSE streaming with tool use.

Ported from pegasus-ai-extension/handlers.py (Tornado → FastAPI).
Supports both Anthropic Messages API and OpenAI-compatible endpoints.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
from typing import Any, AsyncGenerator

import httpx
import structlog
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from llm.model_limits import FLOOR_MAX_TOKENS, anthropic_max_tokens
from models import ChatHistoryResponse, ChatMessage
from workspace import is_protected, workflows_dir, workspace_root

log = structlog.get_logger()

router = APIRouter(prefix="/api/chat", tags=["chat"])

from knowledge import layout

# Active streaming clients tracked by request_id
_active_clients: dict[str, httpx.AsyncClient] = {}

# (base URL, model) pairs discovered to require the OpenAI *Responses* API
# (e.g. gpt-5.5 on the FABRIC/RENCI proxy) rather than Chat Completions.
# Populated on first 400.
#
# Keyed by the pair, not the URL alone. These proxies front *many* models on one
# base URL and each speaks its own wire API, so remembering "this endpoint needs
# Responses" because one model did pins every other model behind it to a shape
# its gateway cannot parse. A chat-completions gateway handed a Responses body
# answers `BadRequest - chat-completions request must have at least one
# message`, at turn one, having read nothing — which is what it did to glm-5,
# qwen3-small and gemma4-12b on the NRP proxy.
_RESPONSES_ENDPOINTS: set[tuple[str, str]] = set()
_RESPONSES_ENDPOINTS_MAX = 128


def _memoize_responses_endpoint(base_url: str, model: str) -> None:
    """Remember a (base URL, model) that needs the Responses API, bounded so the
    set can't grow without limit on a long-lived pod (BE-9). In practice there
    are only a handful of distinct endpoints; the cap is a backstop."""
    if len(_RESPONSES_ENDPOINTS) < _RESPONSES_ENDPOINTS_MAX:
        _RESPONSES_ENDPOINTS.add((base_url, model))


_RESPONSES_HINTS = (
    "moved to 'input'",
    "Responses API",
    "Unsupported parameter: 'messages'",
)

# Models that only speak the Responses API (no Chat Completions). For these we
# go straight to /responses instead of probing /chat/completions first — which
# is what Codex does (it hardcodes wire_api = "responses"). Matches e.g.
# gpt-5.5 on the FABRIC/RENCI proxy and OpenAI reasoning models (o1/o3/o4).
_RESPONSES_MODEL_PREFIXES = ("gpt-5", "o1", "o1-", "o3", "o4")

# Pegasus bot routing: when enabled, every LLM call goes through 
# the pegasus bot server's /chat endpoint
PEGASUS_BOT_SERVER_URL = os.environ.get("PEGASUS_BOT_SERVER_URL", "").rstrip("/")
_PEGASUS_BOT_CHAT_ENABLED = os.environ.get("PEGASUS_BOT_CHAT_ROUTE", "") == "1"


def _is_responses_model(model: str) -> bool:
    """Heuristic: True if the model id implies a Responses-only endpoint."""
    m = (model or "").lower().split("/")[-1]  # drop any "provider/" prefix
    return any(m.startswith(p) for p in _RESPONSES_MODEL_PREFIXES)

# Max tool-calling rounds
_MAX_TOOL_ROUNDS = 50

# Transient transport failures (proxy connection resets, read timeouts) talking
# to the LLM endpoint. On a long multi-round agent session an external proxy
# will occasionally drop the stream; retry the round a few times — but only
# before any token has been emitted — so a single blip doesn't kill a long
# generation (e.g. scaffolding a whole workflow).
_MAX_STREAM_RETRIES = 2
_STREAM_RETRY_DELAY_S = 1.5

# How often to emit an SSE comment while a round is in flight but silent.
# nginx caps the /api/ location at proxy_read_timeout (300s here), and an
# ingress in front of a real deployment will have its own limit, so the stream
# has to prove it is alive from the application side rather than relying on
# proxy configuration. Well under any plausible proxy timeout.
_HEARTBEAT_INTERVAL_S = 20.0

_HEARTBEAT = {"type": "__heartbeat__"}


async def _with_heartbeat(
    stream: AsyncGenerator[dict[str, Any], None],
    interval: float = _HEARTBEAT_INTERVAL_S,
) -> AsyncGenerator[dict[str, Any], None]:
    """Yield a stream's events, emitting `_HEARTBEAT` whenever it goes quiet.

    A reasoning model can spend minutes on thinking tokens before the first
    text delta of a round. The per-round ping is emitted *before* the round, so
    it does not help inside one: the proxy sees no bytes for the whole think and
    drops the connection, which reaches the client as a bare network error.

    Awaiting the next event as a task lets the wait be bounded without
    disturbing the stream — nothing is consumed until it actually arrives.
    """
    it = stream.__aiter__()
    pending: asyncio.Task | None = None
    try:
        while True:
            pending = asyncio.ensure_future(it.__anext__())
            while True:
                done, _ = await asyncio.wait({pending}, timeout=interval)
                if done:
                    break
                yield _HEARTBEAT
            try:
                event = pending.result()
            except StopAsyncIteration:
                return
            finally:
                pending = None
            yield event
    finally:
        # Caller broke out (client gone, tool round finished early): the
        # in-flight __anext__ must not outlive this generator.
        if pending is not None and not pending.done():
            pending.cancel()


# How long the upstream LLM connection may stay silent before we give up on it.
#
# This was a flat 120s read timeout, which is a *token gap*, not a total budget —
# so any model that thinks for two minutes in one burst was torn down mid-turn.
# It surfaced as `ReadTimeout` and was reported to the user as a drop "from the
# provider", which is the worst possible framing: the natural response is to
# retry, and a retry often succeeds because the model happens to think in shorter
# bursts the second time. A ceiling that presents as flakiness.
#
# Observed: a model produced 27 tool calls over 428s, then 16 over 335s on the
# identical prompt — different points, same wall-clock wall — while the *same*
# model ran 1628s on another task without trouble, because there it streamed
# continuously. Reasoning models that buffer before emitting are the ones hit.
#
# 900s is deliberately generous: a silent connection costs one held socket, while
# a premature teardown costs the whole turn and misattributes the failure.
_LLM_READ_TIMEOUT_S = float(os.environ.get("STUDIO_LLM_READ_TIMEOUT", "900"))
_LLM_CONNECT_TIMEOUT_S = float(os.environ.get("STUDIO_LLM_CONNECT_TIMEOUT", "30"))


def _llm_timeout() -> httpx.Timeout:
    """Timeouts for the upstream call to the model provider."""
    return httpx.Timeout(
        _LLM_READ_TIMEOUT_S,
        connect=_LLM_CONNECT_TIMEOUT_S,
        write=60.0,
        pool=30.0,
    )


def _transport_error_message(
    exc: Exception, base_url: str, mid_stream: bool
) -> str:
    """User-facing message for a network-level drop to the LLM endpoint."""
    host = base_url
    try:
        from urllib.parse import urlparse

        host = urlparse(base_url).hostname or base_url
    except Exception:
        pass
    detail = type(exc).__name__
    if isinstance(exc, httpx.ReadTimeout):
        # Ours, not theirs. Saying "transient provider drop" here sends people to
        # retry a problem a retry cannot fix.
        return (
            f"The model at {host} went quiet for more than "
            f"{_LLM_READ_TIMEOUT_S:.0f}s, so the connection was closed. Models "
            "that reason for a long time before answering can hit this; raise "
            "STUDIO_LLM_READ_TIMEOUT if it keeps happening. Some steps may "
            "already have run."
        )
    if mid_stream:
        return (
            f"Lost the connection to the LLM endpoint ({host}) partway through "
            f"the response ({detail}). This is usually a transient drop from the "
            "provider; some steps may have already run — please retry."
        )
    return (
        f"Could not reach the LLM endpoint ({host}) after {_MAX_STREAM_RETRIES + 1} "
        f"attempts ({detail}). This is usually a transient provider/network "
        "drop — please retry in a moment."
    )

# Context-window management. The frontend re-sends the whole conversation each
# turn, and tool results (especially read_file, up to 100KB) can be large. Bound
# both so a long session doesn't blow past the model's context and 400.
_MAX_HISTORY_MESSAGES = 40         # most-recent turns fed back to the model
_MAX_TOOL_RESULT_CHARS = 16000     # per tool result fed back to the model
_MAX_UI_RESULT_CHARS = 2000        # per tool result shown in the UI

# stop_reason values that mean the model was cut off at the output-token cap.
_TRUNCATED_STOP_REASONS = {"length", "max_tokens", "incomplete"}

# Only the Anthropic Messages API makes max_tokens mandatory; the OpenAI
# transports omit it and let the model decide.
#
# The ceiling for that one transport is resolved from the selected model rather
# than fixed here (llm/model_limits.py): a constant 8192 was the value every
# Messages-API model accepts, which truncated every current model at a
# sixteenth of its real 128K capacity — and on a reasoning model, whose
# thinking is billed against the same budget, the cap could be spent before any
# visible content appeared. `extra_config.max_tokens` still overrides it.


def _coerce_max_tokens(value: Any, provider: str) -> int | None:
    """Validate an operator-supplied ``extra_config.max_tokens``.

    The field is free-form JSON, so a bad value must not take the turn down
    with it: anything that is not a positive integer is logged and dropped,
    leaving the ceiling to the model.
    """
    if value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = 0
    if parsed <= 0:
        log.warning("llm_max_tokens_invalid", provider=provider, value=value)
        return None
    return parsed


def _clip_ui_result(text: str) -> str:
    """Cap a tool result shown in the UI, with a visible marker when clipped."""
    if len(text) <= _MAX_UI_RESULT_CHARS:
        return text
    return (
        text[:_MAX_UI_RESULT_CHARS]
        + f"\n[... truncated for display, {len(text)} chars total ...]"
    )


def _trim_history(msgs: list[dict]) -> list[dict]:
    """Keep only the most recent messages so context stays bounded, trimmed to
    a valid conversation boundary.

    A blind ``msgs[-40:]`` can start on an assistant message or mid
    tool-exchange; the Anthropic Messages API then 400s ("first message must
    use the 'user' role"), so once a conversation passed 40 messages every
    turn failed (BE-4). Advance the start to the first genuine user turn — a
    ``user`` message whose content is a plain string, i.e. not a tool-result
    carrier (Anthropic tool results ride on a ``user`` message with a list
    content and must keep their preceding assistant ``tool_use``). The current
    ask is always such a message, so the tail is never empty."""
    if len(msgs) <= _MAX_HISTORY_MESSAGES:
        return msgs
    tail = msgs[-_MAX_HISTORY_MESSAGES:]
    for i, m in enumerate(tail):
        if m.get("role") == "user" and isinstance(m.get("content"), str):
            return tail[i:]
    # No clean user turn in the tail (pathological) — fall back to the newest
    # message so we never send an assistant-first or empty list.
    return tail[-1:]


def _clip_tool_result(text: str) -> str:
    """Cap a tool result fed back to the model, with a visible marker."""
    if len(text) <= _MAX_TOOL_RESULT_CHARS:
        return text
    kept = text[:_MAX_TOOL_RESULT_CHARS]
    dropped = len(text) - _MAX_TOOL_RESULT_CHARS
    return f"{kept}\n\n[... {dropped} characters truncated ...]"

# Tool definitions for the built-in chat (file ops + Pegasus commands)
TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write content to a file in the workspace. Creates parent directories.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path relative to the workspace root"},
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
            "description": "Read a file from the workspace (max 100KB).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path relative to the workspace root"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": "List files and directories in the workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Directory path relative to the workspace root (default: the root itself)"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Execute a shell command in the workspace (5 min timeout).",
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
            "description": "Create a directory in the workspace.",
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

# Pegasus-aware read-only tools (run status, jobs, diagnosis, outputs) with
# chat-scope argument defaulting — defined in services/chat_tools.py so they
# stay unit-testable away from the streaming loop.
from services import chat_tools as _chat_tools

TOOL_DEFINITIONS += _chat_tools.TOOL_DEFINITIONS

# Anthropic-format tool definitions
TOOL_DEFINITIONS_ANTHROPIC = [
    {
        "name": t["function"]["name"],
        "description": t["function"]["description"],
        "input_schema": t["function"]["parameters"],
    }
    for t in TOOL_DEFINITIONS
]

WORKSPACE_ROOT = workspace_root()

_MAX_READ_BYTES = 100 * 1024
_MAX_OUTPUT_BYTES = 8 * 1024
_CMD_TIMEOUT = 300


# --- Tool execution ---

def _safe_path(path: str) -> str:
    """Resolve and validate path inside workspace."""
    if not path:
        return WORKSPACE_ROOT
    p = path.strip()
    if not os.path.isabs(p):
        # Tolerate both "workflows/..." and "work/workflows/..." (and repeats)
        # — models prefix work/ because prompt says $HOME/work/workflows/...
        base = os.path.basename(WORKSPACE_ROOT.rstrip(os.sep))  # "work" by default, dynamic if custom
        # strip one or more leading "base/" segments
        while True:
            if p == base:
                return WORKSPACE_ROOT
            if p.startswith(base + os.sep):
                p = p[len(base) + 1 :].lstrip(os.sep)
                continue
            break
        path = os.path.join(WORKSPACE_ROOT, p)
    resolved = os.path.realpath(os.path.expanduser(path))
    root = os.path.realpath(WORKSPACE_ROOT)
    if not resolved.startswith(root + os.sep) and resolved != root:
        raise ValueError(f"Access denied: path must be inside {WORKSPACE_ROOT}")
    if is_protected(resolved, root):
        # Reading is denied as well as writing: an assistant that can read the
        # Condor token or the settings DB can put either in a message.
        raise ValueError("Access denied: that directory holds credentials")
    return resolved


async def _execute_tool(
    name: str,
    arguments: dict[str, Any],
    scope: dict[str, Any] | None = None,
) -> str:
    """Execute a tool and return result string. `scope` is the chat's active
    workflow/run context; the Pegasus tools default their ids from it."""
    import asyncio
    import shutil

    # Pegasus tools first — read-only service wrappers, never shell-gated.
    pegasus_result = await _chat_tools.execute(name, arguments, scope)
    if pegasus_result is not None:
        return pegasus_result

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


# Tools that run arbitrary shell and so require per-turn approval (SEC-4).
# File tools (read/write/list/create) are confined to the workspace by
# _safe_path, so they stay auto-approved to keep the prompt from firing on
# every step.
_APPROVAL_REQUIRED_TOOLS = {"run_command"}


def _permission_summary(name: str, arguments: dict[str, Any]) -> str:
    """Human-readable one-liner for the approval card."""
    if name == "run_command":
        return str(arguments.get("command", "")).strip()
    return json.dumps(arguments)[:300]


async def _run_tool_gated(
    name: str,
    arguments: dict[str, Any],
    *,
    request_id: str,
    require_approval: bool,
    scope: dict[str, Any] | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """Yield SSE payload dicts for one tool call, pausing for approval first
    when the tool runs arbitrary shell.

    Yields a ``permission_request`` (when gated), always a ``tool_call``, then
    a ``tool_result`` — the last item is tagged ``{"_final_result": <text>}``
    so the caller can thread the result into the model conversation.
    """
    from services import chat_permissions

    gated = require_approval and name in _APPROVAL_REQUIRED_TOOLS
    if gated:
        yield {"permission_request": {
            "id": arguments.get("_call_id", ""),
            "name": name,
            "summary": _permission_summary(name, arguments),
        }}
        decision = await chat_permissions.await_decision(
            request_id, arguments["_call_id"]
        )
        if decision != "approve":
            reason = (
                "timed out waiting for approval"
                if decision == "timeout"
                else "denied by the user"
            )
            result = f"Command not run — {reason}."
            yield {"tool_call": {
                "id": arguments["_call_id"], "name": name,
                "arguments": _public_args(arguments),
            }}
            yield {"tool_result": {
                "id": arguments["_call_id"], "name": name,
                "result": result,
            }}
            yield {"_final_result": result}
            return

    yield {"tool_call": {
        "id": arguments["_call_id"], "name": name,
        "arguments": _public_args(arguments),
    }}
    result = await _execute_tool(name, _public_args(arguments), scope)
    yield {"tool_result": {
        "id": arguments["_call_id"], "name": name,
        "result": _clip_ui_result(result),
    }}
    yield {"_final_result": result}


def _public_args(arguments: dict[str, Any]) -> dict[str, Any]:
    """Arguments minus the internal ``_call_id`` marker."""
    return {k: v for k, v in arguments.items() if k != "_call_id"}


# --- System prompt ---

def _load_system_prompt(agent_id: str | None = None) -> str:
    """Build system prompt from PEGASUS_AI.md + tool instructions + agent persona."""
    parts = []

    # Shared context: the Studio overlay plus the portable Pegasus guide. This
    # read used to be a hardcoded references/PEGASUS_AI.md under a hardcoded
    # KNOWLEDGE_ROOT; both moved, and because every lookup here failed silently
    # the chat lost its context, its agent personas and its skills at once.
    shared = layout.context_document()
    if shared:
        parts.append(shared)

    parts.append(
        "\n\n## Tool Use Instructions\n\n"
        "You have access to tools for file operations and shell commands. "
        "Use them to help the user create, run, and debug Pegasus workflows.\n\n"
        "For workflow questions, prefer the structured Pegasus tools over "
        "shelling out: list_workflow_projects, list_workflow_runs, "
        "get_run_status, get_run_jobs, diagnose_run (the right first call for "
        "'why did it fail?'), and list_run_outputs. When the chat has an "
        "Active Context, their run_id/project_id default to it — call them "
        "with no arguments.\n\n"
        "You have no interactive question tool. To ask something, write the "
        "question as ordinary prose and wait for the reply — never emit a "
        "tool-call object as your message.\n\n"
        "WORKSPACE RULES:\n"
        f"- Each workflow goes in its own directory under "
        f"{workflows_dir(WORKSPACE_ROOT)}\n"
        "- Use kebab-case directory names (e.g., csv-summary-workflow/)\n"
    )

    # Agent persona
    # agent_id is client-supplied; constrain it to a bare slug so it can't
    # traverse out of the agents directory (e.g. "../../../etc/passwd") and
    # leak arbitrary files into the system prompt.
    if agent_id and agent_id != "general" and re.fullmatch(r"[A-Za-z0-9_-]+", agent_id):
        agent_path = os.path.join(layout.agents_dir(), f"{agent_id}.md")
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

async def _raise_for_stream_status(resp: httpx.Response) -> None:
    """Read the body and raise on an error status for a *streaming* response.

    httpx defers body reads on streamed responses, so `raise_for_status()`
    alone would give an HTTPStatusError whose `.response.text` isn't populated —
    the transport-fallback logic needs that text, so read it first.
    """
    if resp.status_code >= 400:
        await resp.aread()
        resp.raise_for_status()


async def _pegasus_chat_stream(
    client: httpx.AsyncClient,
    bot_base_url: str,
    messages: list[dict],
    tools: list[dict],
    user: str,
    request_id: str,
    conversation_id: str | None,
    system_prompt: str,
    display_text: str | None = None,
) -> AsyncGenerator[dict, None]:
    """Run ONE LLM round through the Pegasus bot server, heartbeat-aware.

    Prepends the system prompt, posts the conversation + tool definitions to
    {bot}/chat, and reshapes the JSON reply into the same ``result`` dict the
    builtin generators produce, so the shared tool-round machinery downstream
    (usage aggregation, local tool execution, persistence) works unchanged.
    Yields ``__heartbeat__`` while awaiting the bot to keep the SSE warm.
    """
    body = {
        "messages": [{"role": "system", "content": system_prompt}] + messages,
        "tools": tools,
        "user": user,
        "request_id": request_id,
        "conversation_id": conversation_id,
        "display_text": display_text,
    }

    async def _do_post():
        resp = await client.post(f"{bot_base_url}/chat", json=body, timeout=120.0)
        resp.raise_for_status()
        return resp.json()

    task = asyncio.create_task(_do_post())
    while not task.done():
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=15)
        except asyncio.TimeoutError:
            yield {"type": "__heartbeat__"}
    data = task.result()

    if data.get("status_code") != 200 or data.get("status") != "successful":
        raise RuntimeError(data.get("detail") or data.get("response") or "bot server error")

    tool_calls = []
    for tc in data.get("tool_calls") or []:
        args = tc.get("arguments")
        if isinstance(args, str):
            try:
                args = json.loads(args) if args else {}
            except json.JSONDecodeError:
                args = {}
        tool_calls.append({
            "id": tc.get("id") or str(uuid.uuid4()),
            "name": tc.get("name", ""),
            "arguments": args if isinstance(args, dict) else {},
        })

    usage = data.get("usage") or {}
    yield {
        "type": "result",
        "result": {
            "content": data.get("response", "") or "",
            "tool_calls": tool_calls,
            "stop_reason": "tool_calls" if tool_calls else (data.get("finish_reason") or "stop"),
            "usage": {
                "input": usage.get("input_tokens", 0) or 0,
                "output": usage.get("output_tokens", 0) or 0,
            },
        },
    }


async def _anthropic_stream(
    client: httpx.AsyncClient,
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict],
    system_prompt: str,
    max_tokens: int | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """Stream the Anthropic Messages API.

    Yields ``{"type": "text", "text": delta}`` as tokens arrive and a final
    ``{"type": "result", "result": {...}}`` with the aggregated content, tool
    calls and usage (same shape the tool-calling loop expects).

    Unlike the OpenAI transports, the Messages API *requires* ``max_tokens``,
    so this is the one path that cannot defer the ceiling to the model. The
    caller resolves it from the selected model (llm/model_limits.py); the floor
    here is only for a direct call that passes nothing.

    Streaming, which this is, is also what makes a large ceiling safe: the SDKs
    require it above ~16K because a non-streamed response that big outruns the
    HTTP timeout.
    """
    url = f"{base_url}/v1/messages"
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    body = {
        "model": model,
        "max_tokens": max_tokens or FLOOR_MAX_TOKENS,
        "system": system_prompt,
        "messages": messages,
        "tools": TOOL_DEFINITIONS_ANTHROPIC,
        "stream": True,
    }

    content = ""
    tool_blocks: dict[int, dict[str, str]] = {}
    order: list[int] = []
    usage_in = 0
    usage_out = 0
    stop_reason = ""

    async with client.stream("POST", url, json=body, headers=headers) as resp:
        await _raise_for_stream_status(resp)
        async for line in resp.aiter_lines():
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if not data:
                continue
            try:
                evt = json.loads(data)
            except json.JSONDecodeError:
                continue
            etype = evt.get("type")
            if etype == "message_start":
                u = (evt.get("message", {}) or {}).get("usage", {}) or {}
                usage_in = u.get("input_tokens", 0) or 0
            elif etype == "content_block_start":
                idx = evt.get("index")
                block = evt.get("content_block", {}) or {}
                if block.get("type") == "tool_use":
                    tool_blocks[idx] = {
                        "id": block.get("id", ""),
                        "name": block.get("name", ""),
                        "json": "",
                    }
                    order.append(idx)
            elif etype == "content_block_delta":
                idx = evt.get("index")
                delta = evt.get("delta", {}) or {}
                dtype = delta.get("type")
                if dtype == "text_delta":
                    t = delta.get("text", "")
                    if t:
                        content += t
                        yield {"type": "text", "text": t}
                elif dtype == "input_json_delta" and idx in tool_blocks:
                    tool_blocks[idx]["json"] += delta.get("partial_json", "")
            elif etype == "message_delta":
                d = evt.get("delta", {}) or {}
                if d.get("stop_reason"):
                    stop_reason = d["stop_reason"]
                u = evt.get("usage", {}) or {}
                usage_out = u.get("output_tokens", usage_out) or usage_out

    tool_calls = []
    for idx in order:
        tb = tool_blocks[idx]
        try:
            args = json.loads(tb["json"]) if tb["json"] else {}
        except json.JSONDecodeError:
            args = {}
        tool_calls.append({"id": tb["id"], "name": tb["name"], "arguments": args})

    yield {
        "type": "result",
        "result": {
            "content": content,
            "tool_calls": tool_calls,
            "stop_reason": stop_reason,
            "usage": {"input": usage_in, "output": usage_out},
        },
    }


async def _openai_stream(
    client: httpx.AsyncClient,
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict],
    system_prompt: str,
    max_tokens: int | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """Stream an OpenAI-compatible /chat/completions response.

    Yields text deltas then a final aggregated result (see _anthropic_stream).

    ``max_tokens`` is omitted unless configured, so the ceiling is whatever the
    model and endpoint allow. A fixed cap here truncates reasoning models —
    their thinking tokens are billed against the same budget, so a small
    ceiling can be spent before any content or tool call is emitted.
    """
    url = f"{base_url}/chat/completions"
    headers: dict[str, str] = {"content-type": "application/json"}
    if api_key:
        headers["authorization"] = f"Bearer {api_key}"

    full_messages = [{"role": "system", "content": system_prompt}] + messages
    body: dict[str, Any] = {
        "model": model,
        "messages": full_messages,
        "tools": TOOL_DEFINITIONS,
        "stream": True,
        # Ask for usage in the terminal chunk (OpenAI + most compatible proxies).
        "stream_options": {"include_usage": True},
    }
    if max_tokens:
        body["max_tokens"] = max_tokens

    content = ""
    tool_frags: dict[int, dict[str, str]] = {}
    order: list[int] = []
    usage_in = 0
    usage_out = 0
    stop_reason = ""

    async with client.stream("POST", url, json=body, headers=headers) as resp:
        await _raise_for_stream_status(resp)
        async for line in resp.aiter_lines():
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if not data:
                continue
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue
            u = chunk.get("usage")
            if u:
                usage_in = u.get("prompt_tokens", usage_in) or usage_in
                usage_out = u.get("completion_tokens", usage_out) or usage_out
            choices = chunk.get("choices") or []
            if not choices:
                continue
            ch = choices[0]
            if ch.get("finish_reason"):
                stop_reason = ch["finish_reason"]
            delta = ch.get("delta", {}) or {}
            t = delta.get("content")
            if t:
                content += t
                yield {"type": "text", "text": t}
            for tc in delta.get("tool_calls") or []:
                idx = tc.get("index", 0)
                if idx not in tool_frags:
                    tool_frags[idx] = {"id": "", "name": "", "args": ""}
                    order.append(idx)
                if tc.get("id"):
                    tool_frags[idx]["id"] = tc["id"]
                fn = tc.get("function", {}) or {}
                if fn.get("name"):
                    tool_frags[idx]["name"] = fn["name"]
                if fn.get("arguments"):
                    tool_frags[idx]["args"] += fn["arguments"]

    tool_calls = []
    for idx in order:
        tf = tool_frags[idx]
        try:
            args = json.loads(tf["args"]) if tf["args"] else {}
        except json.JSONDecodeError:
            args = {}
        tool_calls.append({
            "id": tf["id"] or str(uuid.uuid4()),
            "name": tf["name"],
            "arguments": args,
        })

    yield {
        "type": "result",
        "result": {
            "content": content,
            "tool_calls": tool_calls,
            "stop_reason": stop_reason,
            "usage": {"input": usage_in, "output": usage_out},
        },
    }


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


def _parse_responses_output(data: dict[str, Any]) -> dict[str, Any]:
    """Extract content/tool_calls/usage from a Responses-API response object."""
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
    u = data.get("usage", {}) or {}
    return {
        "content": "".join(content_parts),
        "tool_calls": tool_calls,
        "stop_reason": data.get("status", ""),
        "usage": {
            "input": u.get("input_tokens", 0),
            "output": u.get("output_tokens", 0),
        },
    }


async def _openai_responses_stream(
    client: httpx.AsyncClient,
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict],
    system_prompt: str,
    max_tokens: int | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """Stream the OpenAI Responses API (/responses).

    Text deltas are forwarded live; tool calls and usage come from the terminal
    ``response.completed`` event (whose payload matches the non-streaming shape).

    ``max_output_tokens`` is omitted unless configured (see _openai_stream).
    """
    url = f"{base_url}/responses"
    headers: dict[str, str] = {"content-type": "application/json"}
    if api_key:
        headers["authorization"] = f"Bearer {api_key}"

    body: dict[str, Any] = {
        "model": model,
        "instructions": system_prompt,
        "input": _messages_to_responses_input(messages),
        "tools": _tools_for_responses(),
        "stream": True,
    }
    if max_tokens:
        body["max_output_tokens"] = max_tokens

    content = ""
    final: dict[str, Any] | None = None

    async with client.stream("POST", url, json=body, headers=headers) as resp:
        await _raise_for_stream_status(resp)
        async for line in resp.aiter_lines():
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if not data or data == "[DONE]":
                continue
            try:
                evt = json.loads(data)
            except json.JSONDecodeError:
                continue
            etype = evt.get("type")
            if etype == "response.output_text.delta":
                t = evt.get("delta", "")
                if t:
                    content += t
                    yield {"type": "text", "text": t}
            elif etype in (
                "response.completed",
                "response.incomplete",
                "response.failed",
            ):
                final = evt.get("response") or final

    result = _parse_responses_output(final or {})
    # If text arrived only in the final object (no deltas), surface it now.
    if not content and result["content"]:
        yield {"type": "text", "text": result["content"]}
    elif content:
        result["content"] = content

    yield {"type": "result", "result": result}


async def _openai_dispatch(
    client: httpx.AsyncClient,
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict],
    system_prompt: str,
    wire_api: str = "auto",
    max_tokens: int | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """Stream via Chat Completions or the Responses API, picking the transport.

    Yields the same event dicts as the underlying stream generators. ``wire_api``
    controls the choice:
      - "responses": always use /responses (no Chat-Completions probe).
      - "chat":      always use /chat/completions (no fallback).
      - "auto" (default): use /responses if the model is Responses-only
        (e.g. gpt-5.5 on the FABRIC/RENCI proxy) or the endpoint was already
        learned to require it; otherwise try /chat/completions and fall back
        to /responses on a clear signal — a 400 with a known hint, a 404/405,
        a 5xx, or a transport-level failure (some proxies reset the connection
        for unsupported routes, which surfaces as a generic "network error").

    The transport is chosen by the *handshake*: each stream generator opens the
    connection and validates the status before its first yield, so a fallback
    only ever happens before any token is emitted — never mid-stream.
    """
    def responses() -> AsyncGenerator[dict[str, Any], None]:
        return _openai_responses_stream(
            client, base_url, api_key, model, messages, system_prompt, max_tokens
        )

    def chat() -> AsyncGenerator[dict[str, Any], None]:
        return _openai_stream(
            client, base_url, api_key, model, messages, system_prompt, max_tokens
        )

    if wire_api == "responses" or (base_url, model) in _RESPONSES_ENDPOINTS or (
        wire_api == "auto" and _is_responses_model(model)
    ):
        async for ev in responses():
            yield ev
        return
    if wire_api == "chat":
        async for ev in chat():
            yield ev
        return

    # auto: probe /chat/completions; fall back to /responses if the handshake
    # fails before any token is streamed.
    gen = chat()
    try:
        first = await gen.__anext__()
    except StopAsyncIteration:
        return
    except httpx.HTTPStatusError as e:
        resp = e.response
        status = resp.status_code if resp is not None else None
        body = resp.text if resp is not None else ""
        # A 400 that explicitly says "use the Responses API" is definitive —
        # memoize so we skip the chat probe next time.
        if status == 400 and any(h in body for h in _RESPONSES_HINTS):
            _memoize_responses_endpoint(base_url, model)
            async for ev in responses():
                yield ev
            return
        # 404/405 (route missing) or 5xx (maybe transient): retry /responses and
        # memoize only if it actually starts streaming, so one flaky 500 from a
        # healthy Chat-Completions endpoint can't pin all traffic to /responses.
        if status in (404, 405) or (status is not None and status >= 500):
            rgen = responses()
            rfirst = await rgen.__anext__()
            _memoize_responses_endpoint(base_url, model)
            yield rfirst
            async for ev in rgen:
                yield ev
            return
        raise
    except httpx.RequestError:
        # Transport-level failure on /chat/completions — retry /responses once
        # for this call. Don't memoize: a true outage would fail both, and we
        # don't want a transient blip to pin the endpoint to Responses forever.
        async for ev in responses():
            yield ev
        return

    yield first
    async for ev in gen:
        yield ev


# --- Slash commands ---

def _skill_for_command(command: str) -> str | None:
    """Resolve a slash command to a skill in the knowledge tree.

    Built from what is installed rather than a hardcoded table. The table listed
    the pre-plugin names (scaffold, dockerfile, ...), so after the rename to
    pegasus-scaffold, pegasus-apptainer and the rest it matched nothing: every
    slash command expanded to no skill, silently.

    Both spellings resolve, so /scaffold keeps working beside /pegasus-scaffold.
    """
    names = layout.skill_names()
    if command in names:
        return command
    if f"pegasus-{command}" in names:
        return f"pegasus-{command}"
    # The container skill was renamed with the switch away from Docker; users
    # still type the old command.
    if command == "dockerfile" and "pegasus-apptainer" in names:
        return "pegasus-apptainer"
    return None


def _process_slash_command(message: str) -> tuple[str, str | None]:
    """Check for slash commands and inject skill prompt."""
    if not message.startswith("/"):
        return message, None

    parts = message.split(None, 1)
    command = parts[0][1:]
    user_args = parts[1] if len(parts) > 1 else ""

    skill_name = _skill_for_command(command)
    if not skill_name:
        return message, None

    skill_path = layout.skill_document(skill_name)
    if not skill_path:
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

def _sse_response(source) -> StreamingResponse:
    return StreamingResponse(
        source,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _brokered_stream(
    generator, conversation_id: str | None
) -> StreamingResponse:
    """Stream a turn through the broker rather than straight to the client.

    The response looks identical to the caller. The difference is ownership:
    the generator is drained by a background task, so the turn survives the
    request ending — a navigation, a closed tab, a dropped connection — and
    can be reattached to via /turn/events.
    """
    from services.chat_turn import manager as turn_manager

    turn = turn_manager.start(generator, conversation_id)
    return _sse_response(turn.subscribe())


@router.get("/turn")
async def current_turn() -> dict:
    """Whether a turn is in flight, so a freshly-loaded panel can reattach."""
    from services.chat_turn import manager as turn_manager

    turn = turn_manager.active()
    return turn.snapshot() if turn else {"active": False}


@router.get("/turn/events")
async def turn_events() -> StreamingResponse:
    """Replay the in-flight turn from its start, then follow it live.

    Replay rather than tail: a panel that reloaded mid-answer has a truncated
    message in local storage, and rebuilding from the beginning is the only way
    to end up with the same text a client that never left would have.
    """
    from services.chat_turn import manager as turn_manager

    turn = turn_manager.current()
    if turn is None:
        raise HTTPException(status_code=404, detail="No chat turn in progress")
    return _sse_response(turn.subscribe())


@router.post("/stream")
async def chat_stream(request: Request) -> StreamingResponse:
    """SSE-streaming chat with tool-calling loop."""
    try:
        body = await request.json()
    except Exception:
        return _error_sse("Invalid JSON body")

    messages = body.get("messages", [])
    display_text = body.get("display_text")  # lean last-user message for DB source (hybrid @ cache)
    model_override = body.get("model", "")
    provider_override = body.get("provider", "")
    max_tokens_override = body.get("max_tokens")
    agent_id = body.get("agent", "general")
    request_id = body.get("request_id", str(uuid.uuid4()))
    conversation_id = body.get("conversation_id")
    scope = body.get("context") if isinstance(body.get("context"), dict) else {}

    if not messages:
        return _error_sse("No messages provided")

    # Load LLM config
    from main import db

    config = await db.get_llm_config()
    if not config or not config.get("provider"):
        return _error_sse("No LLM provider configured. Go to Settings to configure.")

    provider = provider_override or config["provider"]
    api_key = config.get("api_key", "")
    is_anthropic = provider == "anthropic"

    # If a different provider was requested, use ITS saved config — endpoint,
    # model, and key — not just the key (lets multiple custom endpoints work).
    if provider_override and provider_override != config.get("provider"):
        prov_config = await db.get_provider_config(provider_override)
        if prov_config:
            # Use the override provider's OWN key. Do NOT fall back to the
            # default provider's key — that would send e.g. an Anthropic key as
            # a bearer token to a different provider's endpoint. An empty key is
            # correct for keyless providers (gateway, ollama).
            api_key = prov_config.get("api_key", "") or ""
            is_anthropic = provider_override == "anthropic"
            config = {
                **config,
                "base_url": prov_config.get("base_url") or config.get("base_url"),
                "model": prov_config.get("default_model") or config.get("model"),
            }

    # Resolve base_url and default model
    from llm.providers import PROVIDER_DEFAULTS, PROVIDERS

    defaults = PROVIDER_DEFAULTS.get(provider, {})
    model = (
        model_override
        or config.get("model")
        or PROVIDERS.get(provider, {}).get("default_model", "")
    )
    if is_anthropic:
        # Honor a configured Anthropic-compatible base URL (proxy/gateway);
        # fall back to the public API. _anthropic_request appends
        # "/v1/messages", so strip a trailing "/v1" to avoid ".../v1/v1/...".
        base_url = (config.get("base_url") or "").rstrip("/") or "https://api.anthropic.com"
        if base_url.endswith("/v1"):
            base_url = base_url[:-3]
    else:
        base_url = config.get("base_url") or defaults.get("base_url", "")

    if not base_url:
        return _error_sse(f"No base URL configured for provider: {provider}")

    # Optional explicit transport override: extra_config.wire_api =
    # "auto" | "chat" | "responses". Lets an operator force the Responses API
    # for a provider whose model id isn't auto-detected.
    wire_api = (config.get("extra_config") or {}).get("wire_api", "auto")

    use_bot_route = _PEGASUS_BOT_CHAT_ENABLED and bool(PEGASUS_BOT_SERVER_URL)
    user = os.environ.get("JUPYTERHUB_USER", "unknown")

    # Optional output ceiling: extra_config.max_tokens. Unset means the model
    # and endpoint decide, which is what reasoning models need — a fixed cap is
    # spent on thinking tokens before any content or tool call is emitted.
    # The Anthropic transport requires a value and falls back to its default.
    # Per-request override wins, exactly as provider/model do. The stored
    # value is global — one row, shared by every provider and model a request
    # can select — so an evaluation harness or a caller that knows the model
    # cannot express "this model needs more room" through config alone.
    max_tokens = _coerce_max_tokens(
        max_tokens_override
        if max_tokens_override is not None
        else (config.get("extra_config") or {}).get("max_tokens"),
        provider,
    )
    # Nothing configured, and this is the transport that must send a number:
    # ask the model what it allows instead of assuming the lowest common
    # denominator. Cached per model, and it falls back to that same floor if
    # the provider cannot answer, so a turn is never held up by this.
    if max_tokens is None and is_anthropic:
        max_tokens = await anthropic_max_tokens(base_url, api_key, model)

    system_prompt = _load_system_prompt(agent_id)

    # Workflow/run scope from the chat panel's context picker. Resolved fresh
    # per turn (status and diagnosis move while a conversation is open) and
    # soft-failing: an unknown or unreadable scope logs and the turn proceeds
    # unscoped rather than erroring. The validated form also feeds the Pegasus
    # tools, whose run_id/project_id default to it.
    tool_scope: dict[str, Any] | None = None
    if scope:
        from services.chat_scope import SCOPE_TYPES, build_scope_block

        scope_block = await build_scope_block(scope.get("type"), scope.get("id"))
        if scope_block:
            system_prompt = f"{system_prompt}\n\n{scope_block}"
        if scope.get("type") in SCOPE_TYPES and scope.get("id"):
            tool_scope = {"type": scope["type"], "id": scope["id"]}

    async def event_generator() -> AsyncGenerator[str, None]:
        import time

        client = httpx.AsyncClient(timeout=_llm_timeout())
        _active_clients[request_id] = client

        # Read the approval posture once per turn (SEC-4): whether run_command
        # pauses for user confirmation.
        from services import chat_permissions

        require_approval = await chat_permissions.require_approval(db)

        # Aggregate usage/timing across all tool-calling rounds for the footer.
        t0 = time.monotonic()
        tot_in = 0
        tot_out = 0
        tool_count = 0

        def _meta_event() -> str:
            return "data: " + json.dumps({"meta": {
                "input_tokens": tot_in,
                "output_tokens": tot_out,
                "tokens": tot_in + tot_out,
                "tool_calls": tool_count,
                "duration_s": round(time.monotonic() - t0, 1),
            }}) + "\n\n"

        client_gone = False
        try:
            # Convert messages to provider format. Expand slash commands in
            # every user message (not just the latest) so a `/scaffold …` from
            # an earlier turn keeps its injected skill prompt on later turns —
            # the frontend re-sends the raw command in history. Expansion is
            # idempotent: expanded text no longer starts with "/".
            conv_messages = []
            for m in messages:
                if m.get("role") not in ("user", "assistant"):
                    continue
                content = m["content"]
                if m.get("role") == "user" and isinstance(content, str):
                    content, _ = _process_slash_command(content)
                conv_messages.append({"role": m["role"], "content": content})

            # Bound the history fed to the model so long sessions don't 400.
            conv_messages = _trim_history(conv_messages)

            # Persist the new user turn (the last message) so history is complete
            # — previously only assistant text was saved. Store the raw text the
            # user typed, not the slash-expanded copy.
            last = messages[-1]
            if last.get("role") == "user" and isinstance(last.get("content"), str):
                try:
                    await db.add_chat_message(
                        "user", last["content"], agent_id=agent_id,
                        conversation_id=conversation_id,
                    )
                except Exception:
                    pass

            # Names of tools invoked this turn, saved alongside the assistant msg.
            tools_used: list[str] = []
            pending_deny_close = False

            for _ in range(_MAX_TOOL_ROUNDS):
                if request_id not in _active_clients:
                    return

                # Heartbeat before each round: keeps a proxy from dropping the
                # connection while the model spins up / reasons before token 1
                # (SSE comment line — ignored by the client parser).
                yield ": ping\n\n"

                result: dict[str, Any] | None = None
                emitted_text = False

                if use_bot_route:
                    # Bot-server round: one non-streaming POST per LLM call.
                    # Tool calls come back in the result below and are executed
                    # by the shared local machinery — same as the builtin path.
                    try:
                        async for ev in _with_heartbeat(_pegasus_chat_stream(
                            client, PEGASUS_BOT_SERVER_URL, conv_messages,
                            TOOL_DEFINITIONS, user, request_id,
                            conversation_id, system_prompt, display_text,
                        )):
                            if ev["type"] == "__heartbeat__":
                                yield ": ping\n\n"
                                continue
                            if ev["type"] == "result":
                                result = ev["result"]
                        # no break here — fall through to shared post-processing
                        # (usage, tool_calls, final_text) like the direct path
                    except httpx.HTTPStatusError as e:
                        status = e.response.status_code if e.response is not None else "?"
                        body_txt = e.response.text[:500] if e.response is not None else str(e)
                        yield f"data: {json.dumps({'error': f'Bot server error ({status}): {body_txt}'})}\n\n"
                        return
                    except Exception as e:
                        yield f"data: {json.dumps({'error': f'Bot server error: {e}'})}\n\n"
                        return
                    if request_id not in _active_clients:
                        return
                    if result.get("content"):
                        yield f"data: {json.dumps({'content': result['content']})}\n\n"
                else:
                    # Retry the round on a transient transport drop, but only while
                    # nothing has been streamed to the client yet — retrying after
                    # tokens were emitted would duplicate them.
                    retry = 0
                    while True:
                        try:
                            if is_anthropic:
                                stream = _anthropic_stream(
                                    client, base_url, api_key, model,
                                    conv_messages, system_prompt, max_tokens,
                                )
                            else:
                                stream = _openai_dispatch(
                                    client, base_url, api_key, model,
                                    conv_messages, system_prompt, wire_api,
                                    max_tokens,
                                )
                            async for ev in _with_heartbeat(stream):
                                # Client cancelled mid-stream — stop and release the
                                # HTTP connection instead of draining the response.
                                if request_id not in _active_clients:
                                    await stream.aclose()
                                    return
                                if ev["type"] == "__heartbeat__":
                                    yield ": ping\n\n"
                                    continue
                                if ev["type"] == "text":
                                    emitted_text = True
                                    yield f"data: {json.dumps({'content': ev['text']})}\n\n"
                                elif ev["type"] == "result":
                                    result = ev["result"]
                            break  # stream completed cleanly
                        except httpx.HTTPStatusError as e:
                            status = e.response.status_code if e.response is not None else "?"
                            error_body = e.response.text[:500] if e.response is not None else str(e)
                            yield f"data: {json.dumps({'error': f'LLM API error ({status}): {error_body}'})}\n\n"
                            return
                        except httpx.TransportError as e:
                            # Transient network drop to the LLM endpoint. Retry only
                            # if no tokens were emitted this round yet; otherwise a
                            # retry would duplicate already-streamed content.
                            if not emitted_text and retry < _MAX_STREAM_RETRIES:
                                retry += 1
                                await asyncio.sleep(_STREAM_RETRY_DELAY_S * retry)
                                yield ": reconnecting\n\n"  # SSE comment heartbeat
                                continue
                            yield f"data: {json.dumps({'error': _transport_error_message(e, base_url, emitted_text)})}\n\n"
                            return
                        except Exception as e:
                            yield f"data: {json.dumps({'error': str(e)})}\n\n"
                            return

                if result is None:
                    return

                usage = result.get("usage") or {}
                tot_in += usage.get("input", 0) or 0
                tot_out += usage.get("output", 0) or 0

                tool_calls = result.get("tool_calls", [])
                # B: graceful-close after deny — suppress auto-approved listings
                if pending_deny_close:
                    orig_len = len(tool_calls)
                    filtered = [tc for tc in tool_calls if tc.get("name") not in ("list_directory", "read_file")]
                    if len(filtered) < orig_len:
                        for tc in tool_calls:
                            if tc.get("name") in ("list_directory", "read_file"):
                                conv_messages.append({
                                    "role": "tool",
                                    "tool_call_id": tc["id"],
                                    "content": "Listing skipped — verification was denied.",
                                })
                    tool_calls = filtered
                    pending_deny_close = False

                tool_count += len(tool_calls)
                tools_used.extend(tc.get("name", "") for tc in tool_calls)

                if not tool_calls:
                    # Text already streamed as deltas above. If the model was cut
                    # off at the output-token cap, tell the user (otherwise the
                    # reply just ends mid-sentence with no explanation).
                    final_text = result.get("content", "")
                    if str(result.get("stop_reason", "")).lower() in _TRUNCATED_STOP_REASONS:
                        notice = "\n\n_[Response truncated at the output-token limit.]_"
                        yield f"data: {json.dumps({'content': notice})}\n\n"
                        final_text += notice

                    # Per-turn usage/timing footer and persist the full text
                    # (with the tools used this turn) scoped to the conversation.
                    yield _meta_event()
                    try:
                        msg_id = await db.add_chat_message(
                            "assistant", final_text, agent_id=agent_id,
                            tool_calls=tools_used or None,
                            conversation_id=conversation_id,
                        )
                        # Expose the saved row id so the client can attach 👍/👎.
                        yield f"data: {json.dumps({'message_id': msg_id})}\n\n"
                    except Exception:
                        pass
                    return

                # Prefatory text (if any) was already streamed as deltas.

                # Execute tool calls
                if is_anthropic and not use_bot_route:
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
                        gated_args = {**tc["arguments"], "_call_id": tc["id"]}
                        tool_result = ""
                        async for ev in _run_tool_gated(
                            tc["name"], gated_args,
                            request_id=request_id,
                            require_approval=require_approval,
                            scope=tool_scope,
                        ):
                            if "_final_result" in ev:
                                tool_result = ev["_final_result"]
                            else:
                                yield f"data: {json.dumps(ev)}\n\n"
                        if "denied by the user" in tool_result or "timed out" in tool_result:
                            pending_deny_close = True
                            tool_results.append({
                                "type": "tool_result",
                                "tool_use_id": tc["id"],
                                "content": "Shell verification was denied — do not retry shell or file listings; summarize what was written and ask if the user wants verification.",
                            })
                        else:
                            tool_results.append({
                                "type": "tool_result",
                                "tool_use_id": tc["id"],
                                "content": _clip_tool_result(tool_result),
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
                        gated_args = {**tc["arguments"], "_call_id": tc["id"]}
                        tool_result = ""
                        async for ev in _run_tool_gated(
                            tc["name"], gated_args,
                            request_id=request_id,
                            require_approval=require_approval,
                            scope=tool_scope,
                        ):
                            if "_final_result" in ev:
                                tool_result = ev["_final_result"]
                            else:
                                yield f"data: {json.dumps(ev)}\n\n"
                        if "denied by the user" in tool_result or "timed out" in tool_result:
                            pending_deny_close = True
                            conv_messages.append({
                                "role": "tool",
                                "tool_call_id": tc["id"],
                                "content": "Shell verification was denied — do not retry shell or file listings; summarize what was written and ask if the user wants verification.",
                            })
                        else:
                            conv_messages.append({
                                "role": "tool",
                                "tool_call_id": tc["id"],
                                "content": _clip_tool_result(tool_result),
                            })

            # Reached the tool-round cap without a final text answer — tell the
            # user instead of ending on a silent spinner, and persist the notice
            # so the saved history reflects the truncated turn.
            notice = (
                f"\n\n_[Response stopped: reached the maximum of "
                f"{_MAX_TOOL_ROUNDS} tool-calling rounds.]_"
            )
            yield f"data: {json.dumps({'content': notice})}\n\n"
            yield _meta_event()
            try:
                msg_id = await db.add_chat_message(
                    "assistant", notice.strip(), agent_id=agent_id,
                    tool_calls=tools_used or None,
                    conversation_id=conversation_id,
                )
                yield f"data: {json.dumps({'message_id': msg_id})}\n\n"
            except Exception:
                pass

        except GeneratorExit:
            # Client disconnected — skip the finally's [DONE] yield to avoid
            # "async generator ignored GeneratorExit" noise (BE-9). Resource
            # cleanup below still runs.
            client_gone = True
            raise
        finally:
            _active_clients.pop(request_id, None)
            await client.aclose()
            if not client_gone:
                yield "data: [DONE]\n\n"

    return _brokered_stream(event_generator(), conversation_id)


@router.post("/stop")
async def stop_chat(request: Request) -> dict:
    """Cancel an active streaming request."""
    try:
        body = await request.json()
    except Exception:
        body = {}

    request_id = body.get("request_id", "")
    if request_id and request_id in _active_clients:
        # Release any command-approval prompt this turn is blocked on, so
        # Stop doesn't leave the stream awaiting a decision that never comes.
        from services import chat_permissions

        chat_permissions.deny_all_for(request_id)
        client = _active_clients.pop(request_id)
        await client.aclose()
        return {"status": "stopped", "request_id": request_id}

    # Stop-all only when explicitly requested. Previously an empty/unknown
    # request_id fell through to cancelling *every* active stream, so stopping
    # one tab could kill another tab's (or another user's) in-flight turn.
    if body.get("all"):
        for _rid, client in list(_active_clients.items()):
            try:
                await client.aclose()
            except Exception:
                pass
        _active_clients.clear()
        return {"status": "stopped", "cleared": True}

    return {"status": "noop", "request_id": request_id}


@router.post("/permission")
async def chat_permission(request: Request) -> dict:
    """Resolve a pending command-approval prompt (SEC-4).

    Body: ``{"request_id": str, "call_id": str, "decision": "approve"|"deny"}``.
    The matching streaming turn is blocked in ``await_decision``; this releases
    it. ``noop`` means nothing was waiting (already resolved, timed out, or the
    turn was stopped).
    """
    from services import chat_permissions

    try:
        body = await request.json()
    except Exception:
        body = {}

    request_id = str(body.get("request_id", ""))
    call_id = str(body.get("call_id", ""))
    decision = str(body.get("decision", "")).lower()
    if decision not in ("approve", "deny"):
        raise HTTPException(status_code=400, detail="decision must be approve|deny")
    if not request_id or not call_id:
        raise HTTPException(status_code=400, detail="request_id and call_id required")

    resolved = chat_permissions.resolve(request_id, call_id, decision)  # type: ignore[arg-type]
    return {"status": "resolved" if resolved else "noop", "call_id": call_id}


@router.get("/command-approval")
async def get_command_approval() -> dict:
    """Whether chat pauses for confirmation before running shell commands."""
    from main import db
    from services import chat_permissions

    return {"require_approval": await chat_permissions.require_approval(db)}


@router.put("/command-approval")
async def set_command_approval(request: Request) -> dict:
    """Body: ``{"require_approval": bool}``."""
    from main import db
    from services import chat_permissions

    try:
        body = await request.json()
    except Exception:
        body = {}
    value = bool(body.get("require_approval", True))
    await chat_permissions.set_require_approval(db, value)

    # The OpenCode engine enforces this setting as a `permission` rule in its
    # generated config (#367), so storing it is only half the job: without
    # propagating, Settings said "commands are confirmed" while the engine kept
    # whatever rule it was last given — including `allow` — for the rest of the
    # pod's life. The builtin loop reads the setting per turn and does not care.
    try:
        from llm.propagator import LLMPropagator

        await LLMPropagator().propagate(db)
    except Exception as e:
        log.warning("command_approval_propagate_failed", error=str(e))

    return {"require_approval": value}


@router.post("/feedback")
async def chat_feedback(request: Request) -> dict:
    """Rate an assistant reply useful / not useful.

    Body: ``{"message_id": <int>, "value": 1 | -1 | 0}`` where 1 = useful,
    -1 = not useful, 0 = clear the rating.
    """
    from main import db

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    message_id = body.get("message_id")
    value = body.get("value")
    if not isinstance(message_id, int) or value not in (1, -1, 0):
        raise HTTPException(
            status_code=400,
            detail="message_id (int) and value (1, -1, or 0) are required",
        )

    stored = None if value == 0 else value
    ok = await db.set_chat_feedback(message_id, stored)
    if not ok:
        raise HTTPException(status_code=404, detail="Unknown message_id")
    return {"status": "ok", "message_id": message_id, "value": value}


@router.get("/agents")
async def list_agents() -> dict:
    """List available agent personas."""
    from routers.knowledge import list_agents

    return await list_agents()


@router.get("/scope-block")
async def scope_block(type: str, id: str) -> dict[str, str | None]:
    """The Active Context block for a workflow or run, as plain text.

    The builtin loop appends this to its own system prompt, where the browser
    never sees it. The OpenCode engine has no such seam: the agent loop is not
    ours, so the caller that knows which workflow the conversation is about is
    the panel, and it sends the block as a synthetic prompt part — visible to
    the model, hidden by the renderer, and re-resolved every turn so a
    finished run stops being described as running.

    Returns ``{"block": null}`` for an id that resolves to nothing, which is
    the same "no context, carry on" outcome the builtin path already has.
    """
    from services.chat_scope import SCOPE_TYPES, build_scope_block

    if type not in SCOPE_TYPES:
        raise HTTPException(
            status_code=400, detail=f"Unknown scope type: {type}"
        )
    return {"block": await build_scope_block(type, id)}


@router.get("/history", response_model=ChatHistoryResponse)
async def get_history(
    limit: int = 100, conversation_id: str | None = None
) -> ChatHistoryResponse:
    """Get chat history from the database, optionally scoped to a conversation."""
    from main import db

    rows = await db.get_chat_history(limit=limit, conversation_id=conversation_id)
    messages = [
        ChatMessage(
            id=r.get("id"),
            role=r["role"],
            content=r["content"],
            agent_id=r.get("agent_id"),
            tool_calls=r.get("tool_calls"),
            created_at=r.get("created_at"),
            feedback=r.get("feedback"),
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
