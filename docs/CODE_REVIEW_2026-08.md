# studio-webapp Code Review — August 2026

Full-repo review covering the FastAPI backend (`studio-api`), the Next.js frontend
(`studio-web`), and the container/CI/infra layer. Conducted with five parallel
focused passes (backend security, backend correctness, frontend, container/infra,
tests + docs); every reported finding was verified against the source, and the
highest-severity items were re-checked by hand for this writeup.

Reviewed at branch `refactor/sidebar-version` (`f4bc70c`).

## Gate results (run for this review)

- **Backend tests**: `pytest -q` in `studio-api` → **185 passed** (1 deprecation warning).
- **Frontend types**: `tsc --noEmit` in `studio-web` → **clean** (strict mode).

Both gates are green. CI runs neither (see INF-1), so these were run locally.

Status legend: ☐ open · ✅ verified fixed since July · ⏸ deferred (needs a decision)

Findings fixed after this review carry a `**Status:**` line naming the PR;
anything without one is still open.

---

## Executive summary

The codebase is in good shape overall: the July 2026 review's fixes are all still
in place (WebSocket CSWSH guards, CORS lockdown, notification ledger, NFS-robust
run deletion, param-introspection safety, opencode session handling), the backend
test suite is genuinely substantive rather than tautological, and the frontend's
per-user-prefix handling — historically the #1 bug source — is now clean across all
47 TS/TSX files.

The issues worth acting on cluster into five themes:

1. **Blocking work on the async event loop** (BE-1) — the single biggest backend
   robustness problem: synchronous NFS globs and `pegasus-status` subprocesses run
   directly in async handlers and the 10 s notification scan, freezing every
   request/stream for up to 15 s × N runs.
2. **Silent failure in the chat + live-view UI** (FE-1, FE-2, FE-6, BE-6) — HTTP
   errors and dropped SSE streams are swallowed, producing "chat/DAG is just
   broken" with no message. This is the same class the July review kept hitting.
3. **Subprocess orphaning on timeout** (BE-2) — `wait_for` cancels the wait, not
   the child, so a timed-out `pegasus-plan --submit` can still submit a second DAG.
4. **Supply-chain / deploy safety** (INF-1, INF-2, SEC-1) — the auto-deploying
   `:main` tag is pushed with no test gate, there's no `.dockerignore`, and the
   marketplace `git clone`s a URL taken verbatim from a community-editable registry.
5. **Docs have drifted materially** (DOC-*) — `studio-web/README.md` describes
   removed routes, `studio-api/README.md` misstates the entrypoint and a now-
   destructive DELETE, and both untracked docs (`TODO-PLAN.md`,
   `CODE_REVIEW_2026-07.md`) are stale ~40 commits later.

Prior open security items remain open and are re-confirmed here: unrestricted
`run_command` shell (SEC-4), plaintext LLM keys (SEC-3), Dockerfile `[trusted=yes]`
(INF-4 / SEC-1 group).

---

## Backend — correctness & robustness

### BE-1 · HIGH · Blocking filesystem scans and subprocesses run on the event loop
**Status:** ✅ fixed — PR #132 (2026-08-12): async wrappers
(`discover_runs_async`/`discover_projects_async`/`get_run_status_async`) run the
sync bodies in a thread-pool executor; applied at all workflows.py/notification/
monitor callers; `list_workflows` no longer double-computes status. Deferred: the
optional short-TTL scan cache (staleness/invalidation surface) — own follow-up.
Synchronous work runs directly inside async handlers and the notification loop:
`discover_runs()` / `discover_projects()` / `get_run_status()` recursively glob
`**/braindump.yml` over all of `$HOME` on NFS, parse YAML, read the stampede DB,
and call `subprocess.run(["pegasus-status", ...], timeout=15)`.

- Hot async callers: `routers/workflows.py:78-83,124,151,172,227,271,391,597,778`
- Notification scan every 10 s: `services/notification_engine.py:189,205`
- `get_workflow_detail`'s no-events fallback re-enters the blocking path even
  though it off-loads `read_events` to an executor two lines earlier
  (`services/workflow_monitor.py:33`)
- `discover_projects()` calls `get_run_status()` per run, chaining multiple 15 s
  `pegasus-status` invocations (`services/workflow_scanner.py:212`)

**Impact:** with a few non-terminal runs (or an NFS stall), every request, SSE
stream, terminal WebSocket, and the chat stream freeze for up to 15 s × N runs on
each Workflows poll and each 10 s scan. `routers/dashboard.py:34-35` already does
this correctly with `run_in_executor` — the fix pattern exists in-repo and should
be applied to these paths. **Bonus:** `list_workflows` computes `get_run_status`
then `get_workflow_detail`, which recomputes it — up to two `pegasus-status` runs
per run per poll.

### BE-2 · HIGH · `wait_for(proc.communicate(), timeout)` orphans the subprocess
**Status:** ✅ fixed — PR #62 (2026-08-11): shared `services/procutil.py`
`communicate_or_kill` (kill + reap + re-raise) applied at all ten sites.
`asyncio.wait_for` cancels the wait, not the child. On timeout the code returns an
error while `pegasus-plan --submit` / `pegasus-run` / the generator / npm keeps
running detached.

