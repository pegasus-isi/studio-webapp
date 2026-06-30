# LLM Interaction Telemetry — Design

Status: **Draft for review**
Scope: studio-webapp (PegasusAI chat) + LiteLLM gateway + GKE platform
Goal: collect metrics **and prompt/response content** for LLM interactions
across all clients (PegasusAI chat, opencode, other tools), with per-user cost
attribution.

---

## 1. Guiding constraint

> You can only observe LLM traffic that passes through something you control —
> a **gateway** or a **client you have instrumented**. A client that talks
> directly to a provider with the user's own key/endpoint is invisible.

This is not a tooling gap; it is physics. The design therefore uses **two
capture planes** and is explicit about the resulting blind spot.

## 2. Decisions (recorded)

| # | Decision | Consequence |
|---|----------|-------------|
| D1 | **Direct BYO connections stay first-class** (not forced through LiteLLM) | The gateway is *bonus* coverage, not the source of truth. Owned clients must be instrumented directly. A BYO-direct client (e.g. opencode → user's own provider) is an accepted blind spot. |
| D2 | **Larger / cost-focused** scale | Deploy the scalable Langfuse stack (ClickHouse + Redis + blob storage). Enable LiteLLM cost tracking + budgets. Per-user attribution is mandatory. |
| D3 | Design first; **verify chart/integration specifics at build** | Versions and exact config in §11 are to be confirmed against live docs before manifests are written. |
| D4 | **Tailor UX from interactions** via an app-local personalization layer | Observability backends (Langfuse/MLflow) are *not* the serving store. A per-user profile in the app DB drives the UI; Langfuse = observability + feedback source; MLflow = offline eval of whether tailoring helps. See §12. |

## 3. Capture planes

1. **Owned-client plane — authoritative.** Every client whose code we control
   emits a telemetry trace per turn, *regardless of which provider it hits*.
   Today that is **PegasusAI chat** (`studio-api/routers/chat.py`). This plane
   captures BYO providers too, because we instrument at the point of the call,
   not at the network egress.
2. **Gateway plane — opportunistic.** **LiteLLM** logs everything routed
   through it (prompt, response, tokens, cost, latency, virtual-key identity)
   via its native Langfuse callback. Covers any client pointed at the proxy
   with zero per-client code.

Both planes write to the **same backend (Langfuse)** so traces are unified.

## 4. Architecture

```
 PegasusAI chat ──(instrumented: Langfuse SDK/OTLP, every turn, any provider)──┐
                                                                                │
 opencode ───────┐                                                              ▼
 other clients ──┤── base_url → ┌──────────────┐  Langfuse callback   ┌──────────────────┐
                 │   (optional) │   LiteLLM     │ ───────────────────▶ │     Langfuse     │
 BYO via proxy ──┘              │   (gateway)   │                      │   (self-hosted,  │
                                └──────┬────────┘                      │      GKE)        │
 BYO direct ─ ─ ─ ─ ─ ─ ✗ INVISIBLE   │ /metrics                       │ traces, prompt/  │
 (user key → provider)                ▼                                │ response, cost,  │
                          Google Managed Prometheus ── Grafana         │ scores/feedback  │
                          (numeric metrics, alerts)                    └──────────────────┘
                                                                                ▲
 PegasusAI chat ── thumbs up/down, agent, slash-cmd, studio user ───────────────┘
```

- **Langfuse** = system of record for *what was said* (content), token/cost,
  traces, and quality scores (feedback).
- **Google Managed Prometheus** (already running in-cluster: `gmp-system` /
  `gmp-public`) = numeric metrics scraped from LiteLLM `/metrics` for cheap
  dashboards/alerts without querying Langfuse.

## 5. Per-client coverage

| Client | Path to LLM | Capture mechanism | Coverage |
|--------|-------------|-------------------|----------|
| PegasusAI chat | any provider via `chat.py` | direct instrumentation (+ gateway if proxied) | **Full** incl. BYO |
| opencode / OpenAI-compatible tools | their `base_url` | gateway *iff* base_url → LiteLLM | Full if proxied; **none if direct** |
| BYO registered in LiteLLM | proxy pass-through | gateway | Full |
| **BYO direct** (own key → provider) | direct | — | **Blind spot (accepted per D1)** |

**Blind-spot mitigations (no enforcement, per D1):**
- Ship a default `opencode` / tool config whose `base_url` points at LiteLLM
  with a per-user virtual key; users *may* override (and lose visibility).
- Surface a per-user "observed vs. unobserved" indicator in the studio so users
  know when their traffic is being captured.
- Document clearly that direct connections are not measured (cost or content).

## 6. Trace data model

One **trace per chat turn**; nested **spans/generations** for each model call
and tool call. Fields:

- **Identity/attribution:** `user_id` (studio username — already derived in
  `pegasus_studio_pre_spawn_hook`), `session_id` (conversation id from
  chat-store), `client` (`pegasus-chat` | `opencode` | …).
- **Request:** provider, model, base_url host, `agent_id`, slash-command (if
  any), system-prompt hash, message count.
- **Content:** prompt (messages) and response text — subject to redaction (§9).
- **Usage/cost:** input/output tokens, computed cost (LiteLLM cost map or
  Langfuse model pricing — works even for BYO since we have token counts),
  latency, tool-round count.
- **Tools:** per tool call — name, duration, ok/err, result size.
- **Outcome:** `status` ∈ {ok, error, stopped}, error type/status code.
- **Quality:** thumbs up/down score, optional free-text.

## 7. PegasusAI chat instrumentation (studio-api)

`chat.py::chat_stream` already aggregates per-turn `meta` (tokens, tool calls,
duration) — we extend, not rebuild:

1. Wrap each turn's `event_generator` in a Langfuse **trace** (created at
   request start with attribution from §6).
