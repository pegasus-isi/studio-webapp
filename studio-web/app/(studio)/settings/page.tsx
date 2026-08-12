"use client";

import { useEffect, useState } from "react";
import {
  useProviders,
  useProviderConfigs,
  useUpsertProviderConfig,
  useActivateProvider,
  useDeleteProviderConfig,
  useValidateProvider,
} from "@/lib/hooks/use-llm";
import { useTools } from "@/lib/hooks/use-tools";
import { ToolCard } from "@/components/tools/tool-card";
import { useToolStore } from "@/lib/stores/tool-store";
import { usePanelStore } from "@/lib/stores/panel-store";
import { cn } from "@/lib/utils";
import type { ProviderConfig } from "@/lib/api/client";
import * as api from "@/lib/api/client";

type Tab = "providers" | "tools" | "chat" | "pegasus" | "notifications";

// Notification categories the engine emits, with friendly labels for the
// mute toggles. `kind` matches the backend `kind` field.
const NOTIFICATION_CATEGORIES: {
  kind: string;
  label: string;
  description: string;
}[] = [
  {
    kind: "workflow_state",
    label: "Workflow state changes",
    description: "When a run starts, succeeds, or fails.",
  },
  {
    kind: "job_failure",
    label: "Job failures",
    description: "When jobs within a running workflow fail.",
  },
  {
    kind: "resource",
    label: "Resource provisioning",
    description: "When a run needs more capacity (e.g. idle/held jobs).",
  },
  {
    kind: "manual",
    label: "Other alerts",
    description: "Manual and miscellaneous notifications.",
  },
];

export default function SettingsPage() {
  const [activeTab, setActiveTab] = useState<Tab>("providers");

  return (
    <div className="mx-auto max-w-4xl p-6">
      <h1 className="text-lg font-semibold text-fg">Settings</h1>
      <p className="mt-1 text-sm text-fgmuted">
        Configure LLM providers, manage AI tools, and set Pegasus options.
      </p>

      {/* Tab navigation */}
      <div className="mt-4 flex border-b border-line">
        <button
          onClick={() => setActiveTab("providers")}
          className={cn(
            "px-4 py-2 text-sm font-medium border-b-2 -mb-px",
            activeTab === "providers"
              ? "border-pegasus-600 text-pegasus-600"
              : "border-transparent text-fgmuted hover:text-fg hover:border-line"
          )}
        >
          LLM Providers
        </button>
        <button
          onClick={() => setActiveTab("tools")}
          className={cn(
            "px-4 py-2 text-sm font-medium border-b-2 -mb-px",
            activeTab === "tools"
              ? "border-pegasus-600 text-pegasus-600"
              : "border-transparent text-fgmuted hover:text-fg hover:border-line"
          )}
        >
          Tools
        </button>
        <button
          onClick={() => setActiveTab("chat")}
          className={cn(
            "px-4 py-2 text-sm font-medium border-b-2 -mb-px",
            activeTab === "chat"
              ? "border-pegasus-600 text-pegasus-600"
              : "border-transparent text-fgmuted hover:text-fg hover:border-line"
          )}
        >
          Chat
        </button>
        <button
          onClick={() => setActiveTab("pegasus")}
          className={cn(
            "px-4 py-2 text-sm font-medium border-b-2 -mb-px",
            activeTab === "pegasus"
              ? "border-pegasus-600 text-pegasus-600"
              : "border-transparent text-fgmuted hover:text-fg hover:border-line"
          )}
        >
          Pegasus Options
        </button>
        <button
          onClick={() => setActiveTab("notifications")}
          className={cn(
            "px-4 py-2 text-sm font-medium border-b-2 -mb-px",
            activeTab === "notifications"
              ? "border-pegasus-600 text-pegasus-600"
              : "border-transparent text-fgmuted hover:text-fg hover:border-line"
          )}
        >
          Notifications
        </button>
      </div>

      {/* Tab content */}
      <div className="mt-6">
        {activeTab === "providers" && <ProvidersTab />}
        {activeTab === "tools" && <ToolsTab />}
        {activeTab === "chat" && <ChatTab />}
        {activeTab === "pegasus" && <PegasusOptionsTab />}
        {activeTab === "notifications" && <NotificationsTab />}
      </div>
    </div>
  );
}

