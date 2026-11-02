# studio-webapp Code Review — July 2026

> **Historical snapshot.** Line/citation references have drifted and the
> markers below are July's. The three items July left open were carried into
> the August review (re-checked 2026-11-02): `run_command` is mitigated by
> per-command approval (SEC-4, #68); S7 is fixed by write-only keys (#64) and
> encryption at rest when `STUDIO_SECRET_KEY` is set (#136); S10 was fixed in
> #66 and then partly regressed (the Pegasus apt source is `[trusted=yes]`
> again, see INF-4). The current review is
> [`CODE_REVIEW_2026-08.md`](CODE_REVIEW_2026-08.md).


Critical review focused on the AI chatbot, plus a full sweep of the rest of the
webapp (studio-api routers/services, Next.js frontend, Docker). Each finding was
verified against the source.

Status legend: ☐ open · ⏳ in progress · ✅ fixed · ⏸ deferred (needs decision)

**Update (pass 1):** Fixed the self-contained chat bugs, the chat backend
correctness/security bugs, added markdown rendering, and hardened CORS + the
WebSocket endpoints against CSWSH (plus the S3 PTY leak, fixed opportunistically).
Deferred/open items are noted inline. All 58 backend tests + web typecheck pass;
new `tests/test_security.py` covers the WS origin logic.

**Update (pass 2):** #12 real token streaming, S4/S5, #6 (option a — persist
history properly), #14 context-window management, #15 polish, and S6–S9. Verified
green through #14/#15 *code* (63 backend tests + `tsc` clean). ⚠️ The 4 tests added
last (truncation surfacing, `_trim_history`, `_clip_tool_result`, workflow-runs prune)
and the S6/S7/S8/S9 *code* were written but NOT yet run — the shell environment broke
before the final `pytest`/`tsc` pass. Re-run `pytest -q` in studio-api and
`tsc --noEmit` in studio-web to confirm.

---

## AI Chatbot

### Bugs

- ✅ **1. Duplicate React keys — two independent `msg-N` counters.**
  `chat-input.tsx:10` (`let _msgId = 0`) and `chat-store.ts:64` (`let _msgCounter = 0`)
  both mint ids like `msg-1`. First exchange collides user + assistant keys; after
  reload, reset counters collide with persisted ids. Fix: use `crypto.randomUUID()`
  for all message ids.

- ✅ **2. Streaming writes into whichever conversation is active, not the one that
  started the request.** `appendToLast`/`addToolCall`/`addToolResult`/`setLastMeta`
  all use `s.activeId` at event time (`chat-store.ts:106-168`). Switching conversation
  mid-stream misroutes the rest of the response. Capture the conversation id at request
  start and thread it through.

- ✅ **3. Transient 5xx permanently mis-routes an endpoint to the Responses API.**
  `_openai_dispatch` (`chat.py:516-527`) adds base_url to `_RESPONSES_ENDPOINTS`
  forever on any `status >= 500`. One flaky 500 pins all future traffic to `/responses`.
  Memoize only on the explicit 400-with-hint case; for 404/405/5xx retry once and
  memoize only on success.

- ✅ **4. "Stop" with no request id kills everyone's streams.** `/api/chat/stop`
  (`chat.py:821-828`) falls through to closing all active clients. Client-generated
  `req-${Date.now()}` ids (`chat-input.tsx:88`) can collide across tabs. Generate the
  request id server-side; make stop-all an explicit flag.

- ✅ **5. Stop doesn't abort the client fetch.** `handleStop` (`chat-input.tsx:181-184`)
  flips `isStreaming` but never aborts the fetch/reader; the loop keeps consuming and
  the server error event gets appended. Use an `AbortController`; abort on stop + unmount.

- ✅ **6. Server-side chat history is write-only and incomplete.** Only final assistant
  text is saved, only on the clean exit path (`chat.py:723-728`); user messages, tool
  activity, errors, and truncation are never saved; no conversation_id column; table
  never pruned; UI never reads it. Either persist properly (both roles + conversation_id,
  UI loads from it) or delete the table + endpoint. *Done (option a): added
  `conversation_id` column + idempotent migration + index; both user and assistant turns
  saved (with tool names + the truncation notice) scoped to the conversation; `/history`
  accepts `conversation_id`; hourly prune caps the table (`STUDIO_CHAT_MAX_ROWS`,
  default 5000); the UI hydrates an empty conversation from the DB on open (localStorage
  stays the live store). Covered by new `tests/test_db_chat.py` + a history-scoping test.*

