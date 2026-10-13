"""OpenCodeAdapter — knowledge adapter for OpenCode.

Ported from config_builder.py:build_opencode_config.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

import structlog

from knowledge import layout
from knowledge.adapters import KnowledgeAdapter
from knowledge.common import (
    model_limits_by_bare_id,
    build_knowledge_appendix,
    ensure_context_file,
    record_generated_file,
    remove_file_if,
    remove_if_unmodified,
    remove_generated_context_file,
    rmdir_if_empty,
    unlink_if_links_into,
)
from llm.providers import PROVIDERS, provider_type

log = structlog.get_logger()



def _provider_options(ptype: str, api_key: str, base_url: str) -> dict[str, str]:
    """Build one provider's ``options`` block.

    Native SDK providers (anthropic, openai) take only an apiKey — a baseURL
    would point the SDK away from the vendor API. Ollama is keyless but its
    SDK still requires *some* apiKey value. Everything else is treated as
    OpenAI-compatible: baseURL required, key optional (self-hosted endpoints
    are often keyless).
    """
    options: dict[str, str] = {}
    if ptype in ("anthropic", "openai"):
        if api_key:
            options["apiKey"] = api_key
    elif ptype == "ollama":
        options["baseURL"] = base_url or "http://localhost:11434/v1"
        options["apiKey"] = "ollama"
    else:
        if base_url:
            options["baseURL"] = base_url
        if api_key:
            options["apiKey"] = api_key
    return options


# The OTel plugin that turns the engine's own events into telemetry (#367
# step 6, docs/llm-telemetry-design.md). Pinned exactly: opencode resolves a
# bare package name to whatever npm serves that day, and a plugin that throws
# on import fails *silently* (docs/OPENCODE-CHAT-SPIKE.md) — a breaking release
# would take telemetry away with nothing in the panel to say so.
_OTEL_PLUGIN = "@devtheops/opencode-plugin-otel@1.5.1"

# Only these characters survive into a resource attribute value. The attribute
# string is a flat `k=v,k=v` list, so a username containing a comma or an
# equals sign would not corrupt one attribute — it would silently redefine the
# ones after it.
_ATTR_SAFE = re.compile(r"[^A-Za-z0-9._@:-]")


def _attr_value(raw: str) -> str:
    return _ATTR_SAFE.sub("_", raw.strip())


def _telemetry_plugin(content_capture: bool) -> list[Any] | None:
    """The OTel plugin entry for opencode.json, or None to leave it out.

    Absent unless ``STUDIO_OTLP_ENDPOINT`` names a collector. Studio also runs
    on a FABRIC VM and in plain `docker run` containers where no collector
    exists, and there a configured exporter is not harmless: the plugin would
    be fetched from npm on every engine start and then retry an endpoint that
    is never coming up. No endpoint, no plugin, no npm dependency at all.

    Two things travel separately here, because they carry different risk:

    * **Metrics** (tokens, tool durations, session and message counts, model
      ids) are on whenever there is a collector. They are counters about
      activity and contain nothing the user typed.
    * **Traces** carry the prompt itself. The plugin puts it in the span's
      `input.value` regardless of `capturePromptInLogs`, so traces *are* the
      content-capture decision and they stay off until the user opts in
      per-user in Settings. When off, `disabledTraces: ["all"]` expands to
      every trace type the plugin knows (verified against its
      `expandDisabledTraces`), so a future fourth trace type is disabled too
      rather than newly exporting prompts.

    Log events are off unconditionally: there is no log backend deployed
    (`monitoring` is kube-prometheus-stack only), so they would be egress to
    somewhere that drops them.
    """
    endpoint = (os.environ.get("STUDIO_OTLP_ENDPOINT") or "").strip()
    if not endpoint:
        return None

    # Per-user attribution. The pod is per-user, so the identity is the pod's:
    # JUPYTERHUB_USER is injected by KubeSpawner and trusted the same way
    # main.py trusts it for the account menu.
    user = _attr_value(
        os.environ.get("JUPYTERHUB_USER")
        or os.environ.get("USER")
        or os.environ.get("LOGNAME")
        or ""
    )
    attrs = [
        "service.namespace=studio",
        f"deployment.environment={_attr_value(os.environ.get('STUDIO_DEPLOYMENT_ENV') or 'unknown')}",
        # Whether this session's traces are allowed to carry content, recorded
        # on the data itself. The collector redacts content from any resource
        # that does not say `true` — belt and braces, so a stale config left by
        # a missed engine restart cannot leak a prompt.
        f"studio.content_capture={'true' if content_capture else 'false'}",
    ]
    if user:
        attrs.append(f"enduser.id={user}")

    options: dict[str, Any] = {
        "enabled": True,
        "endpoint": endpoint,
        # http/protobuf, not the plugin's grpc default: the collector is
        # reached over plain HTTP through the cluster network, and the OTLP
        # http exporter is the one that needs no extra port or TLS setup.
        "protocol": (os.environ.get("STUDIO_OTLP_PROTOCOL") or "http/protobuf"),
        "logsEnabled": False,
        "capturePromptInLogs": False,
        "disabledTraces": [] if content_capture else ["all"],
        "resourceAttributes": ",".join(attrs),
    }
    return [_OTEL_PLUGIN, options]


def _studio_mcp_url() -> str:
    """Where studio-api serves the Pegasus MCP tools, from inside this pod.

    Loopback rather than the public hostname: the engine is a sibling process,
    and routing its tool calls back out through the ingress would make them
    depend on a gateway that has nothing to do with them.
    """
    port = os.environ.get("STUDIO_API_PORT", "8080")
    return f"http://127.0.0.1:{port}/api/mcp"


def _studio_mcp_headers() -> dict[str, Any]:
    """The proxy-stamped secret, when this session runs behind the gate.

    Empty on the hosted path, so the generated config is unchanged there. When
    ``STUDIO_INTERNAL_SECRET`` is set, studio-api refuses anything that did not
    come through the front proxy — and this connection deliberately does not,
    being a sibling process on loopback — so the header has to be supplied here
    instead.
    """
    secret = (os.environ.get("STUDIO_INTERNAL_SECRET") or "").strip()
    if not secret:
        return {}
    return {"headers": {"X-Studio-Internal": secret}}


def _global_config_dir() -> str:
    """OpenCode's global config dir (XDG). Config here applies from ANY cwd —
    the workspace-only config was picked up only when opencode started in
    ~/work, so terminals launched elsewhere (PVC mounts at ~) fell back to the
    default 'Zen' provider."""
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
        os.environ.get("HOME", "/tmp"), ".config"
    )
    return os.path.join(base, "opencode")


def _link_agents_into_global() -> int:
    """Symlink each agent file into opencode's global agent directory.

    ``~/.config/opencode/agent/<name>.md`` is the location opencode documents
    for agents that should be available from any cwd. Individual files are
    linked into a real directory rather than symlinking the directory itself:
    the loader globs for ``<name>.md``, and a link per file is read through
    ``open()`` like any other file.

    Stale links (an agent removed upstream) are dropped, so the directory always
    reflects the checkout.
    """
    src = layout.agents_dir()
    dst = os.path.join(_global_config_dir(), "agent")
    if not os.path.isdir(src):
        return 0

    try:
        os.makedirs(dst, exist_ok=True)
    except OSError as e:
        log.warning("opencode_agent_dir_failed", dir=dst, error=str(e))
        return 0

    wanted = {n for n in os.listdir(src) if n.endswith(".md")}

    # Drop links we previously made that no longer have a source. Only links are
    # removed: an agent the user wrote by hand is theirs, not ours to delete.
    for name in os.listdir(dst):
        path = os.path.join(dst, name)
        if os.path.islink(path) and name not in wanted:
            try:
                os.unlink(path)
            except OSError:
                pass

    linked = 0
    for name in sorted(wanted):
        target, path = os.path.join(src, name), os.path.join(dst, name)
        try:
            if os.path.islink(path) or os.path.exists(path):
                if os.path.realpath(path) == os.path.realpath(target):
                    linked += 1
                    continue
                os.unlink(path)
            os.symlink(target, path)
            linked += 1
        except OSError as e:
            log.warning("opencode_agent_link_failed", agent=name, error=str(e))
    return linked


def _looks_like_our_config(text: str) -> bool:
    """An opencode.json written by update_llm_config(): it always carries the
    Studio MCP servers. A user's own config would not name gitmcp.io/pegasus."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return False
    mcp = data.get("mcp") if isinstance(data, dict) else None
    if not isinstance(mcp, dict):
        return False
    docs = mcp.get("pegasus-docs")
    return isinstance(docs, dict) and docs.get("url") == "https://gitmcp.io/pegasus-isi/pegasus"


