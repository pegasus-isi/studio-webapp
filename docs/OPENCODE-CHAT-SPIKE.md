# Spike: chat as a client of `opencode serve`

Findings from the spike that #367 names as blocking everything else. Run against
`opencode-ai` **1.18.29** (and 1.18.8, the version the image pinned) with a real
OpenAI-compatible gateway behind it, driving the HTTP API by hand.

Read this before touching `studio-api/routers/opencode.py` or the part
renderers: several of the answers here are not in OpenCode's docs, and two of
them contradict what the design issue assumed.

## Version

`1.18.8` → `1.18.29` adds and removes **no** API paths (both `/doc` OpenAPI
documents list an identical path set), so the bump is safe and the image now
pins 1.18.29. `@opencode-ai/sdk` must stay on the same version as the server:
the client is generated from that OpenAPI document, and CI enforces the match.

## Which API surface the client uses: v1, not v2

The server exposes two generations at once.

- **v1** (`/session`, `/event`, `/permission/…`) is *state*-shaped. Every
  `message.part.updated` event carries a whole `Part` object with an id and, for
  a tool, its full `state` (`pending` → `running` → `completed`). A message read
  back from `/session/{id}/message` has the same parts, so a reconnect and a
  live stream produce identical objects.
- **v2** (`/api/session`, `/api/event`) is *delta*-shaped: `session.next.text.delta`,
  `session.next.tool.input.delta`, and so on, with messages stored as a
  `content` array rather than parts.

Studio uses v1. The renderer registry in #367 is keyed by part type, and only v1
hands the browser those parts whole, which is what makes "an unknown part type
still renders, collapsed" achievable without the backend understanding any of
them. It is also the surface the SDK's default (non-`/v2`) client covers.

Part types observed in a real transcript: `text`, `tool`, `step-start`,
`step-finish`, plus `reasoning`, `file`, `patch`, `agent`, `retry`, `snapshot`,
`compaction`, `subtask` in the type union.

## Authentication and health

Basic auth, username `opencode`, password `$OPENCODE_SERVER_PASSWORD`. Without
it every route answers 401 — including `/api/health`, so a health check has to
send credentials. `/global/health` returns the version too:

```
$ curl -su opencode:$PW http://127.0.0.1:4096/global/health
{"healthy":true,"version":"1.18.29"}
```

## The two open questions in #367

**Does `experimental.chat.system.transform` receive the session id?** Yes.

```
system.transform input={"sessionID":"ses_f7eb…","model":"gpt-5.4-mini"} n=1
```

So the preferred design works: Studio stores the scope per session id and the
plugin fetches the Active Context block for that session, keeping it out of the
transcript. It fires once per step, not once per turn, so the fetch wants to be
cheap or cached.

**A synthetic prompt part also works**, and is the fallback if the plugin ever
stops loading. `POST /session/{id}/prompt_async` accepts
`parts: [{type:"text",text:"…"},{type:"text",text:"## Active Context…",synthetic:true}]`
and the stored part keeps `synthetic: true`, so the renderer can hide it while
the model still sees it.

## Plugin loading, and how it fails

Registration is the `plugin` array in `opencode.json`, holding a `file://` URL
with an absolute path. Two things cost time here:

- **Every exported function is treated as a plugin factory**, not just the one
  named `server`. A module exporting `server` and `Legacy` had both called.
- **A plugin that throws while being imported fails silently.** Nothing is
  logged, at any log level, and the server starts normally with the hook simply
  absent. The first attempt here imported a package that was not installed and
  read an unset environment variable; the only symptom was that the marker never
  appeared in the system prompt. Anything Studio generates must therefore be
  self-contained: no bare imports, and no assumption that an env var is set.

`.opencode/plugins/` is *not* an auto-load directory (the singular
`.opencode/plugin/` is). Declaring the path in the config is unambiguous and is
what the knowledge adapter writes.

## Config changes need a restart, not a dispose

Editing `opencode.json` under a running server has no effect, and neither
`POST /global/dispose` nor `POST /instance/dispose` reloads it — `/config` kept
reporting the old model after both. The propagator therefore restarts the
supervised process after it rewrites the config. This is the same failure the
old adapter showed as "Model not found": the config on disk and the config the
server is running are different things.

## Permissions

Setting `permission: {"bash": "ask"}` in the config makes the server pause and
emit `permission.asked`, with everything a dialog needs:

```json
{"id":"per_0814…","sessionID":"ses_f7eb…","permission":"bash",
 "patterns":["echo spike-ok"],"metadata":{"command":"echo spike-ok"},
 "always":["echo *"],"tool":{"messageID":"msg_0814…","callID":"call_o4hv…"}}
```

`POST /permission/{id}/reply` with `{"reply":"once"|"always"|"reject"}` resumes
the turn. `always` is the pattern list the reply would persist, so the dialog can
offer "allow `echo *` from now on" without Studio inventing the pattern.
Questions are the same shape: `POST /question/{id}/reply` with
`{"answers":[["label"]]}`, one array of chosen labels per question.

## Streaming through the proxy

The event stream sets `x-accel-buffering: no` and sends a heartbeat comment, so
it survives nginx as configured (`proxy_buffering off`, `proxy_read_timeout
3600s` on `/api/`). Both a global `/event` stream and a per-session
`/api/session/{id}/event` stream exist; the global one is what a v1 client
subscribes to, filtering by `sessionID`.

## Sessions and directories

A session records the `directory` it was created in. `GET /session` with no
`directory` query parameter lists every session the server knows, so "chats for
this workflow" is a filter, not a separate store. `GET /path` reports the
server's own default directory, which is where a session lands when the caller
names none.

## What the SDK's URL handling means for the prefix

`getUrl` in the generated client is plain string concatenation
(`baseUrl + path`), not `new URL(path, baseUrl)`, and the SSE helper builds a
`Request` from that string. A **relative** `baseUrl` therefore works in the
browser: it resolves against `<base href>`, so the KubeSpawner prefix is handled
by the same mechanism as every other Studio URL, and `lib/urls.ts` stays the one
place that knows a prefix exists.