2. Each provider call (`_anthropic_request` / `_openai_dispatch`) → a
   **generation** with model, usage, latency, prompt/response.
3. Each `_execute_tool` → a **span** with name, duration, ok/err, result size.
4. Finalize the trace in the `finally` block so **stopped/errored** turns are
   recorded as distinct outcomes (today the `finally` only closes the client).
5. Capture full prompt/response (gated by content-logging flag, §9).
6. New `POST /api/chat/feedback` writes a Langfuse **score** keyed by
   `request_id`/trace id; frontend adds a thumbs up/down control on assistant
   messages (`chat-panel.tsx`) and a tiny `chat-store` action.

Transport: Langfuse Python SDK (async, batched) **or** OTel GenAI semantic
conventions → OTLP → Langfuse. OTel keeps us vendor-neutral; decide at build.

## 8. LiteLLM configuration

- Enable **Langfuse callback** (`success_callback`/`failure_callback`); keys in
  a k8s Secret.
- Enable **cost tracking + budgets**; mint a **per-user virtual key** so spend
  and rate are attributed per studio user. Pass `user`/`metadata` (agent,
  client) so gateway traces align with owned-client traces.
- Enable **Prometheus `/metrics`**; add a GMP `PodMonitoring`.
- Register common BYO upstreams as model entries / pass-through so BYO-via-proxy
  is captured with cost.

## 9. Privacy, retention, security

Capturing prompts/responses means PII/secrets reach Langfuse. Required controls:
- **Content toggle** (global + per-user opt-out): LiteLLM
  `turn_off_message_logging`; Langfuse masking hook in the SDK path.
- **Redaction** of obvious secrets (API keys, tokens) before send.
- **Retention/TTL** on traces (e.g. 30–90d) + blob lifecycle on the bucket.
- **RBAC** on the Langfuse project; network-restrict ingestion to in-cluster.
- **Consent/notice** to users; the "unobserved" indicator (§5) doubles as a
  transparency signal.

