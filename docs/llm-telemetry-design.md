# LLM Interaction Telemetry

Status: **built, not yet observed in production** (2026-09-09). The pod side and
the cluster side both exist, in this repo and in `studio-system`, and nothing in
them has yet processed a real turn in a live pod. Every claim below that says
"verified" names how; everything else is unverified by design and listed in
§8.

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

* `k8s/infrastructure/controllers/clickhouse-operator/` — the
  `clickhouse.com/v1alpha1` CRDs, in the tier that Flux waits on.
* `k8s/apps/monitoring/otel-collector.yaml` + `otel-collector-secret.yaml` —
  the collector, its ServiceMonitor, and the Langfuse project keys.
* `k8s/apps/langfuse/` — the trace store.
* `k8s/apps/jupyterhub/helmrelease.yaml` — `STUDIO_OTLP_ENDPOINT`.

Langfuse is reached by port-forward, not by a hostname. A public URL would
mean another Gateway listener, a certificate, a hand-managed CNAME and then a
login page in front of a store of other people's prompts; for a handful of
maintainers a port-forward is the smaller surface.

## 8. Not done, in the order it matters

1. **Nothing has been observed end to end.** The collector config renders
   (`helm template`, chart 0.172.1) and the OTTL functions and the plugin's
   own option parsing were both read rather than assumed, but no metric has
   made it to Prometheus and no span to Langfuse. First run should check, in
   order: the plugin loaded at all (it fails silently on an import error), the
   collector received anything, `opencode_*` series appear in Prometheus with
   an `enduser_id` label, and a redacted span arrives with its content gone
   and its token counts intact.
2. **The Langfuse project keys are placeholders.** They can only be created in
   the Langfuse UI, which needs the release running. Until they are replaced,
   trace export fails auth.
3. **Retention and RBAC are unset.** Langfuse has per-project retention; for a
   store of prompt text it should be set before the first opted-in user, not
   after.
4. **No dashboard.** The metrics are useless until someone can see them.
5. **Cost per §5.**
6. **The gateway plane per §6.**
