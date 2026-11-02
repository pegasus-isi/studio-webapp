"use client";

import { useCallback, useEffect, useState } from "react";
import {
  useProviders,
  useProviderConfigs,
  useUpsertProviderConfig,
  useActivateProvider,
  useDeleteProviderConfig,
  useValidateProvider,
} from "@/lib/hooks/use-llm";
import { useTools } from "@/lib/hooks/use-tools";
import { useActionToast } from "@/lib/hooks/use-action-toast";
import {
  useUIPreferences,
  useUpdateUIPreferences,
} from "@/lib/hooks/use-ui-prefs";
import { announceEngineChange } from "@/lib/chat-engine-channel";
import { ToolCard } from "@/components/tools/tool-card";
import {
  CollectionNotice,
  ContentSharingExplainer,
  FeedbackPromptsToggle,
  ProfileForm,
} from "@/components/onboarding/feedback-consent";
import { useToolStore } from "@/lib/stores/tool-store";
import { usePanelStore } from "@/lib/stores/panel-store";
import { cn } from "@/lib/utils";
import type { ProviderConfig } from "@/lib/api/client";
import * as api from "@/lib/api/client";
import * as oc from "@/lib/opencode/client";

type Tab =
  | "providers"
  | "tools"
  | "chat"
  | "pegasus"
  | "notifications"
  | "feedback";

// What an ordinary user changes, versus what an operator does. Pegasus Options
// sits on the everyday side because it holds the Simplified Run toggle — the
// one switch a scientist is most likely to come here for.
const EVERYDAY_TABS: [Tab, string][] = [
  ["notifications", "Notifications"],
  ["pegasus", "Pegasus Options"],
  ["chat", "Chat"],
  ["feedback", "Feedback & privacy"],
];

