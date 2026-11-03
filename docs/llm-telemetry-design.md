# LLM Interaction Telemetry

Status: **built and running in production** (re-checked 2026-11-02). Until
2026-10-07 the collector presented placeholder Langfuse keys and every trace
batch was rejected; studio-system then sealed real keys (`f4f5196`) and added a
"Studio telemetry" Grafana dashboard and alerts built against the live
Prometheus series (`843d9d3`). Reply ratings were added on 2026-10-21 (§4a,
#609, with a dashboard row in studio-system). The text below was written on
2026-09-09, before any of that; §8 marks which of its open items have since
closed. Every claim below that says "verified" names how.

This replaces an earlier proposal that described a different platform. Two of
its central assumptions were wrong by the time anyone read it: it routed
everything through **LiteLLM**, and it deployed onto **GKE** with Google
Managed Prometheus. Neither exists. The gateway is `studio-llm-gateway`, our
own service with tiered virtual models, and the cluster is a self-managed one
on Jetstream2 running kube-prometheus-stack. The design below is what was
actually built against those.

## 1. The constraint that shapes everything

You can only observe LLM traffic that passes through something you control: a
gateway, or a client you have instrumented. A client talking straight to a
provider with the user's own key is invisible, and no amount of backend buys
that back.

Studio's chat is now a client of `opencode serve` running in the user's own pod
(#367). That is the thing we control, so that is where the instrumentation
lives — at the point of the call, which means it captures a user's own
provider key just as well as it captures the shared gateway.

## 2. What is collected, and the line through the middle

The engine runs `@devtheops/opencode-plugin-otel`, pinned to an exact version
in `studio-api/knowledge/opencode.py` and configured through the generated
`opencode.json` rather than through environment variables, so a per-user
setting can change it without a pod restart.

Two kinds of data come out, and they are governed differently.

**Metrics — always on where a collector exists.** Token counts by type
(input, output, reasoning, cache read, cache create), tool call durations,
session and message counts, model and provider ids, retries, session duration.
These are counters about activity. None of them contains anything the user
typed.

**Traces — off unless the user opts in.** Session, LLM and tool spans. These
carry the prompt itself: the plugin puts it in the span's `input.value`
whatever its prompt-in-logs flag says, and on a tool span `input.value` is the
tool's arguments, which for a shell tool is the command. There is no
"traces without content" mode to hide behind, so traces *are* the content
decision.

Log events are disabled outright. The plugin emits a useful set
(`user_prompt`, `api_request`, `api_error`, `tool_result`, `tool_decision`),
but this cluster has no log backend — `monitoring` is kube-prometheus-stack
and nothing else — so enabling them would be egress to a drop. If Loki ever
lands, this is the first thing to turn back on, and it is one flag in
`_telemetry_plugin`.

## 3. Consent

`services/telemetry.py` owns a single settings row and `GET`/`PUT
/api/settings/telemetry` expose it. Settings → Chat (and Feedback & privacy)
has the control, worded around the distinction in §2 rather than around the
word "telemetry". The guided tour also asks it directly (#430): two
equal-weight buttons, nothing preselected, and leaving the tour writes
nothing.

Because "off" is also the default, the choice itself is recorded separately
(`telemetry_content_capture_decided_at`, returned read-only as `decided`), so
the per-login tour keeps asking until the user has chosen either way (#454).
An existing opt-in counts as decided; a failed lookup as undecided.

Three properties, each deliberate:

* **Its own row, not a `UIPreferences` field.** `PUT /api/settings/ui`
  replaces preferences wholesale, so a consent flag there could be reverted by
  an unrelated toggle saved from a stale cache. `GET /api/settings/ui` also
  drops individual values that fail validation and substitutes defaults —
  right for "was the sidebar open", wrong where the default must be reached by
  a path that cannot be confused with a successful read.
* **Every failure resolves to "no".** A lookup that raises means we do not
  know whether the user consented, and not knowing must not send prompts.
* **Saving propagates.** The engine reads plugin options once at startup and
  no endpoint reloads them, so storing the flag without rewriting
  `opencode.json` and restarting would leave Settings claiming one thing while
  the running engine did another. The propagator schedules the restart; the
  request does not wait for it.

The consent state also travels *with the data*, as the resource attribute
`studio.content_capture`. The collector strips content from any resource that
does not say `true`, so a pod still running a config written before consent
was withdrawn — a restart that failed, say — cannot leak a prompt. Redaction
keeps the span, its timing, its token counts and its model, so a
non-consenting user still appears in aggregate without a word of theirs being
stored.

Because traces are what Langfuse ingests, one consequence is worth stating
plainly: **Langfuse holds data only for users who opted in.** Everyone else is
visible in Prometheus and nowhere else.

## 4. Where it goes

```
 user pod (per user)
   opencode serve
     └── otel plugin ──OTLP/http──┐
                                   │   no credential in the pod; the collector
                                   │   is the trust boundary
                                   ▼
        otel-collector (monitoring ns)
          ├── metrics ──► prometheus exporter :8889 ──► kube-prometheus-stack
          │                                             (ServiceMonitor) ──► Grafana
          └── traces ───► transform/redact_content ──► Langfuse (langfuse ns)
                          (drops input.value, output.value,
                           llm.{input,output}_messages unless
                           studio.content_capture == "true")
```

Attribution is by pod, because the pod is per user: `enduser.id` comes from
`JUPYTERHUB_USER`, which KubeSpawner injects and which `main.py` already
trusts for the account menu. The value is sanitised before it goes into the
attribute string — that string is a flat `k=v,k=v` list, so a username
containing a comma and an equals sign would not corrupt its own attribute, it
would redefine the ones after it.

`STUDIO_OTLP_ENDPOINT` is the on switch. Unset, the adapter leaves the plugin
out of the generated config entirely, so the FABRIC VM and plain `docker run`
containers neither fetch the plugin from npm nor retry an endpoint that is
never coming up. It is set in one place: `singleuser.extraEnv` in
studio-system's jupyterhub HelmRelease.

## 4a. Reply ratings

A 👍/👎 on a reply is stored in the user's own `studio.db`
(`message_feedback`, no text). `services/rating_telemetry.py` sends it to the
same collector, the same two ways the engine's data goes:

* **Gauge `studio.chat.reply_ratings`** (Prometheus
  `studio_chat_reply_ratings{rating, model, agent, enduser_id}`), always when
  a collector exists. The current count of rated replies, read from the DB
  and re-sent every minute by studio-api: the collector's Prometheus exporter
  forgets a series after five minutes of silence, and the DB, unlike an
  in-process counter, survives a restart. A gauge because a changed or
  cleared vote lowers it. Query it with `max_over_time(...[$__range])` so a
  stopped server's last count still counts.
* **A span `chat.reply_rating` per vote**, only for users who opted in to
  sharing chat text. Langfuse cannot create a score over OTLP, and its score
  API needs the project keys, which are never in a pod. It does map
  `session.id`, which the engine's spans set to OpenCode's session id, so the
  rating span lands in the same Langfuse session as the turns it rates, with
  `studio.rating` (1/-1) and `studio.message_id` as attributes. It holds no
  text, but follows the traces' consent rule, so Langfuse still holds data
  only for users who opted in.

A real Langfuse *score* (filterable in its score views) would need a small
relay holding the project keys, cluster-side; not built.

## 5. Cost

The plugin exports `opencode.cost.usage`, and for us it will be close to
meaningless. Cost is derived from models.dev pricing, and the model this
gateway serves is called `default`. Token counters are real and per model, so
cost has to be computed downstream from tokens and a price list we maintain,
or not claimed at all. Do not build a spend dashboard on that metric without
checking what it actually reports first.

## 6. The gateway plane, and the blind spot

`studio-llm-gateway` sees every request routed through it and currently
reports none of it. Instrumenting it would cover the tools Studio does not
own — a terminal `opencode`, `codex`, a notebook calling the endpoint
directly — with no per-client work. That is a change in a third repo and is
not part of this.

Until then: anything not going through Studio's chat engine is unobserved. A
user who points a tool at their own provider key is unobserved by design
(recorded as an accepted decision in the earlier proposal, and still the right
one).

## 7. Deployment

studio-webapp:

* `studio-api/knowledge/opencode.py` — `_telemetry_plugin`, the plugin entry
  and its options.
* `studio-api/services/telemetry.py` — the consent row.
* `studio-api/routers/settings.py` — `/api/settings/telemetry`.
* `studio-api/llm/propagator.py` — carries consent into the generated config.
* `studio-web/app/(studio)/settings/page.tsx` — the control.

studio-system:

* `k8s/apps/clickhouse-operator/` — the `clickhouse.com/v1alpha1` CRDs.
* `k8s/apps/monitoring/otel-collector.yaml` + `otel-collector-secret.yaml` —
  the collector, its ServiceMonitor, and the Langfuse project keys.
* `k8s/apps/langfuse/` — the trace store.
* `k8s/apps/jupyterhub/helmrelease.yaml` — `STUDIO_OTLP_ENDPOINT`.

Langfuse is reached by port-forward, not by a hostname. A public URL would
mean another Gateway listener, a certificate, a hand-managed CNAME and then a
login page in front of a store of other people's prompts; for a handful of
maintainers a port-forward is the smaller surface.

## 8. Not done, in the order it matters

1. **Nothing has been observed end to end.** *(2026-11-02: closed. The
   dashboard's expressions were evaluated against live Prometheus series, and
   traces export to Langfuse since the keys were sealed on 2026-10-07.)* The collector config renders
   (`helm template`, chart 0.172.1) and the OTTL functions and the plugin's
   own option parsing were both read rather than assumed, but no metric has
   made it to Prometheus and no span to Langfuse. First run should check, in
   order: the plugin loaded at all (it fails silently on an import error), the
   collector received anything, `opencode_*` series appear in Prometheus with
   an `enduser_id` label, and a redacted span arrives with its content gone
   and its token counts intact.
2. **The Langfuse project keys are placeholders.** *(2026-11-02: closed,
   sealed in studio-system on 2026-10-07.)* They can only be created in
   the Langfuse UI, which needs the release running. Until they are replaced,
   trace export fails auth.
3. **Retention and RBAC are unset.** Langfuse has per-project retention; for a
   store of prompt text it should be set before the first opted-in user, not
   after.
4. **No dashboard.** The metrics are useless until someone can see them.
   *(2026-11-02: closed, the "Studio telemetry" Grafana dashboard.)*
5. **Cost per §5.**
6. **The gateway plane per §6.**