- `services/workflow_submitter.py:69,155,187,239`; `routers/workflows.py:193,302`;
  `services/workflow_monitor.py:152`; `services/generator_introspect.py:68`
  (verified — returns on `TimeoutError` with no `proc.kill()`); `services/installer.py:123`;
  `main.py:131`

**Impact:** a slow `pegasus-plan --submit` times out, the UI reports failure, the
user resubmits, and the original later completes and submits anyway → two live
DAGs for one click. `generator_introspect` runs on **every** param-form open, so a
generator that hangs at import leaks a `python3` process each time. `marketplace.py:226-227`
and `image_builder.py:172-176` kill correctly — apply that pattern consistently.

### BE-3 · MEDIUM · `OpenCodeEngine.last_turn` is shared singleton state
**Status:** ✅ fixed — PR #126 (2026-08-12): stream_turn writes into a per-call turn_sink; router persists from that, not the shared last_turn.
`self.last_turn` is reset per call on the process-wide `engine`
(`services/opencode_engine.py:360`, consumed at `routers/chat.py:937-947`). Two
overlapping turns (two tabs, or the main panel + the Jupyter-iframed chat) race:
the second `stream_turn` wipes the first's accumulator, so conversation A persists
conversation B's assistant text/tools via `db.add_chat_message`. Streamed output is
correct; only the persisted history row and its feedback `message_id` are wrong.

### BE-4 · MEDIUM · `_trim_history` can yield an Anthropic-invalid message list
**Status:** ✅ fixed — PR #126 (2026-08-12): trim advances to the first genuine (string-content) user turn, never an assistant/tool-result boundary.
`msgs[-40:]` (`routers/chat.py:112-118`, applied at `:1092`) — verified. For an
alternating u/a history ending in the new user message (odd length), the 40-length
tail starts with an `assistant` message, which the Anthropic Messages API rejects
("first message must use the 'user' role"). Once an Anthropic conversation exceeds
40 messages, every subsequent turn 400s. OpenAI-compatible providers are unaffected.

### BE-5 · MEDIUM · Legacy `PUT /api/settings` bypasses key validation and wipes the key
**Status:** ✅ fixed — PR #126 (2026-08-12): routed through a shared `save_llm_config` (no-wipe + check_api_key_requirement), same as PUT /api/llm/config.
`routers/settings.py:66-84` calls `db.set_llm_config(...)` directly, lacking both
the "omitted key keeps the saved one" logic and the PR #52 `check_api_key_requirement()`.
A client saving via `/api/settings` with `api_key` omitted nulls the stored key and
can re-save a keyless required-key provider — resurrecting the "No api key passed in"
chat failure the codebase specifically fixed. Route this through the same path as
`routers/llm.py:72-104` or retire it.

### BE-6 · MEDIUM · OpenCode chat turn has no error handling around the proxy
**Status:** ✅ fixed — PR #60 (2026-08-11): engine catches `Exception` → error
frame + partial-turn persistence; router gains a backstop except; 4 new tests.
`routers/chat.py:917-950` has only a `finally`; `opencode_engine._session_for`
catches only `httpx.HTTPError` and the prompt POST / event-loop body catch nothing
but `GeneratorExit` (`:364-425`). If `opencode serve` dies mid-turn or an event has
an unexpected shape (`json`/`KeyError`), the turn ends instantly with a bare
`[DONE]`, no error text, and nothing persisted — the cause only appears in logs.

### BE-7 · MEDIUM · Several run endpoints resolve `run_dir` from the DB only
**Status:** ✅ fixed — PR #126 (2026-08-12): shared `_resolve_run_dir` (DB then discover_runs fallback) used by jobs/events/analyze/estimate/get.
`/{run_id}/jobs`, `/{run_id}/events`, `/{run_id}/analyze`
(`routers/workflows.py:661-693,740-756`) lack the `discover_runs()` fallback that
`get_workflow` and `estimate` have, so they 404 for any run not yet upserted by
`GET /api/workflows` (fresh DB after restart, a pruned row, or a direct deep-link).

### BE-8 · MEDIUM · Misc state/scan bugs
**Status:** ✅ fixed — PR #126 (2026-08-12): get_tool pid-guard; run graph workflow_status derived from node statuses; notification engine watches the scanner root.
- **`get_tool` resets running terminal tools to "installed"** — `routers/tools.py:86`
  lacks the `pid is not None` guard that `list_tools` has at `:55`; `_is_pid_alive(None)`
  is False, so any tool-detail hit flips a running (pid-less) terminal tool's badge off.
- **`run_graph_json` `workflow_status` is always `None`** — `routers/workflows.py:627`
  reads `run.get("status")`, but `discover_runs()` entries never carry `"status"`
  (`services/workflow_scanner.py:128-141`); the DAG viewer can't tell running from done.
- **Notification engine watches `~/work`, run list watches `$HOME`** — `main.py:229-233`
  vs `services/workflow_scanner.py:80-83`; a run outside `~/work` is listed but never
  produces a completed/failed notification.

### BE-9 · LOW · Smaller robustness items
**Status:** ✅ mostly fixed — PR #126 (2026-08-12): yield-in-finally noise, condor cache-lock fast path, image_builder drain guard, terminal GOOGLE_API_KEY, rmtree onexc, bounded _RESPONSES_ENDPOINTS/_seen, marketplace id-collision. Deferred: _tail_jsonl overhaul (BE-1-adjacent sync-NFS I/O), unbounded terminal client queues.
- `_tail_jsonl` (`services/workflow_monitor.py:89-109`) never emits `[DONE]`, holds a
  file handle per client forever after the run finishes, goes silent on log rewrite,
  and uses sync `open/readline` on NFS.