const ADVANCED_TABS: [Tab, string][] = [
  ["providers", "LLM Providers"],
  ["tools", "Tools"],
];

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
  // Notifications first (#241, Phase 7). LLM providers, tool installs and
  // registry credentials are infrastructure; opening Settings on an API-key
  // form tells a scientist this product is not for them.
  const [activeTab, setActiveTab] = useState<Tab>("notifications");
  // ?tab=providers: chat links here when no AI provider is configured
  // (an OOD site without a gateway, #498). Read once, after mount, from the
  // real URL; useSearchParams would need a Suspense boundary for a value
  // that matters only on arrival.
  useEffect(() => {
    const wanted = new URLSearchParams(window.location.search).get("tab");
    const known = [...EVERYDAY_TABS, ...ADVANCED_TABS].some(([id]) => id === wanted);
    if (wanted && known) setActiveTab(wanted as Tab);
  }, []);

  return (
    <div className="mx-auto max-w-4xl p-6">
      <h1 className="text-lg font-semibold text-fg">Application Settings</h1>
      <p className="mt-1 text-sm text-fgmuted">
        Notifications, how workflows run, and the chat assistant. Provider keys
        and tool installs are under Advanced.
      </p>

      {/* Tab navigation.
          Ordered by who needs it (#241, Phase 7): the everyday preferences
          first, then a divider, then the infrastructure. Opening Settings on
          an LLM API-key form tells a scientist this product is not for them,
          which is the opposite of what a settings page should do. */}
      <div className="mt-4 flex flex-wrap items-center border-b border-line">
        {EVERYDAY_TABS.map(([id, label]) => (
          <button
            key={id}
            onClick={() => setActiveTab(id)}
            className={cn(
              "px-4 py-2 text-sm font-medium border-b-2 -mb-px",
              activeTab === id
                ? "border-pegasus-600 text-pegasus-600"
                : "border-transparent text-fgmuted hover:text-fg hover:border-line"
            )}
          >
            {label}
          </button>
        ))}

        <span
          aria-hidden="true"
          className="mx-2 h-4 w-px shrink-0 bg-line"
        />
        <span className="mr-1 text-2xs font-semibold uppercase tracking-wider text-fgsubtle">
          Advanced
        </span>

        {ADVANCED_TABS.map(([id, label]) => (
          <button
            key={id}
            onClick={() => setActiveTab(id)}
            className={cn(
              "px-4 py-2 text-sm font-medium border-b-2 -mb-px",
              activeTab === id
                ? "border-pegasus-600 text-pegasus-600"
                : "border-transparent text-fgmuted hover:text-fg hover:border-line"
            )}
          >
            {label}
          </button>
        ))}
      </div>

      {/* Tab content */}
      <div className="mt-6">
        {activeTab === "providers" && <ProvidersTab />}
        {activeTab === "tools" && <ToolsTab />}
        {activeTab === "chat" && <ChatTab />}
        {activeTab === "pegasus" && <PegasusOptionsTab />}
        {activeTab === "notifications" && <NotificationsTab />}
        {activeTab === "feedback" && <FeedbackTab />}
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
  const runAction = useActionToast();

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
    const result = await runAction("Add provider", () =>
      upsertConfig.mutateAsync({
        provider_id,
        name: isCustom
          ? `Custom Endpoint${customCount ? " " + (customCount + 1) : ""}`
          : preset?.name || newProviderId,
        base_url: isCustom ? "" : preset?.base_url || "",
        default_model: isCustom ? "" : preset?.default_model || "",
        api_key: "",
        // A custom endpoint starts empty and is configured in its card, so it
        // cannot be the active provider yet — activating one with no base URL
        // would point chat at an endpoint that cannot answer. The backend
        // enforces this too; sending it correctly keeps the UI honest.
        is_active: savedConfigs.length === 0 && !isCustom,
      })
    );
    if (!result) return; // failure already surfaced as a toast
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
          onSave={async (data) =>
            (await runAction("Save provider", () =>
              upsertConfig.mutateAsync(data)
            )) !== undefined
          }
          onActivate={async () => {
            await runAction("Activate provider", () =>
              activateProvider.mutateAsync(config.provider_id)
            );
          }}
          onDelete={async () => {
            await runAction("Delete provider", () =>
              deleteConfig.mutateAsync(config.provider_id)
            );
          }}
          isSaving={upsertConfig.isPending}
        />
      ))}

      {/* Add provider */}
      {showAdd ? (
        <div className="rounded-xl border border-dashed border-line bg-base p-4">
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
          className="w-full rounded-lg border border-dashed border-line py-3 text-sm text-fgmuted hover:border-pegasus-300 hover:text-fg disabled:opacity-50"
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
  }) => Promise<boolean>; // resolves true on success (gates the "Saved" note)
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
  // Saved but not yet usable: a custom endpoint is created empty and configured
  // here. Without this it looks like any other provider while quietly refusing
  // to activate.
  const incomplete = needsBaseUrl && !config.base_url;

  const handleSave = async () => {
    // Only show "Saved" if the save actually succeeded (FE-6) — onSave
    // resolves false and toasts on failure.
    const ok = await onSave({
      provider_id: config.provider_id,
      name: name || config.name,
      api_key: apiKey,
      base_url: baseUrl,
      default_model: model,
      is_active: config.is_active,
    });
    if (!ok) return;
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
        "rounded-xl border bg-surface p-4",
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

      {incomplete && (
        <p className="mt-2 rounded-md bg-amber-50 px-3 py-2 text-xs text-amber-700 dark:bg-amber-950 dark:text-amber-300">
          Set a base URL below and save before this endpoint can be activated.
        </p>
      )}

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
            Model <span className="font-normal text-fgsubtle">(optional)</span>
          </label>
          {/* Optional on purpose: the chat's model picker lists everything the
              provider offers, so this only sets which one it starts on. */}
          <input
            type="text"
            value={model}
            onChange={(e) => setModel(e.target.value)}
            placeholder={
              preset?.default_model || "leave blank — pick a model in chat"
            }
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
                ? "border-emerald-300 bg-emerald-50 dark:border-emerald-500/40 dark:bg-emerald-500/10 text-emerald-700 dark:text-emerald-400"
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
            <span className="text-xs text-emerald-700 dark:text-emerald-400">Saved</span>
          )}
        </div>
      </div>
    </div>
  );
}

/* ─── Tools Tab ─── */

/* ─── Knowledge base (marketplace checkout) ─── */

/**
 * The checkout is a git working copy in the user's $HOME, so its version is a
 * commit SHA rather than a package number. Showing it is the only way to tell
 * which skills a session is actually running; Update fast-forwards it, and
 * declines rather than discards when there are local changes.
 */