- ✅ **7. Slash-command context lost after first turn.** `_process_slash_command` only
  rewrites the last message (`chat.py:600-604`); frontend re-sends raw `/scaffold …`.
  Turn 2 sees a bare command with no skill prompt. Have the frontend send expanded text,
  or mark skill turns for server re-expansion.

- ✅ **8. Tool-round exhaustion is silent.** Using all `_MAX_TOOL_ROUNDS = 50`
  (`chat.py:683-794`) falls through to `finally` with no meta/warning event. Yield an
  explicit "turn truncated" event.

- ✅ **9. `agent_id` path traversal.** `os.path.join(KNOWLEDGE_ROOT, "agents", f"{agent_id}.md")`
  with client-supplied `agent_id` (`chat.py:257`) reads arbitrary `.md` into the system
  prompt. Reject ids containing `/` or `..`, or allowlist against the agents directory.

- ✅ **10. Anthropic ignores configured base_url.** `chat.py:639-640` hardcodes
  `https://api.anthropic.com`. Also `PROVIDER_DEFAULTS["anthropic"]` has a `/v1` that
  would double up against `_anthropic_request`'s `/v1/messages` if ever honored.

- ✅ **11. Cross-provider API-key bleed.** On override,
  `api_key = prov_config.get("api_key", "") or api_key` (`chat.py:622`) falls back to the
  default provider's key, sending it to another provider's base_url. Fallback should be empty.

### Security posture (needs a decision)

- ⏸ **Chat endpoint runs arbitrary shell** (`run_command`, `chat.py:202-221`) with no
  FastAPI-layer auth; combined with `allow_origins=["*"]` + `allow_credentials=True`
  this is drive-by RCE. `_safe_path` sandboxing is cosmetic while `run_command` exists.
  Restrict CORS, add same-origin/CSRF or bearer check on state-changing endpoints, and
  decide whether the shell tool should be workspace-confined.

### UX improvements

- ✅ **12. No real streaming.** All LLM rounds are non-streaming; the full response
  arrives as one `content` event after up to 120s. Highest-impact fix: request
  `stream: true` and forward deltas. Also send `: ping` heartbeats so proxies don't
  kill idle connections. *Done: all three transports (Anthropic SSE, OpenAI chat
  deltas, Responses API) now stream token-by-token; transport fallback happens at
  the handshake before any token; a `: ping` heartbeat precedes each round. Remaining
  minor gap: no heartbeat during a long (up to 300s) `run_command` execution.*