class OpenCodeAdapter(KnowledgeAdapter):
    """Translates canonical knowledge into OpenCode's native format.

    This config is read by two things now: the terminal tool, and the
    supervised ``opencode serve`` process that backs the chat panel (#367).
    The server reads it once at startup and no endpoint reloads it, so
    ``config_changed`` records whether this write actually altered the file —
    the propagator restarts the server only when it did.
    """

    #: Whether the last ``update_llm_config`` changed the file on disk.
    config_changed: bool = False

    def install(self, workspace: str, llm_config: dict[str, Any]) -> None:
        # Copy PEGASUS_AI.md as AGENTS.md — into the workspace and the global
        # config dir, so the config's `instructions: ["AGENTS.md"]` resolves
        # whether opencode starts in ~/work or anywhere else.
        content = layout.context_document()
        if content:
            appendix = build_knowledge_appendix()
            body = content + ("\n" + appendix if appendix else "")
            for dst_dir in (workspace, _global_config_dir()):
                try:
                    os.makedirs(dst_dir, exist_ok=True)
                    with open(os.path.join(dst_dir, "AGENTS.md"), "w") as f:
                        f.write(body)
                except OSError as e:
                    log.warning("opencode_agents_write_failed", dir=dst_dir, error=str(e))

        _link_agents_into_global()

        # Write opencode.json (global — read from any cwd)
        self.update_llm_config(workspace, llm_config)
        log.info("opencode_installed", workspace=workspace)

    def update_llm_config(self, workspace: str, llm_config: dict[str, Any]) -> None:
        """Rewrite opencode.json with the current provider config."""
        # Runs on every start, so it is also where a deleted AGENTS.md comes back
        # and where the agent links are (re)made. install() alone was not
        # enough: startup only calls this, so when the config root moved to $HOME
        # the links were never created there and the assistant ran with no skills
        # at all — improvising instead of following pegasus-scaffold.
        ensure_context_file(os.path.join(workspace, "AGENTS.md"))
        ensure_context_file(os.path.join(_global_config_dir(), "AGENTS.md"))
        _link_agents_into_global()
        provider = llm_config.get("provider", "anthropic")
        # "" is "nothing configured" (#504): declare the saved providers, but
        # name no default model, rather than invent one the user never chose.
        unconfigured = provider == ""
        ptype = provider_type(provider)  # custom-<id> -> "custom", etc.
        model = llm_config.get("model")
        api_key = llm_config.get("api_key", "")
        base_url = llm_config.get("base_url", "")

        global_dir = _global_config_dir()
        preset = PROVIDERS.get(ptype, PROVIDERS["anthropic"])
        if not model:
            model = preset.get("default_model", "claude-sonnet-4-5-20250929")
        # The opencode `model` field is "<providerID>/<modelID>". Strip any
        # provider prefix that may already be on the stored model so we build a
        # clean id (e.g. "default"), not "custom-x/default" doubled up.
        model_id = str(model).rsplit("/", 1)[-1]

        # Every saved provider goes into the config, not just the active one:
        # opencode's provider/model picker lists exactly what this map declares,
        # so a custom endpoint that was saved but never activated used to be
        # invisible in the TUI (#205). The active provider may not have a saved
        # row (legacy configs written straight to llm_config), so synthesize it.
        saved = list(llm_config.get("all_providers") or [])
        if not unconfigured and not any(p.get("provider_id") == provider for p in saved):
            saved.insert(0, {
                "provider_id": provider,
                "name": preset.get("name", provider),
                "api_key": api_key,
                "base_url": base_url,
                "default_model": model_id,
            })

        providers_cfg: dict[str, Any] = {}
        for prov in saved:
            pid = prov.get("provider_id") or provider
            p_type = provider_type(pid)
            p_preset = PROVIDERS.get(p_type, PROVIDERS["custom"])
            p_key = prov.get("api_key") or ""
            p_base = prov.get("base_url") or p_preset.get("base_url") or ""
            if pid == provider:
                # The live llm_config wins over the saved row for the active
                # provider, so a key/URL edited via PUT /config isn't shadowed
                # by a stale provider_configs row.
                p_key, p_base = api_key or p_key, base_url or p_base
            p_model = str(
                prov.get("default_model")
                or (model_id if pid == provider else "")
                or p_preset.get("default_model")
                or ""
            ).rsplit("/", 1)[-1]
            # Declare the endpoint's full model list when the propagator
            # fetched one (opencode's picker lists only what is declared, so
            # default_model alone meant one selectable model per endpoint,
            # #207). No list — endpoint down, keyless probe rejected — falls
            # back to default_model; a draft with neither still shows up
            # (empty model list) rather than silently vanishing.
            model_ids = [
                str(m).rsplit("/", 1)[-1] for m in prov.get("models") or []
            ]
            if p_model and p_model not in model_ids:
                model_ids.insert(0, p_model)
            # Declare each model's context window and output cap when the
            # endpoint reported them (propagator, ``model_limits``). opencode
            # compacts a session only against a declared ``limit.context``,
            # and with no ``limit.output`` it asks for 32000 output tokens on
            # every request; once the prompt has grown to within 32000 of the
            # real window the backend rejects every turn, the compaction call
            # included, and the session is stuck. No limit reported means no
            # ``limit`` key — opencode's own defaults beat a made-up number.
            limits = model_limits_by_bare_id(prov)
            models_cfg: dict[str, Any] = {}
            for m in model_ids:
                entry: dict[str, Any] = {"name": m}
                if limits.get(m):
                    entry["limit"] = limits[m]
                models_cfg[m] = entry
            providers_cfg[pid] = {
                "npm": p_preset.get("npm", "@ai-sdk/openai-compatible"),
                "name": prov.get("name") or p_preset.get("name", pid),
                "options": _provider_options(p_type, p_key, p_base),
                "models": models_cfg,
            }

        # Studio's "Confirm shell commands" setting, expressed as OpenCode's own
        # permission rules. The builtin loop implemented this itself
        # (services/chat_permissions.py, a per-turn future resolved by an
        # endpoint); OpenCode has a permission system, so the setting becomes
        # configuration rather than a second implementation. Only bash is
        # gated: file reads and writes inside the workspace were never gated in
        # Studio and turning them into prompts would make the panel unusable.
        require_approval = llm_config.get("require_command_approval", True)
        permission = {"bash": "ask" if require_approval else "allow"}

        config = {
            "$schema": "https://opencode.ai/config.json",
            "provider": providers_cfg,
            "model": f"{provider}/{model_id}",
            "permission": permission,
            # Absolute, not "AGENTS.md": a bare name is resolved against the
            # project opencode walks up to, which is whatever directory the
            # process happened to start in.
            "instructions": [os.path.join(global_dir, "AGENTS.md")],
            "mcp": {
                "pegasus-docs": {
                    "type": "remote",
                    "url": "https://gitmcp.io/pegasus-isi/pegasus",
                    "enabled": True,
                },
                "kiso-docs": {
                    "type": "remote",
                    "url": "https://gitmcp.io/pegasus-isi/kiso",
                    "enabled": True,
                },
                # This Studio's own workflow tools (#367): the user's projects
                # and runs, live job status, a failure diagnosis, run outputs.
                # The other two servers are documentation; this one is the
                # session's actual state, which is what makes "why did this run
                # fail?" answerable without the user pasting anything.
                #
                # Loopback, and read-only. On the hosted path studio-api has
                # no auth of its own — an upstream ingress authenticates the
                # browser — and the engine runs in this pod as this user.
                #
                # On a gated session (#391) that stops being true: studio-api
                # requires a proxy-stamped header, and this call does not go
                # through the proxy — it is loopback, straight to uvicorn. So
                # the engine has to present the secret itself, or every Pegasus
                # tool returns 403 and the chat quietly loses the ability to
                # answer anything about the user's own runs.
                "pegasus": {
                    "type": "remote",
                    "url": _studio_mcp_url(),
                    "enabled": True,
                    **_studio_mcp_headers(),
                },
            },
        }

        # Telemetry (#367 step 6). Declared in the config rather than through
        # OPENCODE_* environment variables so it follows the same path as every
        # other engine setting: written here, picked up on the restart the
        # propagator schedules. An env-var-only switch would need a supervisord
        # change and a pod restart to alter, which a per-user preference cannot
        # ask for.
        plugin = _telemetry_plugin(
            bool(llm_config.get("telemetry_content_capture"))
        )
        if plugin is not None:
            config["plugin"] = [plugin]

        # Point the skill loader straight at the checkout. Declaring the path is
        # what makes discovery independent of where opencode starts: the loader
        # otherwise finds skills by walking up from the cwd, so what the model
        # can invoke depends on the directory a process happened to launch in.
        # It also keeps one source — the checkout — rather than a linked copy
        # per tool, where two trees offering the same skill names left which one
        # answered down to directory-scan order.
        skills_dir = layout.skills_dir()
        if os.path.isdir(skills_dir):
            config["skills"] = {"paths": [skills_dir]}

        # Global config only. It is read from any cwd, so the workspace copy
        # this used to also write was redundant — byte-identical content in a
        # second place to keep in sync, and the only non-hidden file Studio put
        # in the directory users are told to keep workflows in. uninstall()
        # still removes the legacy copy for workspaces that have one.
        if unconfigured:
            # No default: the engine then asks for a model per message, from
            # the picker, instead of defaulting to one nobody configured (#504).
            config.pop("model", None)
        blob = json.dumps(config, indent=2)
        targets = [os.path.join(global_dir, "opencode.json")]
        self.config_changed = False
        for path in targets:
            try:
                # Compare before writing. Propagation runs on every startup and
                # every provider save, and a write that changed nothing must
                # not cost the chat panel a server restart mid-conversation.
                #
                # Compared as bytes, deliberately. Reading as text raises
                # UnicodeDecodeError on a file with one bad byte, and that is
                # not an OSError: it escaped this method, the config was never
                # rewritten, config_changed stayed False so the engine was
                # never restarted, and every later propagation failed the same
                # way. A damaged config used to heal itself on the next write.
                try:
                    with open(path, "rb") as f:
                        unchanged = f.read() == blob.encode()
                except (OSError, ValueError):
                    unchanged = False
                if unchanged:
                    # Still ours; make sure that is on record (an install
                    # that predates the manifest gets one on first restart).
                    record_generated_file(path)
                    log.info("opencode_config_unchanged", path=path)
                    continue
                os.makedirs(os.path.dirname(path), exist_ok=True)
                # Written via a temp file and renamed, because the engine reads
                # this file at startup and the propagator that writes it runs
                # under a hard timeout in the entrypoint. A SIGTERM between
                # truncate and flush would otherwise leave a half-written
                # config for a starting engine to read.
                tmp = f"{path}.{os.getpid()}.tmp"
                with open(tmp, "w") as f:
                    f.write(blob)
                os.replace(tmp, path)
                record_generated_file(path)
                self.config_changed = True
                log.info("opencode_config_written", path=path)
            except OSError as e:
                log.warning("opencode_config_write_failed", path=path, error=str(e))

    def uninstall(self, workspace: str) -> None:
        # ``workspace`` is $HOME. Only what we generated goes, file by file.
        remove_generated_context_file(os.path.join(workspace, "AGENTS.md"))
        # Workspaces seeded before the workspace copy was dropped still have
        # one; at $HOME a file by that name is the user's unless it is ours.
        remove_if_unmodified(os.path.join(workspace, "opencode.json"), _looks_like_our_config)
        # <workspace>/.opencode is not handled here: it was only ever written
        # under ~/work, which installer.uninstall() prunes by exact name.

        gdir = _global_config_dir()
        remove_generated_context_file(os.path.join(gdir, "AGENTS.md"))
        remove_if_unmodified(os.path.join(gdir, "opencode.json"), _looks_like_our_config)

        # Only the links we made. A hand-written agent beside them stays —
        # the same rule _link_agents_into_global() applies on every refresh.
        agent_dir = os.path.join(gdir, "agent")
        if os.path.isdir(agent_dir):
            for name in os.listdir(agent_dir):
                unlink_if_links_into(os.path.join(agent_dir, name))
        rmdir_if_empty(agent_dir)
        rmdir_if_empty(gdir)
        log.info("opencode_uninstalled", workspace=workspace)