function KnowledgeSection() {
  const [status, setStatus] = useState<api.MarketplaceStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  const load = async () => {
    try {
      setStatus(await api.getMarketplaceStatus());
    } catch {
      setStatus(null);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const onUpdate = async () => {
    setBusy(true);
    setNote(null);
    try {
      const next = await api.updateMarketplace();
      setStatus(next);
      setNote(
        next.updated
          ? `Updated to ${next.short_sha}`
          : `No change${next.reason ? ` — ${next.reason}` : " — already current"}`
      );
    } catch (e) {
      setNote(e instanceof Error ? e.message : "Update failed");
    } finally {
      setBusy(false);
    }
  };

  if (!status?.exists) return null;

  return (
    <div className="rounded-xl border border-line bg-surface p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-sm font-semibold text-fg">Pegasus knowledge base</h2>
          <p className="mt-1 text-xs text-fgmuted">
            <span className="tabular-nums">{status.skills}</span> skills shared by every AI tool.
          </p>
          <p className="mt-1 break-all font-mono text-xs text-fgsubtle">
            {status.path}
          </p>
          {status.short_sha && (
            <p className="mt-1 text-xs text-fgmuted">
              <span className="font-mono">{status.short_sha}</span>
              {status.subject ? ` — ${status.subject}` : ""}
            </p>
          )}
          {(status.dirty || status.ahead > 0) && (
            <p className="mt-1 text-xs text-amber-600">
              {status.dirty
                ? "Local edits — updates are declined so they are not lost."
                : `${status.ahead} local commit(s) — updates are declined.`}
            </p>
          )}
        </div>
        {status.is_git && (
          <button
            onClick={onUpdate}
            disabled={busy}
            className="shrink-0 rounded-md border border-line px-3 py-1.5 text-sm text-fg transition-colors hover:border-pegasus-300 disabled:opacity-50"
          >
            {busy ? "Updating..." : "Update"}
          </button>
        )}
      </div>
      {note && <p className="mt-2 text-xs text-fgmuted">{note}</p>}
    </div>
  );
}

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
        className="flex w-full items-center gap-3 rounded-xl border border-line bg-surface p-3 text-left transition-colors hover:border-pegasus-300 sm:w-auto"
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

      <KnowledgeSection />

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

/* ─── Chat Tab ─── */

function ChatTab() {
  const [error, setError] = useState<string | null>(null);
  const [requireApproval, setRequireApproval] = useState<boolean | null>(null);
  const [approvalSaving, setApprovalSaving] = useState(false);

  useEffect(() => {
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

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-sm font-semibold text-fg">Chat</h2>
        <p className="text-sm text-fgmuted">
          Options for the PegasusAI Chat panel.
        </p>
      </div>

      {error && (
        <div className="rounded-md bg-red-50 p-3 text-sm text-red-700 dark:bg-rose-500/10 dark:text-rose-400">
          {error}
        </div>
      )}

      <ChatEngineSection />

      {/* Shell-command approval (SEC-4) */}
      <div className="rounded-xl border border-line bg-surface p-4">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h3 className="text-sm font-semibold text-fg">
              Confirm shell commands
            </h3>
            <p className="mt-1 text-xs leading-relaxed text-fgmuted">
              When on, chat pauses for your Approve/Deny before running any
              shell command it wants to execute. Turn off to let commands run
              automatically (the old behavior). While it is on, chat also asks
              before writing outside ~/workflows and ~/data. Reads are never
              gated.
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

      <TelemetrySection />
    </div>
  );
}

/* ─── Telemetry consent (#367 step 6) ─── */

/**
 * Whether this session's prompts may be exported with its telemetry.
 *
 * Two things are worth being precise about in the copy, because the difference
 * is the whole point of the control. Activity counters (how many turns, how
 * many tokens, how long tools took) are collected wherever a collector is
 * configured and contain nothing the user typed. Prompt and reply text is
 * exported only with this switch on, and the reason it is a switch rather than
 * a build-time decision is that the engine's tracing carries the prompt in the
 * span itself — there is no "traces without content" setting to hide behind.
 *
 * Its own endpoint rather than a UI preference: `PUT /api/settings/ui`
 * replaces preferences wholesale, and a consent flag that an unrelated toggle
 * could revert from a stale cache is not consent.
 */
function TelemetrySection() {
  const [consent, setConsent] = useState<api.TelemetryConsent | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .getTelemetryConsent()
      .then(setConsent)
      // Unknown state reads as "off": the control must never imply content is
      // being withheld when it is, nor invite consent it cannot record.
      .catch(() =>
        setConsent({
          content_capture: false,
          collector_configured: false,
          decided: false,
        })
      );
  }, []);

  const toggle = async (next: boolean) => {
    if (saving || consent === null) return;
    setSaving(true);
    const prev = consent;
    setConsent({ ...consent, content_capture: next });
    setError(null);
    try {
      setConsent(await api.updateTelemetryConsent(next));
    } catch (e) {
      setConsent(prev);
      setError(String(e));
    } finally {
      setSaving(false);
    }
  };

  const on = consent?.content_capture === true;
  const collecting = consent?.collector_configured === true;

  return (
    <div className="rounded-xl border border-line bg-surface p-4">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h3 className="text-sm font-semibold text-fg">
            Share chat text for troubleshooting
          </h3>
          <p className="mt-1 text-xs leading-relaxed text-fgmuted">
            {collecting ? (
              <>
                <ContentSharingExplainer /> You can turn it off again at any
                time; the chat engine restarts when you do.
              </>
            ) : (
              <>
                This Studio has no telemetry collector configured, so nothing
                is being sent anywhere — not usage counts and not chat text.
                This setting has no effect here.
              </>
            )}
          </p>
        </div>
        <button
          role="switch"
          aria-checked={on}
          aria-label="Share chat text for troubleshooting"
          disabled={consent === null || saving || !collecting}
          onClick={() => toggle(!on)}
          className={cn(
            "relative mt-0.5 inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors disabled:opacity-50",
            on ? "bg-pegasus-600" : "bg-muted"
          )}
        >
          <span
            className={cn(
              "inline-block h-4 w-4 transform rounded-full bg-white transition-transform",
              on ? "translate-x-4" : "translate-x-0.5"
            )}
          />
        </button>
      </div>

      {error && (
        <p className="mt-2 text-xs text-red-700 dark:text-rose-400">{error}</p>
      )}
    </div>
  );
}

/* ─── Feedback & privacy (early-user feedback program) ─── */

/**
 * The permanent home of what the first-run tour said once: what is recorded,
 * the switch for the feedback prompts, the profile answers, and the chat-text
 * consent. The consent control is the same component the Chat tab shows,
 * repeated here because the notice points "below" at it — a privacy notice
 * whose controls live on another tab is one most people never act on.
 */
function FeedbackTab() {
  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-sm font-semibold text-fg">Feedback &amp; privacy</h2>
        <p className="text-sm text-fgmuted">
          What Studio records to improve itself, and how to turn it off.
        </p>
      </div>

      <div className="rounded-xl border border-line bg-surface p-4">
        <h3 className="text-sm font-semibold text-fg">What we collect</h3>
        <CollectionNotice where="settings" className="mt-1" />
      </div>

      <div className="rounded-xl border border-line bg-surface p-4">
        <FeedbackPromptsToggle />
      </div>

      <div className="rounded-xl border border-line bg-surface p-4">
        <h3 className="text-sm font-semibold text-fg">About you</h3>
        <p className="mt-1 mb-3 text-xs leading-relaxed text-fgmuted">
          Optional. These let us tell apart, say, a first-year student from
          someone who has run HTCondor for years when we read the feedback.
          Leave any of them blank.
        </p>
        <ProfileForm />
      </div>

      <TelemetrySection />
    </div>
  );
}