/* ─── LLM Providers Tab ─── */

function ProvidersTab() {
  const { data: providerData } = useProviders();
  const { data: configData, isLoading } = useProviderConfigs();
  const upsertConfig = useUpsertProviderConfig();
  const activateProvider = useActivateProvider();
  const deleteConfig = useDeleteProviderConfig();

  const presets = providerData?.providers ?? [];
  const savedConfigs = configData?.configs ?? [];

  // State for "Add Provider" form
  const [showAdd, setShowAdd] = useState(false);
  const [newProviderId, setNewProviderId] = useState("");

  const handleAdd = async () => {
    if (!newProviderId) return;
    const preset = presets.find((p) => p.id === newProviderId);
    // "custom" can be added multiple times — each gets a unique id so several
    // custom endpoints coexist. The user names/configures it in its card.
    const isCustom = newProviderId === "custom";
    const provider_id = isCustom
      ? `custom-${Date.now().toString(36)}`
      : newProviderId;
    const customCount = savedConfigs.filter((c) =>
      c.provider_id.startsWith("custom")
    ).length;
    await upsertConfig.mutateAsync({
      provider_id,
      name: isCustom
        ? `Custom Endpoint${customCount ? " " + (customCount + 1) : ""}`
        : preset?.name || newProviderId,
      base_url: isCustom ? "" : preset?.base_url || "",
      default_model: isCustom ? "" : preset?.default_model || "",
      api_key: "",
      is_active: savedConfigs.length === 0,
    });
    setNewProviderId("");
    setShowAdd(false);
  };

  // Presets not yet added — but "custom" stays available so you can add
  // multiple distinct custom endpoints.
  const availablePresets = presets.filter(
    (p) =>
      p.id === "custom" || !savedConfigs.some((c) => c.provider_id === p.id)
  );

  if (isLoading) {
    return <p className="text-sm text-fgsubtle">Loading providers...</p>;
  }

  return (
    <div className="space-y-4">
      <p className="text-sm text-fgmuted">
        Add LLM providers and configure API keys. The active provider is used by
        the chat and propagated to all installed AI tools.
      </p>

      {/* Saved provider cards */}
      {savedConfigs.length === 0 && (
        <p className="text-sm text-fgsubtle italic">
          No providers configured. Add one below.
        </p>
      )}

      {savedConfigs.map((config) => (
        <ProviderCard
          key={config.provider_id}
          config={config}
          presets={presets}
          onSave={async (data) => {
            await upsertConfig.mutateAsync(data);
          }}
          onActivate={async () => {
            await activateProvider.mutateAsync(config.provider_id);
          }}
          onDelete={async () => {
            await deleteConfig.mutateAsync(config.provider_id);
          }}
          isSaving={upsertConfig.isPending}
        />
      ))}

      {/* Add provider */}
      {showAdd ? (
        <div className="rounded-lg border border-dashed border-line bg-base p-4">
          <div className="flex items-center gap-2">
            <select
              value={newProviderId}
              onChange={(e) => setNewProviderId(e.target.value)}
              className="flex-1 rounded-md border border-line px-3 py-2 text-sm"
            >
              <option value="">Select a provider to add...</option>
              {availablePresets.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
            <button
              onClick={handleAdd}
              disabled={!newProviderId || upsertConfig.isPending}
              className="rounded-md bg-pegasus-600 px-4 py-2 text-sm text-white hover:bg-pegasus-700 disabled:opacity-50"
            >
              Add
            </button>
            <button
              onClick={() => {
                setShowAdd(false);
                setNewProviderId("");
              }}
              className="rounded-md border border-line px-3 py-2 text-sm text-fgmuted hover:bg-base"
            >
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <button
          onClick={() => setShowAdd(true)}
          disabled={availablePresets.length === 0}
          className="w-full rounded-lg border border-dashed border-line py-3 text-sm text-fgmuted hover:border-gray-400 hover:text-fg disabled:opacity-50"
        >
          + Add Provider
        </button>
      )}
    </div>
  );
}

/* ─── Single Provider Card ─── */

function ProviderCard({
  config,
  presets,
  onSave,
  onActivate,
  onDelete,
  isSaving,
}: {
  config: ProviderConfig;
  presets: { id: string; name: string; base_url: string | null; default_model: string; api_key_env: string | null }[];
  onSave: (data: {
    provider_id: string;
    name: string;
    api_key: string;
    base_url: string;
    default_model: string;
    is_active: boolean;
  }) => Promise<void>;
  onActivate: () => Promise<void>;
  onDelete: () => Promise<void>;
  isSaving: boolean;
}) {
  const validate = useValidateProvider();

  // Saved keys are write-only server-side (the GET only reports
  // has_api_key), so the field starts empty; leaving it blank on Save keeps
  // the stored key.
  const [apiKey, setApiKey] = useState("");
  const [model, setModel] = useState(config.default_model || "");
  const [baseUrl, setBaseUrl] = useState(config.base_url || "");
  const [name, setName] = useState(config.name || "");
  const [saved, setSaved] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{
    valid: boolean;
    models: string[];
    error: string | null;
  } | null>(null);

  // Sync if config changes externally
  useEffect(() => {
    setModel(config.default_model || "");
    setBaseUrl(config.base_url || "");
    setName(config.name || "");
  }, [config.default_model, config.base_url, config.name]);

  const preset = presets.find((p) => p.id === config.provider_id);
  const isCustom = config.provider_id.startsWith("custom");
  const needsBaseUrl = isCustom || config.provider_id === "ollama";

  const handleSave = async () => {
    await onSave({
      provider_id: config.provider_id,
      name: name || config.name,
      api_key: apiKey,
      base_url: baseUrl,
      default_model: model,
      is_active: config.is_active,
    });
    setSaved(true);
    setTimeout(() => setSaved(false), 3000);
  };

  const handleTest = async () => {
    setTesting(true);
    try {
      // A typed key tests via /validate; an empty field with a saved key
      // tests server-side against the stored key (which the browser can't
      // read).
      const result = apiKey
        ? await validate.mutateAsync({
            provider: config.provider_id,
            api_key: apiKey,
            base_url: baseUrl || preset?.base_url || undefined,
          })
        : await api.getSavedProviderModels(config.provider_id);
      setTestResult(result);
    } catch (e) {
      setTestResult({
        valid: false,
        models: [],
        error: (e as Error).message || "Request failed",
      });
    } finally {
      setTesting(false);
    }
  };

  return (
    <div
      className={cn(
        "rounded-lg border bg-surface p-4",
        config.is_active ? "border-pegasus-500 ring-1 ring-pegasus-200" : "border-line"
      )}
    >
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <h3 className="font-medium text-fg">{config.name}</h3>
          {config.is_active && (
            <span className="rounded-full bg-pegasus-100 dark:bg-pegasus-400/15 px-2 py-0.5 text-xs font-medium text-pegasus-700 dark:text-pegasus-300">
              Active
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          {!config.is_active && (
            <button
              onClick={onActivate}
              className="text-xs text-pegasus-600 hover:text-pegasus-800"
            >
              Set Active
            </button>
          )}
          <button
            onClick={onDelete}
            className="text-xs text-red-500 hover:text-red-700 dark:text-rose-400"
          >
            Remove
          </button>
        </div>
      </div>

      <div className="mt-3 space-y-3">
        {/* Name (custom endpoints — so multiple are distinguishable) */}
        {isCustom && (
          <div>
            <label className="block text-xs font-medium text-fgmuted">
              Name
            </label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. RENCI eLLM"
              className="mt-1 block w-full rounded-md border border-line px-3 py-1.5 text-sm shadow-sm focus:border-pegasus-500 focus:ring-pegasus-500"
            />
          </div>
        )}

        {/* API Key */}
        <div>
          <label className="block text-xs font-medium text-fgmuted">
            API Key
            {preset?.api_key_env && (
              <span className="ml-1 font-normal text-fgsubtle">
                ({preset.api_key_env})
              </span>
            )}
          </label>
          <input
            type="password"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            placeholder={
              config.has_api_key
                ? "•••••••• saved — leave blank to keep"
                : "Enter API key..."
            }
            className="mt-1 block w-full rounded-md border border-line px-3 py-1.5 text-sm shadow-sm focus:border-pegasus-500 focus:ring-pegasus-500"
          />
        </div>

        {/* Model */}
        <div>
          <label className="block text-xs font-medium text-fgmuted">
            Model
          </label>
          <input
            type="text"
            value={model}
            onChange={(e) => setModel(e.target.value)}
            placeholder={preset?.default_model || "model name (or Test to list models)"}
            className="mt-1 block w-full rounded-md border border-line px-3 py-1.5 text-sm shadow-sm focus:border-pegasus-500 focus:ring-pegasus-500"
          />
          {/* Discovered models as clickable chips (after Test Connectivity). */}
          {testResult?.valid && testResult.models.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1">
              {testResult.models.map((m) => (
                <button
                  key={m}
                  onClick={() => setModel(m)}
                  className={cn(
                    "rounded-full border px-2 py-0.5 text-xs",
                    m === model
                      ? "border-pegasus-500 bg-pegasus-50 text-pegasus-700 dark:bg-pegasus-400/10 dark:text-pegasus-300"
                      : "border-line text-fgmuted hover:bg-muted"
                  )}
                >
                  {m}
                </button>
              ))}
            </div>
          )}
          {testResult?.valid && testResult.models.length === 0 && (
            <p className="mt-1 text-xs text-fgsubtle">
              Connected, but the endpoint listed no models — enter one manually.
            </p>
          )}
        </div>

        {/* Base URL (custom/ollama) */}
        {needsBaseUrl && (
          <div>
            <label className="block text-xs font-medium text-fgmuted">
              Base URL
            </label>
            <input
              type="text"
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
              placeholder="e.g. https://api.example.com/v1"
              className="mt-1 block w-full rounded-md border border-line px-3 py-1.5 text-sm shadow-sm focus:border-pegasus-500 focus:ring-pegasus-500"
            />
          </div>
        )}

        {/* Actions */}
        <div className="flex items-center gap-2 pt-1">
          <button
            onClick={handleSave}
            disabled={isSaving}
            className="rounded-md bg-pegasus-600 px-3 py-1.5 text-xs text-white hover:bg-pegasus-700 disabled:opacity-50"
          >
            {isSaving ? "Saving..." : "Save"}
          </button>

          <button
            onClick={handleTest}
            disabled={
              testing ||
              (!apiKey &&
                !config.has_api_key &&
                config.provider_id !== "ollama")
            }
            className={cn(
              "flex items-center gap-1 rounded-md border px-3 py-1.5 text-xs font-medium",
              testResult?.valid
                ? "border-green-300 bg-green-50 dark:bg-emerald-500/10 text-green-700 dark:text-emerald-400"
                : testResult && !testResult.valid
                  ? "border-red-300 bg-red-50 dark:bg-rose-500/10 text-red-700 dark:text-rose-400"
                  : "border-line text-fgmuted hover:bg-base",
              "disabled:opacity-50"
            )}
          >
            {testing ? (
              <>
                <span className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-current border-t-transparent" />
                Testing...
              </>
            ) : testResult?.valid ? (
              <>&#10003; Connected</>
            ) : testResult ? (
              <>&#10007; {testResult.error}</>
            ) : (
              "Test Connectivity"
            )}
          </button>

          {saved && (
            <span className="text-xs text-green-600">Saved</span>
          )}
        </div>
      </div>
    </div>
  );
}

/* ─── Tools Tab ─── */

function ToolsTab() {
  const { data, isLoading } = useTools();
  const addTab = useToolStore((s) => s.addTab);
  const setTerminalOpen = usePanelStore((s) => s.setTerminalOpen);

  const openBash = () => {
    addTab(null, "Terminal");
    setTerminalOpen(true);
  };

  const tools = data?.tools ?? [];

  return (
    <div className="space-y-5">
      <p className="text-sm text-fgmuted">
        Install, launch, and manage AI coding tools. Starting a terminal tool
        opens it in the terminal panel below.
      </p>

      {/* Bash terminal launcher (moved here from the old Workbench). */}
      <button
        onClick={openBash}
        className="flex w-full items-center gap-3 rounded-lg border border-line bg-surface p-3 text-left transition-colors hover:border-gray-400 sm:w-auto"
      >
        <span className="flex h-9 w-9 items-center justify-center rounded-md bg-gray-800 text-white">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="4 17 10 11 4 5" />
            <line x1="12" y1="19" x2="20" y2="19" />
          </svg>
        </span>
        <span>
          <span className="block text-sm font-medium text-fg">Bash Terminal</span>
          <span className="block text-xs text-fgmuted">Open a new shell session</span>
        </span>
      </button>

      {isLoading ? (
        <p className="text-sm text-fgsubtle">Loading tools...</p>
      ) : tools.length === 0 ? (
        <p className="text-sm text-fgmuted">
          No tools available. Check your tool registry configuration.
        </p>
      ) : (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {tools.map((tool) => (
            <ToolCard key={tool.info.id} tool={tool} />
          ))}
        </div>
      )}
    </div>
  );
}

/* ─── Pegasus Options Tab ─── */

/* ─── Chat Tab (engine selection) ─── */

function ChatTab() {
  const [status, setStatus] = useState<api.ChatEngineStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [switching, setSwitching] = useState(false);
  const [copied, setCopied] = useState(false);
  const [requireApproval, setRequireApproval] = useState<boolean | null>(null);
  const [approvalSaving, setApprovalSaving] = useState(false);

  useEffect(() => {
    api
      .getChatEngine()
      .then(setStatus)
      .catch((e) => setError(String(e)));
    api
      .getCommandApproval()
      .then((r) => setRequireApproval(r.require_approval))
      .catch(() => setRequireApproval(true));
  }, []);

  const toggleApproval = async (next: boolean) => {
    if (approvalSaving) return;
    setApprovalSaving(true);
    const prev = requireApproval;
    setRequireApproval(next); // optimistic
    try {
      const r = await api.updateCommandApproval(next);
      setRequireApproval(r.require_approval);
    } catch (e) {
      setRequireApproval(prev ?? true); // revert
      setError(String(e));
    } finally {
      setApprovalSaving(false);
    }
  };

  const select = async (engine: "builtin" | "opencode") => {
    if (!status || status.engine === engine || switching) return;
    setSwitching(true);
    setError(null);
    try {
      const res = await api.updateChatEngine(engine);
      setStatus(res);
      if (res.error) setError(res.error);
    } catch (e) {
      setError(String(e));
    } finally {
      setSwitching(false);
    }
  };

  const copyAttach = async () => {
    if (!status) return;
    try {
      await navigator.clipboard.writeText(status.opencode.attach_command);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard unavailable */
    }
  };

  const engineCard = (
    id: "builtin" | "opencode",
    title: string,
    description: string,
    note?: string
  ) => (
    <button
      onClick={() => select(id)}
      disabled={switching}
      className={cn(
        "flex-1 rounded-lg border p-4 text-left transition-colors",
        status?.engine === id
          ? "border-pegasus-600 bg-pegasus-50 dark:bg-pegasus-400/10"
          : "border-line bg-surface hover:border-pegasus-300"
      )}
    >
      <div className="flex items-center justify-between">
        <span className="text-sm font-semibold text-fg">{title}</span>
        {status?.engine === id && (
          <span className="rounded-full bg-pegasus-600 px-2 py-0.5 text-xs font-medium text-white">
            active
          </span>
        )}
      </div>
      <p className="mt-1 text-xs leading-relaxed text-fgmuted">{description}</p>
      {note && <p className="mt-1 text-xs text-fgsubtle">{note}</p>}
    </button>
  );

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-sm font-semibold text-fg">Chat engine</h2>
        <p className="text-sm text-fgmuted">
          The agent that powers the PegasusAI Chat panel. Switching engines
          keeps the same chat interface.
        </p>
      </div>

      {error && (
        <div className="rounded-md bg-red-50 p-3 text-sm text-red-700 dark:bg-rose-500/10 dark:text-rose-400">
          {error}
        </div>
      )}

      <div className="flex flex-col gap-3 sm:flex-row">
        {engineCard(
          "opencode",
          "OpenCode (default)",
          "A shared `opencode serve` runtime powers the panel: full agent with multi-step tool use, file edits, LSP and MCP support.",
          status && !status.opencode.installed
            ? "OpenCode is not installed — install it from the Tools tab first."
            : undefined
        )}
        {engineCard(
          "builtin",
          "Built-in",
          "Studio's own agent loop with workspace file and shell tools. Used automatically whenever OpenCode is unavailable."
        )}
      </div>

      {status?.engine === "opencode" && (
        <div className="rounded-lg border border-line bg-surface p-4">
          <div className="flex items-center gap-2">
            <span
              className={cn(
                "h-2 w-2 rounded-full",
                status.opencode.running ? "bg-emerald-500" : "bg-amber-500"
              )}
            />
            <span className="text-sm font-medium text-fg">
              {status.opencode.running
                ? `Server running at ${status.opencode.url}`
                : "Server starting…"}
            </span>
          </div>
          <p className="mt-2 text-xs leading-relaxed text-fgmuted">
            Terminals opened in Studio share this runtime — run the command
            below in any Studio terminal to open the same sessions in the
            OpenCode TUI. Chat and CLI stay in sync live.
          </p>
          <div className="mt-2 flex items-center gap-2">
            <code className="flex-1 overflow-x-auto whitespace-nowrap rounded bg-muted px-2 py-1.5 font-mono text-xs text-fg">
              {status.opencode.attach_command}
            </code>
            <button
              onClick={copyAttach}
              className="rounded-md border border-line px-2 py-1.5 text-xs font-medium text-fgmuted hover:bg-muted hover:text-fg"
            >
              {copied ? "Copied!" : "Copy"}
            </button>
          </div>
        </div>
      )}

      {/* Shell-command approval (SEC-4) */}
      <div className="rounded-lg border border-line bg-surface p-4">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h3 className="text-sm font-semibold text-fg">
              Confirm shell commands
            </h3>
            <p className="mt-1 text-xs leading-relaxed text-fgmuted">
              When on, chat pauses for your Approve/Deny before running any
              shell command it wants to execute. Turn off to let commands run
              automatically (the old behavior). File reads and writes inside
              your workspace are never gated.
            </p>
          </div>
          <button
            role="switch"
            aria-checked={requireApproval === true}
            disabled={requireApproval === null || approvalSaving}
            onClick={() => toggleApproval(!requireApproval)}
            className={cn(
              "relative mt-0.5 inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors disabled:opacity-50",
              requireApproval ? "bg-pegasus-600" : "bg-muted"
            )}
          >
            <span
              className={cn(
                "inline-block h-4 w-4 transform rounded-full bg-white transition-transform",
                requireApproval ? "translate-x-4" : "translate-x-0.5"
              )}
            />
          </button>
        </div>
      </div>
    </div>
  );
}

/* ─── Container registry (persisted; used by the Build Image action) ─── */

function RegistrySection() {
  const [registry, setRegistry] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [hasPassword, setHasPassword] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .getRegistrySettings()
      .then((r) => {
        setRegistry(r.registry);
        setUsername(r.username);
        setHasPassword(r.has_password);
      })
      .catch((e) => setError(String(e)));
  }, []);

  const handleSave = async () => {
    setError(null);
    try {
      const res = await api.updateRegistrySettings({
        registry,
        username,
        // Blank input keeps the stored token; typing overwrites it.
        ...(password !== "" ? { password } : {}),
      });
      setHasPassword(res.has_password);
      setPassword("");
      setSaved(true);
      setTimeout(() => setSaved(false), 3000);
    } catch (e) {
      setError(String(e));
    }
  };

  return (
    <div className="rounded-md border border-line p-4">
      <label className="block text-sm font-medium text-fg">
        Container Registry
      </label>
      <p className="mt-1 text-xs text-fgsubtle">
        Where the Build Image action pushes workflow images — workers pull
        from here at run time. Accepts docker:///user/, user, or
        registry.host/org.
      </p>
      <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-3">
        <label className="block text-xs font-medium text-fgmuted">
          Registry
          <input
            type="text"
            value={registry}
            onChange={(e) => setRegistry(e.target.value)}
            placeholder="e.g. docker:///kthare10/"
            className="mt-1 block w-full rounded-md border border-line px-3 py-2 text-sm text-fg shadow-sm focus:border-pegasus-500 focus:ring-pegasus-500"
          />
        </label>
        <label className="block text-xs font-medium text-fgmuted">
          Username
          <input
            type="text"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            placeholder="e.g. kthare10"
            autoComplete="off"
            className="mt-1 block w-full rounded-md border border-line px-3 py-2 text-sm text-fg shadow-sm focus:border-pegasus-500 focus:ring-pegasus-500"
          />
        </label>
        <label className="block text-xs font-medium text-fgmuted">
          Access token
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder={hasPassword ? "Stored — type to replace" : "Paste token"}
            autoComplete="new-password"
            className="mt-1 block w-full rounded-md border border-line px-3 py-2 text-sm text-fg shadow-sm focus:border-pegasus-500 focus:ring-pegasus-500"
          />
        </label>
      </div>
      <div className="mt-3 flex items-center gap-3">
        <button
          onClick={handleSave}
          className="rounded-md bg-pegasus-600 px-3 py-1.5 text-sm text-white hover:bg-pegasus-700"
        >
          Save Registry
        </button>
        {saved && (
          <span className="text-sm text-green-600 dark:text-emerald-400">
            Saved
          </span>
        )}
        {error && (
          <span className="text-sm text-red-600 dark:text-rose-400">
            {error}
          </span>
        )}
      </div>
    </div>
  );
}

