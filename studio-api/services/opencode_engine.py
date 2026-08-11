"""OpenCode chat engine — drives the built-in chat panel via `opencode serve`.

Scenario B of the chat-engine design: Studio runs ONE shared OpenCode server
(localhost, basic-auth) and the chat panel becomes a client of it. CLI users
join the *same* runtime with `opencode attach $OPENCODE_ATTACH_URL` (env is
injected into Studio terminals), so panel and terminal share sessions live.

Wire protocol (verified against opencode 1.17.20 via its /doc OpenAPI spec):

* ``POST /session``                     -> create session (id ``ses_…``)
* ``POST /session/{id}/prompt_async``   -> send user parts, returns 204
* ``GET  /event``                       -> global SSE; per-session filtering
* ``POST /session/{id}/abort``          -> stop a running turn
* Basic auth: username ``opencode`` + ``OPENCODE_SERVER_PASSWORD``.

Event translation into the chat panel's existing vocabulary:

* ``message.part.delta`` (field=text, part is a *text* part)  -> ``content``
  — deltas also fire for reasoning parts, so part types are tracked from
  ``message.part.updated`` and only real text parts stream to the UI.
* ``message.part.updated`` (part.type=tool) -> ``tool_call`` on running,
  ``tool_result`` on completed/error (deduped per callID+phase).
* ``message.updated`` (assistant)      -> token usage for the meta footer.
* ``session.idle``                     -> end of turn.
* ``session.error``                    -> ``error``.
* ``permission.asked``                 -> auto-approved ("always") to match
  the built-in chat's execute-without-asking behavior. Tightening this is
  the same policy decision as the run_command posture (TODO-PLAN item 1).
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import time
from typing import TYPE_CHECKING, Any, AsyncGenerator

import httpx
import structlog

from services.chat_permissions import await_decision, require_approval

if TYPE_CHECKING:
    from db import Database

log = structlog.get_logger()


def _permission_summary(props: dict[str, Any]) -> str:
    """Best-effort one-liner describing what opencode wants to run, for the
    approval card. opencode's permission.asked shape varies by tool, so pull
    the command from metadata when present and fall back to the title."""
    meta = props.get("metadata") or {}
    cmd = ""
    if isinstance(meta, dict):
        cmd = str(meta.get("command") or meta.get("cmd") or "")
    return (cmd or props.get("title") or props.get("pattern") or "").strip()[:300]

OPENCODE_BIN = os.environ.get("STUDIO_OPENCODE_BIN", "opencode")
OPENCODE_PORT = int(os.environ.get("STUDIO_OPENCODE_PORT", "4747"))
BASE_URL = f"http://127.0.0.1:{OPENCODE_PORT}"

_PASSWORD_KEY = "opencode_server_password"
_SESSION_MAP_KEY = "opencode_sessions"
_SESSION_MAP_MAX = 200

_HEALTH_TIMEOUT = 3.0
_SPAWN_WAIT_S = 20.0
_TURN_TIMEOUT_S = 600.0
_PING_INTERVAL_S = 10.0

# Result text shown in the tool chip is display-only; keep it bounded like the
# built-in chat's _clip_ui_result.
_UI_RESULT_LIMIT = 2000


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload)}\n\n"


class OpenCodeEngine:
    """Owns the shared `opencode serve` process and the chat-turn proxy."""

    def __init__(self) -> None:
        self._db: Database | None = None
        self._proc: asyncio.subprocess.Process | None = None
        self._password: str | None = None
        self._agents: set[str] | None = None
        self._lock = asyncio.Lock()

    def set_db(self, db: Database) -> None:
        self._db = db

    # --- credentials / client ---

    async def password(self) -> str:
        """Server password — generated once, persisted so terminals stay valid."""
        if self._password:
            return self._password
        pw = None
        if self._db is not None:
            pw = await self._db.get_setting(_PASSWORD_KEY)
        if not pw:
            pw = secrets.token_urlsafe(24)
            if self._db is not None:
                await self._db.set_setting(_PASSWORD_KEY, pw)
        self._password = pw
        return pw

    async def _auth(self) -> httpx.BasicAuth:
        # opencode's basic auth accepts only the default username.
        return httpx.BasicAuth("opencode", await self.password())

    def _client(self, auth: httpx.BasicAuth, timeout: float = 30.0) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=BASE_URL, auth=auth, timeout=timeout)

    async def attach_env(self) -> dict[str, str]:
        """Env overlay for Studio terminals so `opencode attach` just works."""
        return {
            "OPENCODE_ATTACH_URL": BASE_URL,
            "OPENCODE_SERVER_PASSWORD": await self.password(),
        }

    def attach_command(self) -> str:
        return f"opencode attach {BASE_URL} --password $OPENCODE_SERVER_PASSWORD"

    # --- lifecycle ---

    async def _healthy(self, auth: httpx.BasicAuth) -> bool:
        try:
            async with self._client(auth, timeout=_HEALTH_TIMEOUT) as c:
                r = await c.get("/api/health")
                return r.status_code == 200
        except httpx.HTTPError:
            return False

    async def ensure_running(self) -> str | None:
        """Start `opencode serve` if it isn't already up. Returns an error
        message when the engine can't be made available (caller falls back)."""
        async with self._lock:
            auth = await self._auth()
            if await self._healthy(auth):
                return None

            workspace = os.environ.get(
                "STUDIO_WORKSPACE_DIR",
                os.path.join(os.environ.get("HOME", "/tmp"), "work"),
            )
            env = {**os.environ, "OPENCODE_SERVER_PASSWORD": await self.password()}
            try:
                self._proc = await asyncio.create_subprocess_exec(
                    OPENCODE_BIN, "serve",
                    "--port", str(OPENCODE_PORT),
                    "--hostname", "127.0.0.1",
                    cwd=workspace if os.path.isdir(workspace) else None,
                    env=env,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
            except FileNotFoundError:
                return (
                    "OpenCode is not installed — install it from the "
                    "Workbench, or switch the chat engine back to Built-in."
                )
            except OSError as e:
                return f"Could not start opencode serve: {e}"

            deadline = time.monotonic() + _SPAWN_WAIT_S
            while time.monotonic() < deadline:
                if await self._healthy(auth):
                    log.info("opencode_serve_started", port=OPENCODE_PORT)
                    self._agents = None  # refresh agent list for new server
                    return None
                if self._proc.returncode is not None:
                    return (
                        "opencode serve exited immediately "
                        f"(code {self._proc.returncode})."
                    )
                await asyncio.sleep(0.5)
            return "opencode serve did not become healthy in time."

    async def stop(self) -> None:
        """Terminate the serve process we spawned (container shutdown)."""
        if self._proc and self._proc.returncode is None:
            self._proc.terminate()
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                self._proc.kill()
        self._proc = None

    async def restart(self) -> None:
        """Force the next turn to re-spawn serve with fresh config.

        ``opencode serve`` reads provider config only at startup and never
        reloads it, so a provider/model change requires a real restart. We stop
        the tracked process AND defensively kill any orphan serve bound to our
        port (e.g. one spawned by a previous studio-api process or outside it) —
        otherwise ``ensure_running`` would find the stale-config instance
        "healthy" and skip the respawn, so the change silently wouldn't apply.
        """
        await self.stop()
        try:
            proc = await asyncio.create_subprocess_exec(
                "pkill", "-f", f"opencode serve --port {OPENCODE_PORT}",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
        except (OSError, asyncio.CancelledError):
            pass  # pkill absent or interrupted — stop() already handled ours

    async def forget_sessions(self, db: Database) -> None:
        """Drop conversation→session mappings so the next turn creates fresh
        opencode sessions.

        An opencode session is bound to the provider/model it was created with;
        after a provider switch, reusing it fails with
        ``Model not found: <old-provider>/<old-model>``. Clearing the map resets
        only the opencode-side linkage — studio conversation history is untouched.
        """
        try:
            await db.set_setting(_SESSION_MAP_KEY, {})
        except Exception as e:  # best-effort; never block a config change
            log.warning("opencode_forget_sessions_failed", error=str(e))

    async def status(self) -> dict[str, Any]:
        """Engine status for the settings UI."""
        import shutil

        auth = await self._auth()
        running = await self._healthy(auth)
        return {
            "installed": shutil.which(OPENCODE_BIN) is not None,
            "running": running,
            "url": BASE_URL,
            "attach_command": self.attach_command(),
        }

    # --- sessions ---

    async def _known_agents(self, auth: httpx.BasicAuth) -> set[str]:
        if self._agents is not None:
            return self._agents
        agents: set[str] = set()
        try:
            async with self._client(auth) as c:
                r = await c.get("/agent")
                if r.status_code == 200:
                    for a in r.json():
                        name = a.get("name") if isinstance(a, dict) else None
                        if name:
                            agents.add(str(name))
        except (httpx.HTTPError, ValueError):
            pass
        self._agents = agents
        return agents

    async def _session_for(
        self, auth: httpx.BasicAuth, conversation_id: str | None
    ) -> str:
        """Map a studio conversation to an opencode session (create on miss).

        The mapping lives in DB settings so panel reloads and CLI attach see
        the same session; opencode's own store remains the source of truth.
        """
        key = conversation_id or "default"
        mapping: dict[str, str] = {}
        if self._db is not None:
            stored = await self._db.get_setting(_SESSION_MAP_KEY, {})
            if isinstance(stored, dict):
                mapping = {str(k): str(v) for k, v in stored.items()}

        sid = mapping.get(key)
        if sid:
            try:
                async with self._client(auth) as c:
                    if (await c.get(f"/session/{sid}")).status_code == 200:
                        return sid
            except httpx.HTTPError:
                pass  # unreachable/gone — create a fresh session below

        async with self._client(auth) as c:
            r = await c.post("/session", json={"title": f"studio chat {key}"})
            r.raise_for_status()
            sid = str(r.json()["id"])

        mapping[key] = sid
        if len(mapping) > _SESSION_MAP_MAX:
            for stale in list(mapping)[: len(mapping) - _SESSION_MAP_MAX]:
                mapping.pop(stale, None)
        if self._db is not None:
            await self._db.set_setting(_SESSION_MAP_KEY, mapping)
        return sid

    async def _active_model(self) -> dict[str, str] | None:
        """The active provider/model as an explicit prompt-time model ref.

        An opencode session remembers the model of its last message and reuses
        it when a prompt names none — so a session created under an old
        provider keeps calling that provider forever ("Model not found:
        <old-provider>/<old-model>") if a provider switch ever bypasses
        ``forget_sessions`` (manual DB repair, restored PVC state). Naming the
        model on every prompt makes each turn follow the active config,
        whatever the session remembers.
        """
        if self._db is None:
            return None
        try:
            cfg = await self._db.get_llm_config()
        except Exception as e:
            log.warning("opencode_active_model_lookup_failed", error=str(e))
            return None
        if not cfg or not cfg.get("provider"):
            return None
        from llm.providers import PROVIDERS, provider_type

        provider = str(cfg["provider"])
        model = str(cfg.get("model") or "")
        if not model:
            preset = PROVIDERS.get(provider_type(provider), {})
            model = str(preset.get("default_model") or "")
        if not model:
            return None
        # A stored model may carry a provider prefix; opencode wants a bare id.
        return {"providerID": provider, "modelID": model.rsplit("/", 1)[-1]}

    async def _abort(self, sid: str) -> None:
        try:
            async with self._client(await self._auth(), timeout=5.0) as c:
                await c.post(f"/session/{sid}/abort")
        except httpx.HTTPError:
            pass

    async def _final_text(self, auth: httpx.BasicAuth, sid: str) -> str:
        """Concatenated text parts of the session's last assistant message."""
        try:
            async with self._client(auth) as c:
                r = await c.get(f"/session/{sid}/message")
                r.raise_for_status()
                messages = r.json()
        except (httpx.HTTPError, ValueError):
            return ""
        for m in reversed(messages):
            if (m.get("info") or {}).get("role") == "assistant":
                return "".join(
                    p.get("text") or ""
                    for p in m.get("parts", [])
                    if p.get("type") == "text"
                )
        return ""

    # --- the chat turn ---

    async def stream_turn(
        self,
        *,
        text: str,
        conversation_id: str | None,
        agent_id: str | None,
        request_id: str,
        active_clients: dict[str, httpx.AsyncClient],
    ) -> AsyncGenerator[str, None]:
        """Send one user turn and yield studio-format SSE strings.

        Registers its event-stream client under *active_clients* so the
        existing POST /api/chat/stop contract cancels an OpenCode turn too.
        The final assistant text is exposed via ``self.last_turn`` for the
        caller to persist. Caller emits the trailing ``[DONE]``.
        """
        self.last_turn: dict[str, Any] = {"text": "", "tools": []}
        auth = await self._auth()

        try:
            sid = await self._session_for(auth, conversation_id)
        except Exception as e:
            # Not just httpx errors: a non-JSON /session response raises
            # ValueError/KeyError and a DB failure raises whatever aiosqlite
            # does — all of them must reach the user as an error frame, not
            # escape and end the turn as a bare [DONE].
            log.warning("opencode_session_failed", error=str(e))
            yield _sse({"error": f"OpenCode session error: {e}"})
            return

        t0 = time.monotonic()
        tokens_by_msg: dict[str, dict[str, int]] = {}
        part_types: dict[str, str] = {}
        tool_names: dict[str, str] = {}
        tool_phase_seen: set[str] = set()
        emitted_text: list[str] = []
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=500)

        stream_client = self._client(auth, timeout=httpx.Timeout(_TURN_TIMEOUT_S, connect=10.0))
        active_clients[request_id] = stream_client

        async def _reader() -> None:
            try:
                async with stream_client.stream("GET", "/event") as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if line.startswith("data:"):
                            try:
                                ev = json.loads(line[5:].strip())
                            except json.JSONDecodeError:
                                continue
                            await queue.put(ev)
            except (httpx.HTTPError, RuntimeError, asyncio.CancelledError):
                pass
            finally:
                try:
                    queue.put_nowait(None)
                except asyncio.QueueFull:
                    pass

        reader = asyncio.create_task(_reader())
        aborted = False

        try:
            # Subscribe to events BEFORE prompting so no early event is lost.
            # Wait for the stream's first event (server.connected) or just
            # proceed after a short beat.
            await asyncio.sleep(0.3)

            agents = await self._known_agents(auth)
            payload: dict[str, Any] = {"parts": [{"type": "text", "text": text}]}
            model = await self._active_model()
            if model:
                payload["model"] = model
            if agent_id and agent_id != "general" and agent_id in agents:
                payload["agent"] = agent_id

            async with self._client(auth) as c:
                r = await c.post(f"/session/{sid}/prompt_async", json=payload)
                if r.status_code not in (200, 204):
                    yield _sse({
                        "error": (
                            f"OpenCode rejected the prompt "
                            f"({r.status_code}): {r.text[:300]}"
                        )
                    })
                    return

            deadline = time.monotonic() + _TURN_TIMEOUT_S
            last_emit = time.monotonic()
            while time.monotonic() < deadline:
                # Stop pressed: the registry entry is gone (client closed).
                if request_id not in active_clients:
                    aborted = True
                    return
                # Ping on silence toward the CLIENT, not queue inactivity —
                # filtered events (other sessions, global chatter, status
                # churn) keep the queue busy without emitting a byte, and an
                # idle-timeout proxy would kill the response mid-turn.
                wait = _PING_INTERVAL_S - (time.monotonic() - last_emit)
                if wait <= 0:
                    yield ": ping\n\n"
                    last_emit = time.monotonic()
                    continue
                try:
                    ev = await asyncio.wait_for(queue.get(), timeout=wait)
                except asyncio.TimeoutError:
                    continue
                if ev is None:  # event stream ended underneath us
                    # Keep whatever already streamed so the caller persists
                    # the partial turn instead of dropping it from history.
                    self.last_turn["text"] = "".join(emitted_text)
                    yield _sse({"error": "OpenCode event stream disconnected."})
                    return

                etype = ev.get("type")
                props = ev.get("properties") or {}
                if props.get("sessionID") not in (None, sid):
                    continue
                if props.get("sessionID") is None and etype not in (
                    "permission.asked",
                ):
                    # Global chatter (plugin.added etc.) — not session-scoped.
                    continue

                if etype == "message.part.updated":
                    part = props.get("part") or {}
                    pid = str(part.get("id") or "")
                    ptype = str(part.get("type") or "")
                    if pid:
                        part_types[pid] = ptype
                    if ptype == "tool":
                        call_id = str(part.get("callID") or pid)
                        tool = str(part.get("tool") or "tool")
                        tool_names[call_id] = tool
                        state = part.get("state") or {}
                        status = str(state.get("status") or "")
                        if status == "running" and f"{call_id}:call" not in tool_phase_seen:
                            tool_phase_seen.add(f"{call_id}:call")
                            self.last_turn["tools"].append(tool)
                            yield _sse({"tool_call": {
                                "id": call_id, "name": tool,
                                "arguments": state.get("input") or {},
                            }})
                            last_emit = time.monotonic()
                        elif status in ("completed", "error") and f"{call_id}:result" not in tool_phase_seen:
                            tool_phase_seen.add(f"{call_id}:result")
                            result = state.get("output") or state.get("error") or status
                            yield _sse({"tool_result": {
                                "id": call_id, "name": tool,
                                "result": str(result)[:_UI_RESULT_LIMIT],
                            }})
                            last_emit = time.monotonic()

                elif etype == "message.part.delta":
                    # Reasoning parts also stream text deltas — only forward
                    # deltas for parts we know to be real text parts.
                    pid = str(props.get("partID") or "")
                    if (
                        props.get("field") == "text"
                        and part_types.get(pid) == "text"
                    ):
                        delta = props.get("delta") or ""
                        if delta:
                            emitted_text.append(delta)
                            yield _sse({"content": delta})
                            last_emit = time.monotonic()

                elif etype == "message.updated":
                    info = props.get("info") or {}
                    if info.get("role") == "assistant":
                        toks = info.get("tokens") or {}
                        if isinstance(toks, dict):
                            tokens_by_msg[str(info.get("id"))] = {
                                "input": int(toks.get("input") or 0),
                                "output": int(toks.get("output") or 0),
                            }

                elif etype == "permission.asked":
                    # SEC-4: forward opencode's own permission prompt to the
                    # user instead of auto-approving. opencode only asks for
                    # genuinely sensitive actions (bash, external edits), so
                    # this is the shell-command gate for the opencode engine.
                    perm_id = (props.get("id") or ev.get("id") or "")
                    perm_sid = props.get("sessionID") or sid
                    if perm_id and perm_sid == sid:
                        decision = "approve"
                        if self._db is not None and await require_approval(self._db):
                            yield _sse({"permission_request": {
                                "id": perm_id,
                                "name": props.get("type") or "command",
                                "summary": _permission_summary(props),
                            }})
                            last_emit = time.monotonic()
                            decision = await await_decision(request_id, perm_id)
                        # opencode grammar: always=approve, reject=deny.
                        response = "always" if decision == "approve" else "reject"
                        try:
                            async with self._client(auth, timeout=5.0) as c:
                                await c.post(
                                    f"/session/{perm_sid}/permissions/{perm_id}",
                                    json={"response": response},
                                )
                        except httpx.HTTPError:
                            pass
                        last_emit = time.monotonic()

                elif etype == "session.error":
                    err = props.get("error") or {}
                    name = err.get("name") if isinstance(err, dict) else None
                    detail = ""
                    if isinstance(err, dict):
                        detail = str(
                            (err.get("data") or {}).get("message")
                            or name or "unknown error"
                        )
                    yield _sse({"error": f"OpenCode: {detail}"})
                    return

                elif etype == "session.idle":
                    break
            else:
                self.last_turn["text"] = "".join(emitted_text)
                yield _sse({"error": "OpenCode turn timed out."})
                aborted = True
                return

            # Non-streaming providers may deliver text only in the final
            # message — backfill if nothing streamed.
            final_text = "".join(emitted_text)
            if not final_text:
                final_text = await self._final_text(auth, sid)
                if final_text:
                    yield _sse({"content": final_text})
            self.last_turn["text"] = final_text

            tot_in = sum(t["input"] for t in tokens_by_msg.values())
            tot_out = sum(t["output"] for t in tokens_by_msg.values())
            yield _sse({"meta": {
                "input_tokens": tot_in,
                "output_tokens": tot_out,
                "tokens": tot_in + tot_out,
                "tool_calls": len([k for k in tool_phase_seen if k.endswith(":call")]),
                "duration_s": round(time.monotonic() - t0, 1),
                "engine": "opencode",
            }})

        except GeneratorExit:
            aborted = True
            raise
        except Exception as e:
            # opencode serve died mid-turn, the prompt POST failed, or an
            # event had an unexpected shape (json/KeyError). Without this the
            # exception escaped to the router, which ended the SSE stream
            # with a bare [DONE] — no error text, nothing persisted, cause
            # only in logs. Surface it and keep the partial turn.
            log.warning("opencode_turn_failed", error=str(e), exc_info=True)
            self.last_turn["text"] = "".join(emitted_text)
            yield _sse({"error": f"OpenCode turn failed: {e}"})
        finally:
            if aborted:
                # Fire-and-forget: the generator may be closing (GeneratorExit
                # forbids awaiting here), so schedule the abort as a task.
                asyncio.get_running_loop().create_task(self._abort(sid))
            reader.cancel()
            active_clients.pop(request_id, None)
            try:
                await stream_client.aclose()
            except Exception:
                pass


# Process-wide singleton.
engine = OpenCodeEngine()