/* ─── Chat engine (#367) ─── */

/**
 * Which agent loop answers the chat.
 *
 * Opt-in, and the builtin loop stays the default, because this replaces the
 * engine under an existing conversation rather than adding a feature beside
 * it: everything a user has learned about how the panel behaves is about the
 * builtin loop. The switch is per user and takes effect on the next render of
 * the panel, so trying it and going back costs a toggle rather than a
 * rollback.
 */
function ChatEngineSection() {
  const { prefs } = useUIPreferences();
  const update = useUpdateUIPreferences();
  const [status, setStatus] = useState<oc.EngineStatus | null>(null);
  const [restarting, setRestarting] = useState(false);

  const refresh = useCallback(() => {
    oc.getEngineStatus()
      .then(setStatus)
      .catch(() =>
        setStatus({ running: false, reason: "unreachable", detail: null })
      );
  }, []);

  useEffect(refresh, [refresh]);

  const restart = async () => {
    setRestarting(true);
    try {
      await oc.restartEngine();
      // Give supervisord a moment to bring it back before asking, so the line
      // doesn't read "not listening" for the restart it was just asked for.
      await new Promise((resolve) => setTimeout(resolve, 1500));
      refresh();
    } finally {
      setRestarting(false);
    }
  };

  const onOpenCode = prefs.chat_engine === "opencode";

  return (
    <div className="rounded-xl border border-line bg-surface p-4">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h3 className="text-sm font-semibold text-fg">Chat engine</h3>
          <p className="mt-1 text-xs leading-relaxed text-fgmuted">
            OpenCode runs the conversation inside your session: it summarizes
            older context instead of forgetting it, remembers across chats, and
            can use subagents, MCP servers and skills. The built-in engine is
            the simpler loop Studio has always used.
          </p>
        </div>
        <select
          value={prefs.chat_engine}
          disabled={update.isPending}
          onChange={(e) => {
            update.mutate(
              { chat_engine: e.target.value as "builtin" | "opencode" },
              {
                // Announced only once the save has landed. Another document
                // responds by refetching the preference, so announcing first
                // raced the PUT: it could read the old value and then never
                // hear again, leaving the notebook chat on the engine the user
                // had just switched away from. This document doesn't wait —
                // its cache is updated optimistically by the hook.
                onSuccess: () => announceEngineChange(),
              }
            );
          }}
          className="mt-0.5 shrink-0 rounded border border-line px-2 py-1 text-xs text-fg focus:border-pegasus-500 focus:ring-pegasus-500 disabled:opacity-50"
        >
          {/* Default first (#403). */}
          <option value="opencode">OpenCode</option>
          <option value="builtin">Built-in</option>
        </select>
      </div>

      {onOpenCode && (
        <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-line pt-3 text-xs">
          <span className="text-fgmuted">Engine:</span>
          {status === null ? (
            <span className="text-fgsubtle">checking…</span>
          ) : status.running ? (
            <span className="text-emerald-600 dark:text-emerald-400">
              running{status.version ? ` (${status.version})` : ""}
            </span>
          ) : (
            <span className="text-amber-600 dark:text-amber-400">
              {status.detail || status.reason || "not running"}
            </span>
          )}
          <button
            onClick={restart}
            disabled={restarting}
            title="Restart the OpenCode server in this session"
            className="ml-auto rounded-md border border-line px-2.5 py-1 font-medium text-fgmuted hover:bg-muted disabled:opacity-50"
          >
            {restarting ? "Restarting…" : "Restart"}
          </button>
        </div>
      )}

      {update.isError && (
        <p className="mt-2 text-xs text-rose-600 dark:text-rose-400">
          Could not save that preference. Try again.
        </p>
      )}
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
          <span className="text-sm text-emerald-700 dark:text-emerald-400">
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
        holds the settings that apply to every workflow: where planning runs,
        the API keys your analyses need to download data, and the container
        registry used when building workflow images.
      </p>

      {/* Simplified Run flow — the Phase 1 rollout flag (#241) */}
      <SimpleRunSection />

      {/* Whether the app root shows the landing page or goes to Home (#370) */}
      <TourSection />

      {/* Where pegasus-plan runs — remote by default when the schedd is
          elsewhere, overridable to test local planning */}
      <PlanningLocationSection />

      {/* API keys a workflow's generator needs (#364) */}
      <WorkflowSecretsSection />

      {/* Container registry — persisted; drives the Build Image action */}
      <RegistrySection />

      {/* Where runs are looked for — extra scan roots / excludes (#306) */}
      <WorkflowDiscoverySection />
    </div>
  );
}