- `yield` inside `finally` after client disconnect raises
  `RuntimeError("async generator ignored GeneratorExit")` on every Stop/tab-close
  (`routers/chat.py:949-950,1282-1285`; `routers/workflows.py:252-253`) — log noise
  that masks real errors; cleanup itself still runs.
- `condor_pool.get_status` holds `_cache_lock` across all queries on cold cache
  (`services/condor_pool.py:527-560`) → callers can queue ~2 min.
- `image_builder._run_streaming` unguarded `stdin.drain()` (`:147-150`) can raise
  `BrokenPipeError` mid-SSE instead of a login-failed event.
- `terminal.py._llm_env` lacks the `google → GOOGLE_API_KEY` mapping that
  `tools.py` has (`:74-79` vs `tools.py:142-148`).
- `_best_effort_rmtree` uses `shutil.rmtree(onerror=...)`, deprecated since 3.12
  (`services/workflow_submitter.py:214`).
- Unbounded module-level state: `notification_engine._seen`, terminal client
  queues, `_RESPONSES_ENDPOINTS` — slow leaks on long-lived pods.
- `marketplace._catalog` id-collision silently drops one entry (`routers/marketplace.py:173-180`).

**Known-history checks (all still fixed):** stale opencode session map, notification
read-state ledger, NFS-busy run deletion, param-introspection non-submit — all
verified intact. Two residual introspection gaps: a generator that imports argparse
but never calls `parse_args` still runs its top-level under `runpy`, and BE-2's
timeout-orphan can submit after the API returned "timed out".

---

## Backend — security

Threat model: single-user per-user container behind an authenticating gateway, so
"user attacks self" is low; weighted up are cross-pod reachability, secret
exfiltration, SSRF/RCE into the cluster, and injection from untrusted external
content (prompt injection, third-party registry).

### SEC-1 · HIGH · Argument/transport injection into `git clone` from an untrusted registry
**Status:** ✅ fixed — PR #64 (2026-08-11): http(s)-only at `_normalize` and at
clone time, `--` separator, `GIT_ALLOW_PROTOCOL=http:https`.
`routers/marketplace.py:218-224` (verified). `workflow_id` is slug-validated, but
`entry["repo_url"]` comes verbatim from the external, community-editable PegasusHub
registry JSON and is passed as a positional arg to `git clone --depth 1 <repo_url>
<dir>` with no scheme allowlist and no `--` separator. `create_subprocess_exec`
stops classic shell injection, but a registry entry with `repo_url = ext::sh -c
'…'` (git's `ext::` transport executes a shell for user-initiated clones) or a
leading-`-` value (interpreted as a git flag) runs code in the user's pod on a
benign "Add" click. **Fix:** require `https://`/`http://`/`git@`, reject
`ext::`/`file::`/leading-`-`, and/or set `GIT_ALLOW_PROTOCOL=https`.

### SEC-2 · MEDIUM · AI-tool web UI binds `0.0.0.0`
**Status:** ✅ fixed — PR #66 (2026-08-11): loopback binding (nothing in the
product linked to the pod-IP port anyway); free-port probe also loopback.
`services/installer.py:172` (`opencode web --hostname 0.0.0.0`) and
`services/process_mgr.py:177` (`s.bind(("", 0))`) open the tool port on the pod's
cluster IP. nginx only exposes 8888, so on a flat pod network with no NetworkPolicy
(kubeadm default), another pod can reach the user's OpenCode web session — their
files and configured LLM keys — with no auth. Bind to `127.0.0.1` and proxy locally.

### SEC-3 · MEDIUM · LLM API keys stored plaintext **and echoed back in full**
**Status:** ✅ echo fixed — PR #64 (2026-08-11): keys are write-only
(`has_api_key` presence flag, registry-password pattern); model discovery for
saved providers moved server-side. Plaintext **at rest** (S7) — ✅ fixed in
PR #136 (2026-08-12): `secretbox` envelope-encrypts stored keys + the registry
password with a key derived from `STUDIO_SECRET_KEY` (fail-open to plaintext +
warning when unset; transparent migration). Activation needs studio-system to
inject the sealed secret.
Beyond the known at-rest issue (`db.py:51-59,74-82`), the raw `api_key` for every
provider is serialized back to the browser by `GET /api/llm/config` and
`GET /api/llm/provider-configs` (`models.py:112-118,151-158`; `routers/llm.py:57-69,177-185`).
The registry password is correctly never echoed (`routers/settings.py:27-36` returns
`has_password`); keys should get the same write-only treatment. Any script in the
studio origin (SPA XSS, a loaded Jupyter extension) can exfiltrate all keys.