## 10. Metrics (Google Managed Prometheus)

Scrape LiteLLM `/metrics`. Key series for dashboards/alerts: request rate, p50/p95
latency, tokens in/out, **spend** — all by `model`, `user`, `client`, `status`;
plus error rate and budget-remaining. Reuse existing GMP → Grafana/Cloud
Monitoring; no new metrics backend.

## 11. GKE deployment (Flux GitOps — to verify at build, per D3)

Mirror the existing `jupyterhub`/`authentik` pattern: a `langfuse`
`HelmRepository` + `HelmRelease` reconciled by helm-controller.

Dependencies for the scalable (cost-focused) stack:
- **Postgres** — Cloud SQL (preferred) or in-cluster.
- **ClickHouse** — in-chart (analytics store for v3).
- **Redis/Valkey** — Memorystore or in-cluster (queue/cache).
- **Blob storage** — a **GCS bucket** (S3-compatible API) via Workload Identity.
- **Secrets** — Langfuse keys, DB creds, bucket creds as k8s Secrets
  (`valuesFrom`, as jupyterhub already does).
- **Ingress** — via the existing envoy-gateway; long-lived OTLP/HTTP supported.

LiteLLM: add callback + metrics config (Secret + values), `PodMonitoring` for GMP.

> Confirm at build: current Langfuse chart name/version & exact dependency
> subcharts; whether v3 (ClickHouse/Redis/blob) vs. a lighter PG-only mode fits
> measured volume; LiteLLM callback env/version; OTLP vs. SDK ingestion path.

## 12. Personalization feedback loop

Tailoring the experience to a user's interactions is a **serving** problem, not
an observability one. Langfuse/MLflow are analysis backends; reading them
synchronously to render the UI would couple UX latency/availability to an
external tool. So personalization lives in an **app-local layer** fed by the
same capture, in three stages:

```
 capture (own the client)        derive                         serve (app-local, fast)
 chat.py per turn  ─────────────▶ app DB user_profile  ────────▶ GET /api/me/profile ─▶ UI
  agent, slash-cmd, model,         (rolled-up features,           tailors defaults,
  tokens, latency, status, 👍/👎    updated incrementally)          ordering, suggestions
        └─▶ Langfuse (scores, batch feature enrichment)
```

### Two scopes (both supported)

- **Cross-session (durable foundation).** A persisted per-user profile that
  shapes defaults on next login — preferred model/provider, most-used
  agents/skills, frequent tools, recent workflows, rolling feedback sentiment.
- **In-session (lightweight overlay).** Ephemeral signals reacted to within the
  current conversation — e.g. a 👎 immediately nudges the default agent/prompt,
  repeated tool failures surface a relevant skill tip. Held in `chat-store`
  (client) and/or per-`session_id`; it decays/merges into the durable profile.

### `user_profile` store (app DB)

Per-user row(s), keyed by the studio username (already derived in
`pegasus_studio_pre_spawn_hook`). Sketch:

| Field | Meaning |
|-------|---------|
| `user_id` | studio username (stable key) |
| `agent_usage` | JSON: agent → count / last_used |
| `slash_usage` | JSON: command → count |
| `model_stats` | JSON: model → {turns, ok, errors, avg_latency, 👍/👎} |
| `tool_usage` | JSON: tool → {count, fail_rate} |
| `recent_workflows` | recent run ids / projects |
| `preferences` | explicit user-set overrides (win over derived) |
| `updated_at` | last rollup |

Updated two ways: **incrementally** in `chat.py`'s finalize step (cheap counter
bumps per turn) and via a **periodic rollup** job (recompute rates, optionally
enrich from Langfuse exports). Explicit `preferences` always override derived
values, so personalization never fights an intentional user choice.

### `GET /api/me/profile` (serving contract)

Returns derived **hints** the UI consumes (not raw telemetry):