/**
 * Where `pegasus-plan` runs.
 *
 * Planning happens on the submit host when the schedd is on another machine,
 * because the planner writes its own absolute paths into the submit files and
 * Pegasus executes those on the schedd host (#334). That is the right default
 * and stays the default.
 *
 * The override exists because whether local planning works is not a question
 * anyone can answer by reading: it depends on the two machines agreeing about
 * Pegasus, which changed when the image moved to Debian trixie. Before this it
 * meant an env var in the spawner profile and a pod restart, which is not
 * something a user can do to their own session.
 */
function PlanningLocationSection() {
  const [settings, setSettings] = useState<api.PlanningSettings | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .getPlanningSettings()
      .then(setSettings)
      .catch((e) => setError((e as Error).message || String(e)));
  }, []);

  const choose = async (mode: api.PlanMode) => {
    if (saving || settings?.mode === mode) return;
    setSaving(true);
    setError(null);
    try {
      setSettings(await api.updatePlanningSettings(mode));
    } catch (e) {
      setError((e as Error).message || String(e));
    } finally {
      setSaving(false);
    }
  };

  const OPTIONS: { value: api.PlanMode; label: string; hint: string }[] = [
    {
      value: "auto",
      label: "Automatic (recommended)",
      hint:
        "Plan on the submit host when it is a different machine, here when it " +
        "is not. This is what Pegasus expects.",
    },
    {
      value: "remote",
      label: "Always on the submit host",
      hint:
        "Plan through HTCondor on the machine that runs your workflows, whatever " +
        "detection says.",
    },
    {
      value: "local",
      label: "Always in this workspace",
      hint:
        "Plan here. Use this to check whether local planning works against your " +
        "submit host — it only does when both have Pegasus in the same place.",
    },
  ];

  return (
    <div className="rounded-xl border border-line bg-surface p-4">
      <h2 className="text-sm font-semibold text-fg">Where planning runs</h2>
      <p className="mt-1 text-xs leading-relaxed text-fgmuted">
        Preparing a workflow to run (<code>pegasus-plan</code>) writes file paths
        into the instructions your submit host executes, so it normally happens
        on that host. Override it here to try planning in this workspace
        instead. Nothing else about your workflows changes.
      </p>

      {error && (
        <p className="mt-2 text-xs text-red-600 dark:text-rose-400">{error}</p>
      )}

      <div className="mt-3 space-y-2">
        {OPTIONS.map((o) => {
          const selected = settings?.mode === o.value;
          return (
            <label
              key={o.value}
              className={cn(
                "flex cursor-pointer items-start gap-3 rounded-xl border p-3 transition-colors",
                selected
                  ? "border-pegasus-400 bg-pegasus-50 dark:bg-pegasus-400/10"
                  : "border-line hover:bg-base",
                (!settings || saving) && "cursor-wait opacity-70"
              )}
            >
              <input
                type="radio"
                name="plan-mode"
                className="mt-0.5"
                value={o.value}
                checked={selected}
                disabled={!settings || saving}
                onChange={() => choose(o.value)}
              />
              <span>
                <span className="block text-sm font-medium text-fg">
                  {o.label}
                </span>
                <span className="block text-xs text-fgmuted">{o.hint}</span>
              </span>
            </label>
          );
        })}
      </div>

      {/* What the next run will actually do. With "Automatic" the answer is a
          detection result, and without showing it the setting is a switch you
          have to experiment with to understand. */}
      {settings && (
        <p className="mt-3 text-xs text-fgsubtle">
          Your next run will plan{" "}
          <strong className="text-fgmuted">
            {settings.effective === "remote"
              ? "on the submit host"
              : "in this workspace"}
          </strong>
          {settings.source === "detected" &&
            (settings.submit_host_is_remote
              ? " — the submit host is a different machine."
              : " — the submit host is this machine.")}
          {settings.source === "setting" && " — because you chose it above."}
          {settings.source === "env" &&
            ` — set for this deployment (STUDIO_PLAN_ON_SUBMIT_HOST=${settings.env_default}).`}
          {settings.source === "no-condor" &&
            " — HTCondor isn't available here to send the work anywhere else."}
          {settings.source === "fallback" &&
            " — the submit host could not be checked."}
        </p>
      )}

      {settings?.effective === "local" &&
        settings.submit_host_is_remote === true && (
          <p className="mt-2 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-500/40 dark:bg-amber-500/10 dark:text-amber-300">
            Your submit host is a different machine, so this only works if it
            has Pegasus installed in the same place as this workspace. If they
            differ, the run stops before doing any work and says so — it will
            not fail halfway through.
          </p>
        )}
    </div>
  );
}