- ✅ **13. No markdown rendering.** `chat-panel.tsx:96-98` renders `msg.content` as plain
  text in a `prose` div (which does nothing on raw text). Code assistant shows literal
  ``` fences. Add `react-markdown` + syntax highlighting + copy button.

- ✅ **14. No context-window management.** Full conversation (incl. 100KB tool results)
  re-sent every round; frontend errors appended into assistant content and re-sent
  forever. Trim tool results, cap history, keep errors out of the transcript. *Done:
  backend trims history to the most-recent `_MAX_HISTORY_MESSAGES` (40) and clips each
  model-fed tool result to `_MAX_TOOL_RESULT_CHARS` (16k) with a marker; frontend stores
  transient errors in a separate `error` field (rendered distinctly) and excludes
  empty-content messages from the API payload, so failures never re-enter the transcript.*

- ✅ **15. Polish:** Enter-to-send ignores `isComposing` (breaks CJK IME,
  `chat-input.tsx:174-179`); autoscroll fires on every token even when scrolled up;
  UI tool results truncated to 2000 chars with no indicator; `max_tokens` truncation
  never surfaced. *Done: IME + near-bottom autoscroll (earlier pass); UI tool results now
  show a "truncated for display, N chars total" marker; length-capped responses
  (`stop_reason` length/max_tokens/incomplete) append a "truncated at the output-token
  limit" notice.*

---

## Rest of Webapp

### Critical

- ✅ **S1. Cross-Site WebSocket Hijacking on shell endpoints.**
  `routers/terminal.py:143,215`, `routers/ai_terminal.py:34` call `websocket.accept()`
  with no Origin check / no auth. Browsers don't apply SOP/CORS to WS; cookie auth rides
  along. Any page the user visits gets an interactive shell in their container. Fix:
  validate `Origin` against an allowlist (reject with `close(4403)`), or require a
  short-lived ticket from an authenticated HTTP endpoint.

### High

- ✅ **S2. CORS allows credentialed requests from any origin.** `main.py:180-186`:
  `allow_origins=["*"]` + `allow_credentials=True`. Any site can drive the REST API in
  the user's browser — read/write/delete files, submit/cancel workflows, read stored LLM
  API keys. Restrict `allow_origins`, or drop `allow_credentials`.

### Medium

- ✅ **S3. `ai_tool_terminal` leaks PTY + child on idle disconnect.**
  `routers/ai_terminal.py:104-118,145-155`. `asyncio.gather` waits for both relays;
  `_pty_to_ws` stays blocked in `run_in_executor(_read_pty)` when the shell is silent, so
  the kill-on-cleanup `finally` never runs. Use `asyncio.wait(FIRST_COMPLETED)` + cancel,
  or a non-blocking fd like `terminal_sessions.py`.

- ✅ **S4. `/api/tools/{id}/start` spawns an orphaned unread PTY for terminal tools.**
  `routers/tools.py:178`, `services/process_mgr.py:50-101`. The WS endpoint spawns its
  own process; this duplicate blocks once its PTY buffer fills. Don't spawn in `/start`
  for terminal tools. *Done: `/start` no longer spawns for terminal tools (just flips
  the UI status); list_tools pid-liveness reset now guarded to pid-bearing (web) tools;
  `/stop` always clears status; startup resets stale "running". `start_terminal_tool`
  marked deprecated/unused.*

- ✅ **S5. Workflow-monitor polls ~2h, not documented 1h.**
  `services/workflow_monitor.py:116`: `range(3600)` × `sleep(2.0)` = 7200s. *Done:
  derived iteration count from `_POLL_MAX_SECONDS / _POLL_INTERVAL_S` = 1 hour.*

### Low

- ✅ **S6. `POST /api/terminals` has no workspace containment on `cwd`**
  (`routers/terminal.py:103`) while `/api/workflows/submit` does. (`files.py:_safe_path`
  itself is sound.) *Done: `_contained_cwd` confines the session root to the workspace
  (realpath + prefix), falling back to the workspace root otherwise.*
- ⏳ **S7. LLM API keys plaintext in SQLite** (`db.py:39-70`); `validate_provider` echoes
  upstream `resp.text[:200]` to the client (`llm.py:117`). *Partial: stopped echoing the
  raw upstream body (status + reason only); cross-origin readability already closed by
  S2. At-rest encryption of keys still open (acceptable for single-user container).*
- ✅ **S8. `whoami` trusts client `X-Auth-User`** (`main.py:215`) if the gateway doesn't
  strip it. Low impact (display only). *Done: prefer server-injected `JUPYTERHUB_USER`;
  header fallback gated behind `STUDIO_TRUST_AUTH_HEADER` (documented; default preserves
  current behavior).*
- ✅ **S9. Unbounded table growth** (`db.py`): `chat_messages`, `workflow_runs` never
  pruned. *Done: hourly prune caps both (`STUDIO_CHAT_MAX_ROWS`/`STUDIO_RUNS_MAX_ROWS`).*
- ⏸ **S10. Dockerfile supply-chain gaps**: `deb [trusted=yes]` (`Dockerfile:134`), dev
  `.deb` installed without checksum/signature (`Dockerfile:143`). *Deferred by choice —
  not changed blindly: fixing it right needs the Pegasus apt repo's real GPG key + a
  pinned `.deb` SHA256, and a wrong guess breaks the image build. Remediation when those
  values are available: replace `[trusted=yes]` with
  `signed-by=/usr/share/keyrings/pegasus.gpg` (after importing the key), and add
  `echo "<sha256>  pegasus.deb" | sha256sum -c` before `dpkg -i`.*

### Verified non-issues

- `files.py:_safe_path` — sound (realpath + prefix check, resolves symlinks).
- Workflow subprocesses use `create_subprocess_exec` with arg lists (no shell injection).
- Only `create_subprocess_shell` uses are static registry install strings, not user input.
- `terminal_sessions.py` uses the correct non-blocking-fd + polling pattern.
- Secrets injected via `env`/`add_env`, never on argv.