function PegasusOptionsTab() {
  // Execution site, planning flags (--force/--submit/--cleanup) and the
  // Pegasus path used to live here as fields with a "Save" button, but nothing
  // consumed them — Plan/Run read the per-project parameter form, and the
  // "Saved" toast was a placebo (values reset on reload). They were removed
  // rather than left misleading (FE-7). The container registry below IS
  // persisted and drives the Build Image action.
  return (
    <div className="space-y-6">
      <p className="text-sm text-fgmuted">
        Per-run planning options — execution site, <code>--force</code>/
        <code>--submit</code>/<code>--cleanup</code>, and generator arguments —
        are set on each workflow&apos;s <strong>Plan</strong> /{" "}
        <strong>Run</strong> form, so they can differ per workflow. This tab
        holds the container registry used when building workflow images.
      </p>

      {/* Container registry — persisted; drives the Build Image action */}
      <RegistrySection />
    </div>
  );
}

/* ─── Notifications Tab ─── */

function NotificationsTab() {
  const [muted, setMuted] = useState<string[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .getNotificationPrefs()
      .then((p) => setMuted(p.muted))
      .catch((e) => setError((e as Error).message));
  }, []);

  const toggle = async (kind: string, enabled: boolean) => {
    if (muted === null) return;
    // "enabled" = not muted. Build the next muted list and persist.
    const next = enabled
      ? muted.filter((k) => k !== kind)
      : Array.from(new Set([...muted, kind]));
    const prev = muted;
    setMuted(next); // optimistic
    try {
      const saved = await api.updateNotificationPrefs(next);
      setMuted(saved.muted);
    } catch (e) {
      setMuted(prev); // revert on failure
      setError((e as Error).message);
    }
  };

  if (error) {
    return (
      <p className="text-sm text-red-600 dark:text-rose-400">
        Failed to load notification settings: {error}
      </p>
    );
  }

  if (muted === null) {
    return <p className="text-sm text-fgsubtle">Loading...</p>;
  }

  return (
    <div className="space-y-4">
      <p className="text-sm text-fgmuted">
        Choose which categories raise pop-up notifications. Muted categories are
        still recorded in the notification center (as already-read) but raise no
        toast and don&rsquo;t affect the unread badge.
      </p>

      <div className="divide-y divide-line rounded-lg border border-line bg-surface">
        {NOTIFICATION_CATEGORIES.map((cat) => {
          const enabled = !muted.includes(cat.kind);
          return (
            <div
              key={cat.kind}
              className="flex items-center justify-between gap-4 px-4 py-3"
            >
              <div className="min-w-0">
                <p className="text-sm font-medium text-fg">{cat.label}</p>
                <p className="text-xs text-fgmuted">{cat.description}</p>
              </div>
              <button
                role="switch"
                aria-checked={enabled}
                aria-label={`${enabled ? "Mute" : "Enable"} ${cat.label}`}
                onClick={() => toggle(cat.kind, !enabled)}
                className={cn(
                  "relative h-6 w-11 shrink-0 rounded-full transition-colors",
                  enabled ? "bg-pegasus-600" : "bg-muted"
                )}
              >
                <span
                  className={cn(
                    "absolute top-0.5 h-5 w-5 rounded-full bg-white shadow transition-transform",
                    enabled ? "translate-x-[1.375rem]" : "translate-x-0.5"
                  )}
                />
              </button>
            </div>
          );
        })}
      </div>
    </div>
  );
}