/**
 * Credentials a workflow's generator reads from its environment (#364).
 *
 * A generator gets a key to its jobs by reading it here and baking it in with
 * `add_env` — HTCondor gives jobs a clean environment, so nothing else reaches
 * them. That environment belongs to the server inside the pod, which is why a
 * workflow needing an API key could not run in Studio at all: the only place to
 * set one was the spawner profile, followed by a pod restart.
 *
 * Values are write-only. Once saved, the name and the date are all anyone can
 * read back — including this UI.
 */
function WorkflowSecretsSection() {
  const [settings, setSettings] =
    useState<api.WorkflowSecretsSettings | null>(null);
  const [name, setName] = useState("");
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);

  useEffect(() => {
    api
      .getWorkflowSecrets()
      .then(setSettings)
      .catch((e) => setError((e as Error).message || String(e)));
  }, []);

  const save = async () => {
    if (!name.trim() || !value || busy) return;
    setBusy(true);
    setError(null);
    try {
      setSettings(await api.setWorkflowSecret(name.trim(), value));
      setSaved(name.trim().toUpperCase());
      setName("");
      setValue("");
      setTimeout(() => setSaved(null), 2500);
    } catch (e) {
      setError((e as Error).message || String(e));
    } finally {
      setBusy(false);
    }
  };

  const remove = async (secretName: string) => {
    setBusy(true);
    setError(null);
    try {
      setSettings(await api.deleteWorkflowSecret(secretName));
    } catch (e) {
      setError((e as Error).message || String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="rounded-xl border border-line bg-surface p-4">
      <h2 className="text-sm font-semibold text-fg">Workflow secrets</h2>
      <p className="mt-1 text-xs leading-relaxed text-fgmuted">
        API keys and tokens your analyses need to download data — a Kaggle key,
        a data portal token. Each one is set as an environment variable when
        Studio prepares a run, which is how the value reaches the steps that
        need it. Use the exact name the workflow expects, for example{" "}
        <code>KAGGLE_KEY</code>.
      </p>

      {error && (
        <p className="mt-2 text-xs text-red-600 dark:text-rose-400">{error}</p>
      )}

      {settings && settings.secrets.length > 0 && (
        <ul className="mt-3 divide-y divide-line rounded-md border border-line">
          {settings.secrets.map((s) => (
            <li
              key={s.name}
              className="flex items-center justify-between gap-3 px-3 py-2"
            >
              <span className="min-w-0">
                <code className="text-sm text-fg">{s.name}</code>
                {/* No value, ever — saying so is clearer than an empty gap
                    where a masked value would be. */}
                <span className="ml-2 text-xs text-fgsubtle">
                  set {new Date(s.updated_at).toLocaleDateString()} · value
                  hidden
                </span>
              </span>
              <button
                onClick={() => remove(s.name)}
                disabled={busy}
                className="shrink-0 rounded border border-line px-2 py-1 text-xs text-fgmuted hover:border-red-400 hover:text-red-600 disabled:opacity-50 dark:hover:text-red-400"
              >
                Remove
              </button>
            </li>
          ))}
        </ul>
      )}

      <div className="mt-3 flex flex-wrap items-end gap-2">
        <label className="flex flex-col gap-1">
          <span className="text-xs font-medium text-fgmuted">Name</span>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="KAGGLE_KEY"
            spellCheck={false}
            className="w-48 rounded-md border border-line bg-surface px-2 py-1 font-mono text-sm text-fg"
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-xs font-medium text-fgmuted">Value</span>
          <input
            type="password"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            placeholder="paste the key"
            spellCheck={false}
            autoComplete="off"
            className="w-64 rounded-md border border-line bg-surface px-2 py-1 text-sm text-fg"
          />
        </label>
        <button
          onClick={save}
          disabled={busy || !name.trim() || !value}
          className="rounded-md bg-pegasus-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-pegasus-700 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {busy ? "Saving…" : "Save"}
        </button>
        {saved && (
          <span className="text-xs text-emerald-700 dark:text-emerald-400">
            {saved} saved
          </span>
        )}
      </div>

      {/* secretbox is fail-open: with no master key configured the values are
          stored in plaintext on the volume. The user is the one deciding what
          to paste in, so they get told which it is. */}
      {settings && !settings.encrypted_at_rest && (
        <p className="mt-3 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-500/40 dark:bg-amber-500/10 dark:text-amber-300">
          These are stored unencrypted in this workspace, because no encryption
          key is configured for this deployment. They are as private as your
          home directory — fine for a personal data-portal token, worth asking
          your administrator about for anything more valuable.
        </p>
      )}
    </div>
  );
}

/**
 * User additions to run discovery (#306). Runs are found by scanning for
 * `braindump.yml` under the deployment's scan root; a run planned onto a
 * mounted volume outside it is invisible, and a huge subtree slows every
 * listing. Both lists here *extend* the deployment defaults — a user can't
 * configure themselves into an empty Workflows page.
 */
function WorkflowDiscoverySection() {
  const [settings, setSettings] =
    useState<api.WorkflowScanSettings | null>(null);
  const [rootsText, setRootsText] = useState("");
  const [excludesText, setExcludesText] = useState("");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .getWorkflowScanSettings()
      .then((s) => {
        setSettings(s);
        setRootsText(s.roots.join("\n"));
        setExcludesText(s.excludes.join("\n"));
      })
      .catch((e) => setError(String(e)));
  }, []);

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      const res = await api.updateWorkflowScanSettings({
        roots: rootsText.split("\n").map((l) => l.trim()).filter(Boolean),
        excludes: excludesText.split("\n").map((l) => l.trim()).filter(Boolean),
      });
      setSettings(res);
      setRootsText(res.roots.join("\n"));
      setExcludesText(res.excludes.join("\n"));
      setSaved(true);
      setTimeout(() => setSaved(false), 2500);
    } catch (e) {
      setError((e as Error).message || String(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="rounded-xl border border-line bg-surface p-4">
      <h2 className="text-sm font-semibold text-fg">Workflow discovery</h2>
      <p className="mt-1 text-xs leading-relaxed text-fgmuted">
        Runs are found by scanning for <code>braindump.yml</code> under{" "}
        <code>{settings?.defaults.scan_root ?? "your home directory"}</code>;
        workflow projects live in{" "}
        <code>{settings?.defaults.workflows_dir ?? "~/workflows"}</code>.
        Add directories below to also scan them (runs on a mounted volume),
        or path fragments to skip (a large data directory slowing the list).
        Your additions extend the defaults — they never replace them.
      </p>

      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        <div>
          <label className="text-xs font-medium text-fgmuted">
            Extra scan directories{" "}
            <span className="font-normal text-fgsubtle">
              (absolute paths, one per line)
            </span>
          </label>
          <textarea
            value={rootsText}
            onChange={(e) => setRootsText(e.target.value)}
            rows={3}
            placeholder={"/mnt/scratch\n/data/shared-runs"}
            spellCheck={false}
            className="mt-1 w-full rounded-md border border-line bg-base px-2 py-1.5 font-mono text-xs text-fg focus:border-pegasus-500 focus:ring-pegasus-500"
            data-testid="workflow-scan-roots"
          />
        </div>
        <div>
          <label className="text-xs font-medium text-fgmuted">
            Exclude path fragments{" "}
            <span className="font-normal text-fgsubtle">(one per line)</span>
          </label>
          <textarea
            value={excludesText}
            onChange={(e) => setExcludesText(e.target.value)}
            rows={3}
            placeholder={"big-dataset\narchive/"}
            spellCheck={false}
            className="mt-1 w-full rounded-md border border-line bg-base px-2 py-1.5 font-mono text-xs text-fg focus:border-pegasus-500 focus:ring-pegasus-500"
            data-testid="workflow-scan-excludes"
          />
          <p className="mt-1 text-xs text-fgsubtle">
            Runs whose directory path contains a fragment are hidden. Built-in:{" "}
            {settings?.defaults.builtin_excludes
              .map((f) => f.replace(/^\/+|\/+$/g, ""))
              .join(", ") ?? "…"}
            .
          </p>
        </div>
      </div>

      {settings && settings.missing_roots.length > 0 && (
        <p className="mt-2 text-xs text-amber-600 dark:text-amber-400">
          Not found right now (scanned once they appear):{" "}
          {settings.missing_roots.join(", ")}
        </p>
      )}
      {error && (
        <p className="mt-2 text-xs text-rose-600 dark:text-rose-400">{error}</p>
      )}

      <div className="mt-3 flex items-center gap-2">
        <button
          onClick={handleSave}
          disabled={saving || settings === null}
          className="rounded-md bg-pegasus-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-pegasus-700 disabled:opacity-50"
          data-testid="workflow-scan-save"
        >
          {saving ? "Saving…" : "Save"}
        </button>
        {saved && (
          <span className="text-xs text-emerald-600 dark:text-emerald-400">
            Saved — Analyses uses this on its next refresh.
          </span>
        )}
      </div>
    </div>
  );
}

/**
 * Opt-*out* of the unified Run flow (#241, Phase 1).
 *
 * On by default: the single Run action is the product, and the four Pegasus
 * verbs are what you turn it back to. This stays a per-user preference rather
 * than disappearing because the flow's vocabulary has not yet been through the
 * terminology check, so "turn it off" needs to remain a one-click answer for
 * anyone it doesn't suit.
 */
function SimpleRunSection() {
  const { prefs } = useUIPreferences();
  const update = useUpdateUIPreferences();

  return (
    <div className="rounded-xl border border-line bg-surface p-4">
      <h2 className="text-sm font-semibold text-fg">Simplified Run</h2>
      <p className="mt-1 text-xs text-fgmuted">
        A single <strong>Run</strong> button prepares and starts your workflow,
        doing the Generate, Build Image, Plan and Run steps in order and showing
        you the progress. The individual steps stay available under
        &ldquo;Advanced&rdquo; on each workflow. Turn this off to go back to
        driving the four steps yourself.
      </p>
      <label className="mt-3 flex cursor-pointer items-center gap-2 text-sm text-fg">
        <input
          type="checkbox"
          checked={prefs.simple_run}
          disabled={update.isPending}
          onChange={(e) =>
            update.mutate({ simple_run: e.target.checked })
          }
        />
        Use the simplified Run flow
      </label>
      {update.isError && (
        <p className="mt-2 text-xs text-red-600 dark:text-rose-400">
          Couldn&apos;t save: {(update.error as Error).message}
        </p>
      )}
    </div>
  );
}

/**
 * Whether Home runs the guided tour at each login. (This section also held
 * "skip the welcome page" until that page's content moved to the login page.)
 */
function TourSection() {
  const { prefs } = useUIPreferences();
  const update = useUpdateUIPreferences();

  return (
    <div className="rounded-xl border border-line bg-surface p-4">
      <h2 className="text-sm font-semibold text-fg">Guided tour</h2>
      <p className="mt-1 text-xs text-fgmuted">
        Home runs a short guided tour each time you log in. Turn it off here;
        &ldquo;Take a tour&rdquo; in the account menu always runs it.
      </p>
      <label className="mt-3 flex cursor-pointer items-center gap-2 text-sm text-fg">
        <input
          type="checkbox"
          checked={prefs.tour_on_login}
          disabled={update.isPending}
          onChange={(e) =>
            update.mutate({ tour_on_login: e.target.checked })
          }
        />
        Show the guided tour each time I log in
      </label>
      {update.isError && (
        <p className="mt-2 text-xs text-red-600 dark:text-rose-400">
          Couldn&apos;t save: {(update.error as Error).message}
        </p>
      )}
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

      <div className="divide-y divide-line rounded-xl border border-line bg-surface">
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
