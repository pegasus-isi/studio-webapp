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
import { cn } from "@/lib/utils";
import type { ProviderConfig } from "@/lib/api/client";

type Tab = "providers" | "tools" | "pegasus";

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
      </div>

      {/* Tab content */}
      <div className="mt-6">
        {activeTab === "providers" && <ProvidersTab />}
        {activeTab === "tools" && <ToolsTab />}
        {activeTab === "pegasus" && <PegasusOptionsTab />}
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
    await upsertConfig.mutateAsync({
      provider_id: newProviderId,
      name: preset?.name || newProviderId,
      base_url: preset?.base_url || "",
      default_model: preset?.default_model || "",
      api_key: "",
      is_active: savedConfigs.length === 0,
    });
    setNewProviderId("");
    setShowAdd(false);
  };

  // Providers that haven't been added yet
  const availablePresets = presets.filter(
    (p) => !savedConfigs.some((c) => c.provider_id === p.id)
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

  const [apiKey, setApiKey] = useState(config.api_key || "");
  const [model, setModel] = useState(config.default_model || "");
  const [baseUrl, setBaseUrl] = useState(config.base_url || "");
  const [saved, setSaved] = useState(false);

  // Sync if config changes externally
  useEffect(() => {
    setApiKey(config.api_key || "");
    setModel(config.default_model || "");
    setBaseUrl(config.base_url || "");
  }, [config.api_key, config.default_model, config.base_url]);

  const preset = presets.find((p) => p.id === config.provider_id);
  const needsBaseUrl =
    config.provider_id === "custom" || config.provider_id === "ollama";

  const handleSave = async () => {
    await onSave({
      provider_id: config.provider_id,
      name: config.name,
      api_key: apiKey,
      base_url: baseUrl,
      default_model: model,
      is_active: config.is_active,
    });
    setSaved(true);
    setTimeout(() => setSaved(false), 3000);
  };

  const handleTest = async () => {
    await validate.mutateAsync({
      provider: config.provider_id,
      api_key: apiKey,
      base_url: baseUrl || preset?.base_url || undefined,
    });
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
            placeholder="Enter API key..."
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
            placeholder={preset?.default_model || "model name"}
            className="mt-1 block w-full rounded-md border border-line px-3 py-1.5 text-sm shadow-sm focus:border-pegasus-500 focus:ring-pegasus-500"
          />
          {/* Show model chips from validation */}
          {validate.data?.valid && validate.data.models.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1">
              {validate.data.models.slice(0, 10).map((m) => (
                <button
                  key={m}
                  onClick={() => setModel(m)}
                  className={cn(
                    "rounded-full border px-2 py-0.5 text-xs",
                    m === model
                      ? "border-pegasus-500 bg-pegasus-50 dark:bg-pegasus-400/10 text-pegasus-700 dark:text-pegasus-300"
                      : "border-line text-fgmuted hover:bg-base"
                  )}
                >
                  {m}
                </button>
              ))}
            </div>
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
              placeholder="https://..."
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
            disabled={validate.isPending || (!apiKey && config.provider_id !== "ollama")}
            className={cn(
              "flex items-center gap-1 rounded-md border px-3 py-1.5 text-xs font-medium",
              validate.data?.valid
                ? "border-green-300 bg-green-50 dark:bg-emerald-500/10 text-green-700 dark:text-emerald-400"
                : validate.data && !validate.data.valid
                  ? "border-red-300 bg-red-50 dark:bg-rose-500/10 text-red-700 dark:text-rose-400"
                  : "border-line text-fgmuted hover:bg-base",
              "disabled:opacity-50"
            )}
          >
            {validate.isPending ? (
              <>
                <span className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-current border-t-transparent" />
                Testing...
              </>
            ) : validate.data?.valid ? (
              <>&#10003; Connected</>
            ) : validate.data ? (
              <>&#10007; {validate.data.error}</>
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

  if (isLoading) {
    return <p className="text-sm text-fgsubtle">Loading tools...</p>;
  }

  const tools = data?.tools ?? [];

  if (tools.length === 0) {
    return (
      <p className="text-sm text-fgmuted">
        No tools available. Check your tool registry configuration.
      </p>
    );
  }

  return (
    <div>
      <p className="mb-4 text-sm text-fgmuted">
        Install or remove AI coding tools. Launch them from the Workbench.
      </p>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {tools.map((tool) => (
          <ToolCard key={tool.info.id} tool={tool} />
        ))}
      </div>
    </div>
  );
}

/* ─── Pegasus Options Tab ─── */

function PegasusOptionsTab() {
  const [executionSite, setExecutionSite] = useState("local");
  const [planningFlags, setPlanningFlags] = useState({
    force: false,
    submit: true,
    cleanup: "none" as string,
  });
  const [containerRegistry, setContainerRegistry] = useState("docker:///kthare10/");
  const [pegasusPath, setPegasusPath] = useState("");
  const [saved, setSaved] = useState(false);

  const handleSave = () => {
    // TODO: persist via API when backend endpoint is available
    setSaved(true);
    setTimeout(() => setSaved(false), 3000);
  };

  return (
    <div className="space-y-6">
      <p className="text-sm text-fgmuted">
        Configure Pegasus WMS execution defaults for workflow planning and
        submission.
      </p>

      {/* Execution site */}
      <div>
        <label className="block text-sm font-medium text-fg">
          Execution Site
        </label>
        <select
          value={executionSite}
          onChange={(e) => setExecutionSite(e.target.value)}
          className="mt-1 block w-full rounded-md border border-line px-3 py-2 text-sm shadow-sm focus:border-pegasus-500 focus:ring-pegasus-500"
        >
          <option value="local">local</option>
          <option value="condorpool">condorpool</option>
          <option value="slurm">slurm</option>
          <option value="sge">sge</option>
        </select>
        <p className="mt-1 text-xs text-fgsubtle">
          Target execution site for pegasus-plan --sites
        </p>
      </div>

      {/* Planning options */}
      <div>
        <label className="block text-sm font-medium text-fg">
          Default Planning Options
        </label>
        <div className="mt-2 space-y-2">
          <label className="flex items-center gap-2 text-sm text-fgmuted">
            <input
              type="checkbox"
              checked={planningFlags.force}
              onChange={(e) =>
                setPlanningFlags((f) => ({ ...f, force: e.target.checked }))
              }
              className="rounded border-line text-pegasus-600 focus:ring-pegasus-500"
            />
            --force (overwrite existing run directory)
          </label>
          <label className="flex items-center gap-2 text-sm text-fgmuted">
            <input
              type="checkbox"
              checked={planningFlags.submit}
              onChange={(e) =>
                setPlanningFlags((f) => ({ ...f, submit: e.target.checked }))
              }
              className="rounded border-line text-pegasus-600 focus:ring-pegasus-500"
            />
            --submit (automatically submit after planning)
          </label>
          <div className="flex items-center gap-2">
            <span className="text-sm text-fgmuted">--cleanup</span>
            <select
              value={planningFlags.cleanup}
              onChange={(e) =>
                setPlanningFlags((f) => ({ ...f, cleanup: e.target.value }))
              }
              className="rounded-md border border-line px-2 py-1 text-sm focus:border-pegasus-500 focus:ring-pegasus-500"
            >
              <option value="none">none</option>
              <option value="leaf">leaf</option>
              <option value="inplace">inplace</option>
            </select>
          </div>
        </div>
      </div>

      {/* Container registry */}
      <div>
        <label className="block text-sm font-medium text-fg">
          Container Registry Prefix
        </label>
        <input
          type="text"
          value={containerRegistry}
          onChange={(e) => setContainerRegistry(e.target.value)}
          placeholder="docker:///username/"
          className="mt-1 block w-full rounded-md border border-line px-3 py-2 text-sm shadow-sm focus:border-pegasus-500 focus:ring-pegasus-500"
        />
        <p className="mt-1 text-xs text-fgsubtle">
          Prefix used for container transformation images (e.g.
          docker:///kthare10/)
        </p>
      </div>

      {/* Pegasus install path */}
      <div>
        <label className="block text-sm font-medium text-fg">
          Pegasus Installation Path
        </label>
        <input
          type="text"
          value={pegasusPath}
          onChange={(e) => setPegasusPath(e.target.value)}
          placeholder="/usr/bin (auto-detected if empty)"
          className="mt-1 block w-full rounded-md border border-line px-3 py-2 text-sm shadow-sm focus:border-pegasus-500 focus:ring-pegasus-500"
        />
        <p className="mt-1 text-xs text-fgsubtle">
          Leave empty to use Pegasus from PATH
        </p>
      </div>

      {/* Save */}
      <div className="flex items-center gap-3 pt-2">
        <button
          onClick={handleSave}
          className="rounded-md bg-pegasus-600 px-4 py-2 text-sm text-white hover:bg-pegasus-700"
        >
          Save Options
        </button>
        {saved && <span className="text-sm text-green-600">Saved</span>}
      </div>
    </div>
  );
}