### SEC-4 · MEDIUM · `run_command` runs arbitrary shell from the model (prior open item)
**Status:** ✅ mitigated — PR #68 (2026-08-11): per-turn confirmation UI. Shell
commands (builtin `run_command` + opencode's own permission prompts) now pause
for user Approve/Deny by default; a Settings › Chat toggle restores auto-run.
File tools stay auto-approved (workspace-confined). The prompt-injection →
silent-RCE path now needs an explicit human click.
`routers/chat.py:274-293` runs whatever shell string the builtin chat loop's LLM
emits via `create_subprocess_shell`; `opencode_engine` auto-approves every permission
request (`:26-28`). Cross-site abuse is blocked (JSON POST forces a preflight that
the empty CORS allowlist rejects), so the live risk is **prompt injection**: the
model ingests attacker-controlled content (a cloned README, fetched web text, a
workspace file) and is steered into `run_command "curl … | sh"`. Confined to the
user's own container, but the injection removes the "user intent" assumption. This
is TODO-PLAN item 1 and remains unmitigated.

### SEC-5 · LOW · Unescaped values in generated Codex `config.toml`
**Status:** ✅ fixed — PR #66 (2026-08-11): `_toml_escape` applied to every
dynamic value AND hardened to escape newlines (the real key-injection vector);
table keys sanitized via allowlist.
`studio-api/knowledge/codex_cli.py:68-77,210-260` interpolates `api_key`/`base_url`/
`name` into TOML with bare `f'... = "{value}"'` while a `_toml_escape` helper exists
and is used for agent descriptions (`:158`). Self-supplied, so low impact, but a
value with a `"` or newline can inject TOML keys. Apply `_toml_escape` consistently.

### SEC-6 · LOW · `whoami` trusts `X-Auth-User` by default
**Status:** ✅ fixed — PR #66 (2026-08-11): default flipped to off; prod is
unaffected (identity comes from server-injected `JUPYTERHUB_USER`).
`main.py:310-317` — `STUDIO_TRUST_AUTH_HEADER` defaults to `"1"`. Display-only (not
an auth boundary, per the code comments), but a spoofed header misrepresents identity
in UI/logs if the gateway doesn't strip client copies. Defaulting the flag off is safer.

**Verified sound:** `files.py:_safe_path` and `chat.py` workspace confinement
(realpath + prefix, symlink-resolving); all three WebSocket endpoints call
`reject_ws_if_cross_origin` before `accept()`; workflow/image/condor subprocesses use
`create_subprocess_exec` with arg lists; registry creds go via `--password-stdin`;
YAML is `safe_load` throughout, no pickle, no zip/tar extraction; `pegasus_bot`
SSRF-scoped to one env upstream with a 2-path allowlist; CORS correctly has no
wildcard-with-credentials (empty allowlist default), which is what makes the JSON
POST endpoints CSRF-resistant; no secrets found in logs.

---

## Frontend (studio-web)

**Prefix-handling sweep: CLEAN.** All URL construction goes through `lib/urls.ts`
(relative paths resolved against the `<base href>` that `app/layout.tsx` derives
from `x-forwarded-prefix`); `websocketUrl()` builds an absolute `ws:`/`wss:` URL
from `document.baseURI`. No hardcoded absolute app paths in any of the 47 files —
the historical #1 bug class is gone.

### FE-1 · HIGH · Chat stream swallows HTTP error status → "chat does nothing"
**Status:** ✅ fixed — PR #60 (2026-08-11): `response.ok` guard; JSON `detail`
or tag-stripped gateway page surfaced via `setLastError`.
`components/chat/chat-input.tsx:148-160` checks `!response.body` but never
`response.ok`. A 500 (`{"detail":…}`), a 502/504 HTML page from nginx/Envoy, or a
404 all have a body, so the SSE reader consumes non-`data:` lines, appends nothing,
and the `finally` clears the spinner. Given this deployment's history of stream
failures, this is exactly what turns a diagnosable gateway error into "chat is
silently broken". **Fix:** `if (!response.ok) { setLastError(await response.text()
|| \`HTTP ${response.status}\`, convId); return; }` before reading the body.

### FE-2 · HIGH · Chat store drops tool calls that arrive before the first text token
**Status:** ✅ fixed — PR #128 (2026-08-12): shared _mutateLastAssistant creates the assistant bubble for any first frame (tool_call/meta/message_id), not just text.
`lib/stores/chat-store.ts` — `appendToLast` creates the assistant bubble when the
tail is a user message, but `addToolCall`/`addToolResult`/`setLastMeta`/`setLastServerId`
do not. In the normal tool-first agent shape (a `tool_call` frame precedes any text),
the tool call is silently dropped from the UI, and an early `message_id` frame
disables feedback for that turn. The `messages` / `messagesById[activeId]` mirror is
also re-derived in six places with no type-level guard.

### FE-3 · HIGH · Chat history persisted unbounded to localStorage
**Status:** ✅ fixed — PR #128 (2026-08-12): persist version + partialize caps (50 convs, 200 msgs/conv) + quota-safe storage wrapper.
`lib/stores/chat-store.ts` persist writes the full `messagesById` (including tool-
result log blobs) with no cap/version/migrate. Around ~5 MB, `QuotaExceededError`
makes chat history silently stop surviving reloads.

### FE-4 · HIGH · DAG viewer stops polling right after Submit / on one transient error
**Status:** ✅ fixed — PR #60 (2026-08-11): ACTIVE-set + all-unsubmitted check
replaces TERMINAL; errors keep last good graph + reschedule; layout guarded.
`components/workflows/dag-viewer.tsx` — `TERMINAL` includes `"unsubmitted"` (line
50), so a freshly submitted run (all nodes unsubmitted) fails the "active" check and
polling stops after the first fetch → frozen DAG immediately after Submit.
Separately, the `.catch` at `:148-165` sets `error` without rescheduling `tick`, and
the render checks `error` before `flow` (`:219-223`), so one failed 4 s poll replaces
a working live DAG with "Could not render DAG" and stops polling until the modal is
reopened. `layout()` also dereferences `g.node(n.id)` unguarded with no error
boundary → white-screen on a bad node id.

### FE-5 · MEDIUM · Run-detail SSE never reconnects and stale stats mask fresh polls
**Status:** ✅ fixed — PR #128 (2026-08-12): reconnect with capped backoff; clear liveStats on error so the 5s poll shows through.
`components/workflows/workflow-detail.tsx:78-80` — `es.onerror = () => es.close()`
kills the live stream on the first transient error, and `:108-112` prefers
`liveStats?.X ?? run?.X`, so the frozen snapshot overrides the fresh 5 s poll data
until the run leaves `running`. Progress/counts freeze silently while the list view
shows correct numbers. **Fix:** reconnect with backoff, or clear `liveStats` on error.

### FE-6 · MEDIUM · Action mutations have no error surface
**Status:** ✅ fixed — PR #130 (2026-08-12): shared useActionToast wraps mutateAsync in onClick handlers (Generate/Plan/Run, Analyze, Delete, provider add/save/activate/delete, tool stop/uninstall, launch-tool) and toasts on failure; tool-card install/start/test already handled locally.
`mutateAsync` is awaited in onClick handlers with no try/catch and no `onError`
across: `workflow-project-detail.tsx:145-170,640-643` (Generate/Plan/Run),
`workflow-detail.tsx:88-100` (Analyze, Delete), `settings/page.tsx:148-172,204-215,
308-327` (provider add/save/activate/delete/test), `global-terminal-panel.tsx:40-44`,
`tool-card.tsx:124,141`. On a 500/timeout the button un-sticks with no output and no
message. `tool-card`'s install/start does it correctly — mirror that.

### FE-7 · MEDIUM · Settings → Pegasus Options "Save" is a placebo
**Status:** ✅ fixed — PR #128 (2026-08-12): removed the non-functional controls + fake Saved; kept the real (persisted) registry section; point users to the Plan/Run form.
`settings/page.tsx:811-815` — `handleSave` is `// TODO: persist via API` but shows a
green "Saved" for 3 s. Execution site / flags / Pegasus path are local state only,
reset on reload, never read by Plan/Run (which use the per-project param form). Wire
it up or remove the section — don't show "Saved".

### FE-8 · MEDIUM · TerminalView never initializes under React StrictMode (dev only)
**Status:** ✅ fixed — PR #128 (2026-08-12): reset the initialized ref in cleanup so a remount re-initializes.
`components/terminal/terminal-view.tsx:24-27,140-147` — the `initialized` ref is set
on first run but never reset in cleanup, so StrictMode's mount/unmount/remount leaves
the tab a dead black div and leaks an undisposed xterm. Dev-only (no
`reactStrictMode:false`), but it makes local terminal work painful. Reset the ref in
cleanup or drop the guard (cleanup already tears down fully).

### FE-9 · LOW
**Status:** ✅ mostly fixed — PR #128 (2026-08-12): notifications revert-on-fail + delta unread + manual SSE reconnect; api-key-in-query-key (already done in #64); nested interactive run-row elements; job-poll gating on terminal status; websocketUrl SSR guard; formatDuration NaN/60m; dead double-cast; URLSearchParams for submit. Deferred: notebooks window.open popup retry.
- Notifications optimistic writes never revert and errors are eaten
  (`lib/hooks/use-notifications.ts:22-37`); `unread` recomputed from a 100-capped
  list after `setAll` makes the badge jump (e.g. 137→~99); the EventSource comment
  wrongly assumes auto-reconnect covers a non-2xx (one gateway 502 kills live
  notifications until reload).
- Provider API key embedded in a TanStack query key (`lib/hooks/use-llm.ts:47`) —
  plaintext in the cache; key it on `provider_id` + a hash.
- Nested interactive elements in run rows — `<span onClick>`/`role="button"` inside
  a `<button>` (`workflow-project-detail.tsx:461-522`); invalid HTML, "DAG" is
  keyboard-unreachable, Delete has `tabIndex` but no key handler.
- `useWorkflowJobs`/`useWorkflows`/`useWorkflowProjects` poll every 5 s even for
  terminal runs (`lib/hooks/use-workflows.ts:22-29`) — gate on status.
- `websocketUrl()` SSR guard is broken (`lib/urls.ts:25-27` reads `window` after a
  `document`-only guard); dead double-cast `totalJobs` in `workflow-list.tsx:43-46`
  (the only `as unknown as` in the codebase); `formatDuration(3570)` → `"60m"`,
  `NaN` → `"NaNh"` (`lib/utils.ts`); notebooks `window.open` from a poll callback is
  popup-blocked and never retried (`notebooks/page.tsx:28-31`); unencoded
  `site`/`output_site` query params (`lib/api/client.ts:292`).

**Verified OK:** iframe chat page origin checks + handshake; terminal WS reconnect/
revalidate/cleanup (prod); the deliberate Zustand persist split; mid-stream
conversation pinning; query-key consistency and invalidation (incl. the deliberately
un-prefixed `["project-params", id]` key guarding the past submit-loop incident);
type safety (zero `any`/`@ts-ignore` except the one dead cast).

---

## Container / CI / Infra

### INF-1 · HIGH · CI pushes the production `:main` tag with zero validation
**Status:** ✅ fixed — PR #57 (2026-08-11): `test` job (pytest + tsc) gates
build/push; `pull_request` trigger builds without pushing.
`.github/workflows/deploy.yml:3-7,30-36` — the only workflow triggers on push to
`main` and immediately builds + pushes `ghcr.io/pegasus-isi/studio-webapp:main`. No
`pytest`, no `tsc --noEmit`, no PR-time build; a merge is the first time anything is
exercised, and KubeSpawner pulls `:main` with `imagePullPolicy: Always`. A broken
import ships to the next prod spawn. **Fix:** add a `test` job (pytest + tsc) as a
`needs:` gate before build/push, and a `pull_request` build-without-push trigger.

### INF-2 · HIGH · No root `.dockerignore` (or `.gitignore`)
**Status:** ✅ fixed — PR #57 (2026-08-11): both added; `*.md` deliberately not
excluded from the build context (`COPY knowledge/` needs it).
Absent — verified. The build context ships `.git`, host `node_modules` (darwin-arm64),
`.venv`, `.next`. Worse, `Dockerfile:15` `COPY studio-web/ ./` runs **after**
`npm ci`, merging the host's darwin-native `node_modules`/stale `.next` over the
Linux install; `COPY studio-api/` (`:283`) bakes `.venv`/`__pycache__`. CI's clean
checkout is the only reason prod is unaffected. **Fix:** add `.dockerignore`
(`.git`, `**/node_modules`, `**/.next`, `**/.venv`, `**/__pycache__`,
`**/.pytest_cache`, `.idea`, `.DS_Store`, `*.db`) and a root `.gitignore`.

### INF-3 · HIGH · First-launch `init_db.py` is unguarded against an NFS hang
**Status:** ✅ fixed — PR #57 (2026-08-11): `timeout 60` + loud failure; the
`.initialized` marker is only written on success, so the next spawn retries.
`docker/entrypoint.sh:24-36` runs `init_db.py` synchronously with no `timeout`,
unlike `propagate_llm.py` at `:56-57`, which was deliberately `timeout 20`'d +
backgrounded precisely because it touches the same NFS-backed SQLite DB and "a
blocked write would stall past JupyterHub's 30 s spawn timeout". A stale SQLite lock
(crashed prior pod) or NFS stall makes the first-launch `db.connect()` block forever;
supervisord never starts, the spawn is killed, the retry hits the same lock — a spawn
loop. **Fix:** `timeout 60 ${PYTHON} init_db.py` with an explicit error on
failure/timeout.

### INF-4 · MEDIUM · Supply-chain gaps in the Dockerfile (prior open item)
**Status:** ✅ fixed — PR #66 (2026-08-11): unsigned Pegasus dev repo dropped
(upstream has no InRelease/Release.gpg, so `signed-by` was impossible); the
pinned dev `.deb` is now sha256-verified and installed directly with deps from
signed repos; get-pip.py sha256-pinned; NodeSource via keyring'd apt repo.
- `Dockerfile:132-135` dearmors the Pegasus GPG key into
  `/usr/share/keyrings/pegasus.gpg` then writes the apt source with `[trusted=yes]`
  instead of `[signed-by=…]` — signature checking off for a root-installed package.
  The key it needs is already fetched two lines up. Same for the pinned dev `.deb`
  (`:143-145`, `dpkg -i` with no `sha256sum -c`).
- `curl | python3` / `curl | bash` of unpinned remote bootstrap scripts as root:
  get-pip.py (`:76`) and NodeSource `setup_20.x` (`:201`). Install nodejs from a
  keyring'd repo (like HTCondor at `:127-131`) and pin get-pip by commit + sha256.

### INF-5 · MEDIUM · Unpinned dependency floors ship latest into prod
All `studio-api/requirements.txt` are `>=`; `Dockerfile:88-96` has `jupyterlab>=4.2`
and unpinned numpy/pandas; the chat labextension resolves fresh npm deps every build
(`:248` literally `touch yarn.lock`, and `jupyter-pegasusai-chat/.gitignore` ignores
it). A JupyterLab 5.x / pydantic 3 release breaks a prod rebuild on an unrelated
merge, with no test tripwire (INF-1). Pin at least jupyterlab/fastapi/pydantic/uvicorn
and commit the extension lockfile.

### INF-6 · MEDIUM · A FATAL supervised program leaves a "healthy" half-dead pod
`docker/supervisor/supervisord.conf:19-63` uses default `startretries=3`/`startsecs=1`;
after 3 fast failures a program goes FATAL permanently while supervisord (PID 1)
keeps running, so the container stays "up" and there's no Docker HEALTHCHECK
(`Dockerfile:315` is EXPOSE-only). NFS-slow boot → jupyter goes FATAL → `/jupyter/`
502s forever though the spawn "succeeded"; same for studio-api on a bad migration.
Add `startsecs=5`/`startretries=10` (tolerate NFS warm-up) plus a FATAL
eventlistener that SIGTERMs PID 1 so Kubernetes restarts the pod.

### INF-7 · MEDIUM · nginx caps API SSE at 300 s read-idle
`docker/nginx/default.conf:39,118` — `proxy_read_timeout 300s` on both `/api`
locations. A chat prompt whose agent/tool work produces no SSE bytes for >5 min (no
heartbeat frames) gets its upstream read closed by nginx; the browser sees the stream
end mid-response even though the engine finishes. This sits one layer below the Envoy
timeout already tuned to `0s` for SSE. Raise it on the SSE-serving `/api` locations
(match the `/ws/` `3600s`) or emit `: ping` more often than 300 s.

### INF-8 · MEDIUM · Other build/CI items
- `Dockerfile:13` `npm ci --ignore-scripts 2>/dev/null || npm install` hides lockfile
  drift and silently falls back to a non-reproducible `npm install`. Use `RUN npm ci`.
- `.github/workflows/deploy.yml:13-16` has no `concurrency:` group — two quick pushes
  race and last-to-finish wins `:main` regardless of commit order (a revert can be
  overwritten by the slower earlier build). Add `concurrency: {group: deploy-main,
  cancel-in-progress: true}`, an explicit `platforms: linux/amd64`, and gha cache.
- `/opt/npm-global` is `chmod 777` / `a+rwX` and first on PATH (`Dockerfile:209,224,211`)
  — the baked `opencode` binary is world-writable; any pod code execution can shim it.
  Keep on-demand installs in `$HOME/.npm-global` (already configured) and make
  `/opt/npm-global` read-only, or move it to the end of PATH.
- Image bloat: labextension `node_modules`/yarn caches and `build-essential` (~250 MB)
  ship in the runtime image (`Dockerfile:247-255,271-277,54`) — pull-always pays this
  per cold spawn. Build extensions in a dedicated stage and COPY only the artifacts.

### INF-9 · LOW
- Missing forwarded headers on WS + prefixed-API nginx locations
  (`default.conf:42-52,122-131`) — backend sees 127.0.0.1 as client.
- nginx site config relies on the Debian `sites-enabled → sites-available` symlink
  the Dockerfile never creates (`Dockerfile:292`, `nginx.conf:37`) — a packaging
  change silently yields no server on :8888. Use `conf.d/` or `ln -sf` explicitly.
- Dockerfile comments cite a nonexistent Makefile (`:115,157`); the real runner
  `local-test-instance.sh` omits `--privileged`, so a local SINGULARITY workflow hits
  an opaque `unprivileged_userns_clone` failure. `local-test-instance.sh` also dies
  on unset `$USER` (`set -u`) and wipes the temp home on exit.
- `entrypoint.sh` seeds `AGENTS.md` only on first launch (marker on the PVC), so image
  knowledge-base updates never reach existing homes; the backgrounded `propagate_llm`
  subshell reparents onto supervisord (`reaped unknown pid` noise); studio-api lacks
  `stopasgroup`, orphaning PTY children holding NFS handles on restart.

**Verified OK:** nginx WS/SSE plumbing (upgrade map, buffering off, correct
prefixed twins, non-false-matching regex); the sub_filter set matches `assetPrefix:"."`;
supervisord logs can't fill disk (`*_logfile_maxbytes=0` to stdout); entrypoint
idempotency otherwise good; the vendoring boundary for `jupyter-pegasus-ai-client` is
respected (only the 3 declared files modified); knowledge paths line up end-to-end;
no secrets in image layers (the AMQP cred is the public Pegasus telemetry one); the
amd64 hard-fail guard; the deliberate jupyter-ai/RTC exclusions.

---

## Tests & documentation

### Test coverage

Backend suite is **genuinely good** — real behavior, not tautologies: condor
summarization math and binary-missing paths, the opencode engine driven through a
scripted `httpx.MockTransport` SSE server (cross-session-leak, DB-down), NFS-busy
retry/partial-delete, image-build failure/no-builder paths, real SQLite in `tmp_path`.
Mocks sit at the right boundary. Gaps worth closing:

- **HIGH · `routers/files.py` `_safe_path` has zero tests** — the only containment
  check protecting read/write/delete/mkdir on the user FS. Symlink, `~`, and
  prefix-sibling (`/home/u/work-evil`) cases regress silently. (Hard to test today
  because `WORKSPACE_ROOT` is frozen at import — worth fixing together.)
- **HIGH · `routers/terminal.py`** `_contained_cwd` (a second containment check),
  `_llm_env` (injects LLM creds), `_tool_command`, and the REST session endpoints
  (testable via TestClient) — untested.
- **HIGH · `services/installer.py`** — only `_install_error` is touched; the real
  registry parsing + npm/pip argv construction + status machine (which shells installs
  into `$HOME`) is replaced by a `FakeInstaller` in tests.
- **HIGH · `chat.py run_command` dispatch** — zero test references; this is the open
  security item (SEC-4) with no coverage.
- **MEDIUM** · `terminal_sessions.py` (PTY lifecycle + idle reaping),
  `process_mgr.py`, and the `workflow_monitor.py` event-tailing path (drives all UI
  run status) — untested. **LOW** · `generator_introspect` async wrapper,
  `routers/jupyter.py`, `routers/ai_terminal.py` bridge helpers.

**Frontend: zero tests** (vitest is a devDependency but there's no config, no test
files, no `@/*` alias for vitest). Highest-risk untested logic: `lib/urls.ts`
(`normalizeBasePath` has no shape validation → a passed-through `X-Forwarded-Prefix`
could set `<base href>` to an attacker origin; the load-bearing trailing-slash
invariant; the broken `websocketUrl` SSR guard), the chat-store reducers (FE-2/FE-3),
and `dag-viewer` `layout()`/`TERMINAL` (FE-4).

### Test-quality issues

- **HIGH** · `conftest.py:19-50` imports `app` once per session, so every import-time
  constant (`files.py:WORKSPACE_ROOT`, `scanner.SCAN_ROOT`, chat constants) is frozen
  to the first test's `HOME`; tests hand-patch `SCAN_ROOT` to compensate (fragile;
  the reason `files.py` is hard to test).