```jsonc
{
  "suggested_agents":   ["debug", "scaffold"],        // ordered by fit
  "default_model":      "…",                            // best-performing for this user
  "suggested_skills":   ["pegasus-review"],            // from failure/feedback patterns
  "recent_workflows":   ["run0007", "…"],
  "preferences":        { /* explicit overrides */ }
}
```

`PUT /api/me/preferences` lets users set explicit overrides. The frontend uses
the hints to reorder agent/skill lists, pick defaults, and surface contextual
suggestions.

### Backend roles in the loop

- **Langfuse** — source of 👍/👎 **scores** and a batch feature source to enrich
  the profile; the online observability backend.
- **MLflow** — *offline* evaluation of whether personalization improves outcomes
  (A/B, eval harness). Not in the serving path.

### Privacy (extends §9)

Building a per-user profile is user **profiling** — requires consent/notice, a
per-user **opt-out** (no profile built; UX falls back to global defaults), and
transparency (the "observed" indicator from §5 doubles here). Profiles are
derived, bounded (counts/rates, not raw content), and honor the §9 retention
policy.

## 13. Rollout phases

- **M1 — Backend capture (no new infra):** persist per-turn telemetry from
  `chat.py` to the existing SQLite (table `chat_telemetry` + per-tool rows) and
  emit structured `structlog` events. Delivers immediate value; de-risks the
  data model before standing up Langfuse.
- **M2 — Langfuse on GKE:** deploy via Flux; point `chat.py` instrumentation at
  it; wire LiteLLM → Langfuse callback.
- **M3 — Cost & metrics:** LiteLLM per-user virtual keys + budgets; GMP
  `PodMonitoring`; Grafana dashboard.
- **M4 — Feedback loop:** `POST /api/chat/feedback` + thumbs UI → Langfuse
  scores.
- **M5 — Coverage/transparency:** default proxy config for opencode; "observed"
  indicator; retention/redaction policies enforced.
- **M6 — Personalization (§12):** `user_profile` store + incremental updates in
  `chat.py`; `GET /api/me/profile` + `PUT /api/me/preferences`; UI consumes
  hints; in-session 👎 overlay; opt-out. Optional MLflow eval of efficacy.

## 14. Alternatives considered

- **MLflow (Tracing/3.x)** — captures prompt/response, tokens, latency, tool
  spans; adds eval + prompt registry + ML lineage; **lighter infra**
  (Tracking Server + Postgres + GCS, no ClickHouse/Redis). Weak on multi-user
  **cost/spend/budgets** and per-user production trace querying, and not a
  serving store for personalization (run/experiment-oriented). Best as the
  **offline eval** half (M6) and attractive if MLflow is already run for the ML
  side; not the primary online backend given the cost-focused goal (D2/D4).
- **Arize Phoenix** — lighter (Postgres-only), OTel-native, strong evals; weaker
  cost/spend — less aligned with the cost-focused goal (D2).
- **Helicone** — proxy-model, ClickHouse-heavy; overlaps LiteLLM's role.
- **LiteLLM UI/logs only** — already present (spend/keys), but proxied traffic
  only and thin tracing → fails D1's owned-client requirement.

## 15. Open questions

1. Content logging **on by default** or opt-in per user? (Drives §9 + consent.)
2. Postgres: **Cloud SQL** or in-cluster? Redis: Memorystore or in-cluster?
3. Cost for BYO-via-chat from token counts × price map — acceptable as an
   estimate, or only show cost for known-priced models?
4. Instrumentation transport: **Langfuse SDK** vs. **OTel/OTLP** (vendor-neutral
   but more moving parts)?
5. Is M1 (SQLite-only capture) worth shipping first, or go straight to Langfuse?
6. Personalization (§12): **opt-in or opt-out** by default? Which first tailoring
   surfaces matter most (agent/skill ordering, default model, contextual
   suggestions)? Should explicit `preferences` be exposed in Settings now?