- **MEDIUM** · Cross-loop hazard: `asyncio.run(db.upsert_workflow_run(...))` on
  `main.db` (test_workflows.py:208,232) while that aiosqlite connection lives on the
  still-alive TestClient portal loop — a flake waiting to happen; done correctly
  elsewhere. Process-global `condor_pool._cache` reset by hand per test → order
  dependence with no autouse reset.
- **LOW** · `tempfile.mkdtemp()` for HOME/KNOWLEDGE_ROOT leaks a dir pair per test;
  the lifespan starts real background loops during each test; no `pytest-asyncio`.

### Documentation inaccuracies

- **Meta · HIGH** · `docs/TODO-PLAN.md` and `docs/CODE_REVIEW_2026-07.md` are
  **untracked in git** — the roadmap and prior review exist only on this machine.
  Commit them.
- **`studio-web/README.md` (confirmed stale)** — documents `/dashboard` (doesn't
  exist; dashboard is `/`), `/workbench` (removed in `940ce9a`), misdescribes
  `/chat` and `/settings`, omits every real route (`/`, `/workflows`, `/marketplace`,
  `/provisioning`→"Resources", `/notebooks`), and never says `npm test`/`npm run lint`
  don't work. Rewrite outright.
- **`studio-api/README.md` (HIGH errors)** — says nginx port 80 (it's 8888) and
  JupyterLab is on-demand (always-on under supervisord); says the entrypoint runs
  init_db → seed_config → uvicorn (actually init_db-once → **supervisord**, no
  seed_config); documents `POST /api/jupyter/start|stop` as functional (they're
  no-ops); documents `DELETE /api/workflows/{id}` as "cancel/pegasus-remove" when it
  now **deletes the submit directory from disk**.
- **`README.md` (root)** — the `LLM_PROVIDER`/`ANTHROPIC_API_KEY`/… env block is
  consumed only by `seed_config.py`, which the entrypoint no longer runs (setting them
  does nothing); provider list omits `gateway` (the zero-config default) and `google`;
  nav still called "Examples"/"Provisioning" (now "Marketplace"/"Resources"); lists
  PegasusAI-Chat as an installable tool (explicitly excluded); ~18 read env vars
  undocumented (`STUDIO_ALLOWED_ORIGINS`, `STUDIO_TRUST_AUTH_HEADER`,
  `STUDIO_LLM_GATEWAY_URL`, …).
- **`SPEC.md`** — documents `/examples` route + `GET /api/examples` (actual:
  `/marketplace`, live-fetched from GitHub); omits `notifications`/`marketplace`/
  `pegasus_bot` routers, the destructive `DELETE /api/workflows/{run_id}`, and 4 of 8
  DB tables.
- **`docs/CODE_REVIEW_2026-07.md`** — nearly every line citation has drifted (~40
  commits on); the "written but NOT yet run" caveat is obsolete; S7 (plaintext keys)
  and S10 (`[trusted=yes]`) accurately still open.
- **`docs/TODO-PLAN.md`** — "PRs #1–#13 merged" (now #55); the "Absent today" list is
  all shipped (dashboard, Resources page, pool monitoring, runtime estimate); and it
  **overclaims** "implemented 2026-07-13 (items 8–12)": item 9's SSE for provisioning
  doesn't exist (only `GET /api/provisioning/status`), and item 8's guided tour has no
  code (`grep tour` → nothing).

---

## Recommended priority order

1. **BE-1** (event-loop blocking) — biggest backend reliability win; the correct
   `run_in_executor` pattern already exists in `dashboard.py`.
2. **INF-1 + INF-2 + INF-3** — cheap, high-leverage deploy safety: a CI test gate, a
   `.dockerignore`, and `timeout 60 init_db.py`.
3. **FE-1** (one-line `response.ok` guard) + **FE-4** (post-submit DAG freeze) +
   **BE-6** — directly attack the recurring "chat/DAG silently broken" class.
4. **BE-2** (kill subprocesses on timeout) — prevents duplicate DAG submission.
5. **SEC-1** (git-clone scheme allowlist) + **SEC-3** (stop echoing API keys) — the
   two reachable-beyond-self security items.
6. **Docs** — commit the two untracked docs; rewrite `studio-web/README.md`; fix the
   four HIGH errors in `studio-api/README.md`.
7. Everything else as capacity allows; the prior-open items (SEC-4 `run_command`,
   plaintext keys at rest, `[trusted=yes]`) still need the product decisions the July
   review flagged.
